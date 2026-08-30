"""Inflação, desemprego e atividade pelo SIDRA/IBGE.

O SGS do Banco Central já traz várias dessas séries, mas o SIDRA é a fonte
primária e chega antes; tê-las das duas origens permite conferir uma contra a
outra quando um número surpreende.

A resposta do SIDRA é uma lista em que o primeiro elemento é o dicionário de
rótulos e os demais são as observações, com chaves posicionais (`D1C`, `D2C`,
`D3C`...) que mudam de tabela para tabela. O parser descobre pelo dicionário de
rótulos qual chave carrega o período, em vez de fixar a posição.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.sources.base import Source
from tesouraria.sources.validation import aceitar_serie

logger = logging.getLogger(__name__)

ROTULOS_PERIODO = ("MÊS", "MES", "TRIMESTRE", "ANO", "PERÍODO", "PERIODO")


def _chave_periodo(rotulos: dict[str, str]) -> str | None:
    for chave, rotulo in rotulos.items():
        texto = str(rotulo).upper()
        e_codigo = "(CÓDIGO)" in texto or "(CODIGO)" in texto
        if e_codigo and any(alvo in texto for alvo in ROTULOS_PERIODO):
            return chave
    return None


def _para_data(codigo: str) -> dt.date | None:
    """'202601' -> 2026-01-01; '2026' -> 2026-01-01."""
    texto = str(codigo).strip()
    try:
        if len(texto) == 6:
            return dt.date(int(texto[:4]), int(texto[4:]), 1)
        if len(texto) == 4:
            return dt.date(int(texto), 1, 1)
    except ValueError:
        return None
    return None


class IbgeSidraSource(Source):
    name = "ibge_sidra"
    table = "series_macro"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config

        fixture_payload = None
        if self.offline:
            fixture_payload = json.loads(self.fixture(cfg["fixture"]).decode("utf-8"))

        quadros: list[pd.DataFrame] = []
        for serie in cfg.get("series", []):
            serie_id = f"{serie['tabela']}-{serie['variavel']}"
            try:
                if fixture_payload is not None:
                    payload = fixture_payload.get(serie_id, [])
                else:
                    url = cfg["url_template"].format(
                        tabela=serie["tabela"], variavel=serie["variavel"]
                    )
                    payload = json.loads(self.get(url).decode("utf-8"))
                parcial = self.parse(payload, serie_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SIDRA %s falhou: %s", serie_id, exc)
                continue

            if parcial.empty or not aceitar_serie(parcial, serie, f"SIDRA {serie_id}"):
                continue
            parcial["pais"] = "BR"
            parcial["fonte"] = "ibge_sidra"
            parcial["nome"] = serie.get("nome", serie_id)
            parcial["unidade"] = serie.get("unidade")
            quadros.append(parcial)

        if not quadros:
            return pd.DataFrame()

        frame = pd.concat(quadros, ignore_index=True)
        if since is not None:
            frame = frame[frame["data_ref"] >= since]
        return frame

    @staticmethod
    def parse(payload: list[dict], serie_id: str) -> pd.DataFrame:
        if not payload or len(payload) < 2:
            return pd.DataFrame(columns=["serie_id", "data_ref", "valor"])

        rotulos, observacoes = payload[0], payload[1:]
        chave = _chave_periodo(rotulos)
        if chave is None:
            raise ValueError(f"SIDRA {serie_id}: coluna de período não identificada")

        df = pd.DataFrame(observacoes)
        out = pd.DataFrame(
            {
                "serie_id": serie_id,
                "data_ref": df[chave].map(_para_data),
                # O SIDRA usa '...' e '-' para dado indisponível.
                "valor": pd.to_numeric(df.get("V"), errors="coerce"),
            }
        )
        return out.dropna(subset=["data_ref"]).reset_index(drop=True)
