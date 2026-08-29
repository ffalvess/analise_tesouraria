"""Séries do SGS (Sistema Gerenciador de Séries Temporais) do Banco Central.

Cobre o vértice curto da curva (Selic, CDI), o câmbio PTAX, inflação,
atividade, desemprego e a balança comercial agregada. Todas as séries caem na
tabela alta `series_macro`, identificadas pelo código do SGS.

A API recusa janelas muito longas em séries diárias, então a coleta é fatiada
em blocos de anos definidos em `config/sources.yaml`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.sources.base import Source

logger = logging.getLogger(__name__)

INICIO_PADRAO = dt.date(2010, 1, 1)


def parse_sgs(payload: list[dict], serie_id: str) -> pd.DataFrame:
    """Converte a resposta do SGS (lista de {data, valor}) em quadro longo."""
    if not payload:
        return pd.DataFrame(columns=["serie_id", "data_ref", "valor"])

    df = pd.DataFrame(payload)
    if "data" not in df.columns or "valor" not in df.columns:
        raise ValueError(f"resposta do SGS em formato inesperado para a série {serie_id}")

    out = pd.DataFrame(
        {
            "serie_id": str(serie_id),
            "data_ref": pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce").dt.date,
            "valor": pd.to_numeric(df["valor"], errors="coerce"),
        }
    )
    return out.dropna(subset=["data_ref"]).reset_index(drop=True)


class BcbSgsSource(Source):
    name = "bcb_sgs"
    table = "series_macro"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        inicio = since or INICIO_PADRAO
        fim = dt.date.today()

        fixture_payload = self._fixture_payload() if self.offline else None
        quadros: list[pd.DataFrame] = []

        for serie in cfg.get("series", []):
            codigo = str(serie["codigo"])
            try:
                if fixture_payload is not None:
                    dados = fixture_payload.get(codigo, [])
                else:
                    dados = self._fetch_serie(cfg, codigo, inicio, fim)
                parcial = parse_sgs(dados, codigo)
            except Exception as exc:  # noqa: BLE001 — série ruim não invalida o lote
                logger.warning("SGS %s (%s) falhou: %s", codigo, serie.get("nome"), exc)
                continue

            if parcial.empty:
                continue

            parcial["pais"] = serie.get("pais", "BR")
            parcial["fonte"] = "bcb_sgs"
            parcial["nome"] = serie.get("nome", f"SGS {codigo}")
            parcial["unidade"] = serie.get("unidade")
            quadros.append(parcial)

        if not quadros:
            return pd.DataFrame()
        return pd.concat(quadros, ignore_index=True)

    def _fixture_payload(self) -> dict[str, list[dict]]:
        """A fixture agrupa várias séries num só arquivo, indexadas pelo código."""
        return json.loads(self.fixture(self.config["fixture"]).decode("utf-8"))

    def _fetch_serie(
        self, cfg: dict, codigo: str, inicio: dt.date, fim: dt.date
    ) -> list[dict]:
        url = cfg["url_template"].format(codigo=codigo)
        passo = int(cfg.get("anos_por_requisicao", 10))
        dados: list[dict] = []

        janela_inicio = inicio
        while janela_inicio <= fim:
            janela_fim = min(dt.date(janela_inicio.year + passo, 1, 1), fim)
            bruto = self.get(
                url,
                params={
                    "formato": "json",
                    "dataInicial": janela_inicio.strftime("%d/%m/%Y"),
                    "dataFinal": janela_fim.strftime("%d/%m/%Y"),
                },
            )
            try:
                dados.extend(json.loads(bruto.decode("utf-8")))
            except json.JSONDecodeError:
                # O SGS devolve HTML de erro quando a série não existe ou a
                # janela é inválida; tratar como bloco vazio e seguir.
                logger.warning("SGS %s: resposta não-JSON entre %s e %s", codigo,
                               janela_inicio, janela_fim)
            if janela_fim >= fim:
                break
            janela_inicio = janela_fim + dt.timedelta(days=1)

        return dados
