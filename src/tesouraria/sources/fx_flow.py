"""Fluxo cambial contratado — o movimento de entrada e saída de moeda estrangeira.

O Banco Central publica o movimento de câmbio separado em duas pernas:

* **comercial** — liquidações de exportação e importação;
* **financeiro** — investimentos, empréstimos, remessas de lucros e afins.

O saldo entre compras e vendas é a variável que a página de fluxo cambial
confronta com a cotação do dólar: semanas de saldo negativo (mais dólares
saindo do que entrando) tendem a coincidir com desvalorização do real, e é
justamente a força e a estabilidade dessa relação que a regressão mede.

As séries chegam do SGS como métricas separadas e são pivotadas aqui para uma
linha por data e segmento.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.sources.base import Source
from tesouraria.sources.bcb_sgs import INICIO_PADRAO, parse_sgs

logger = logging.getLogger(__name__)


class FxFlowSource(Source):
    name = "fx_flow"
    table = "fx_flow"

    def skip_reason(self) -> str | None:
        """Sem códigos confirmados, não coletar é melhor que coletar errado.

        Os códigos originais (22707–22715) eram um palpite, e a primeira coleta
        real provou que traziam outra coisa: `comercial` e `financeiro` vinham
        com correlação 1,000000 — a mesma série — e `total` era mil vezes menor
        e negativo em todas as 139 observações. A página exibia isso como
        "fluxo cambial", que é pior do que não exibir nada.
        """
        if not self.config.get("series"):
            return (
                "códigos do SGS não confirmados; veja a nota em config/sources.yaml "
                "e use o workflow 'Sondar séries do SGS' para identificá-los"
            )
        return None

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        inicio = since or INICIO_PADRAO
        fim = dt.date.today()

        fixture_payload = None
        if self.offline:
            fixture_payload = json.loads(self.fixture(cfg["fixture"]).decode("utf-8"))

        registros: list[pd.DataFrame] = []
        for serie in cfg.get("series", []):
            codigo = str(serie["codigo"])
            try:
                if fixture_payload is not None:
                    dados = fixture_payload.get(codigo, [])
                else:
                    dados = json.loads(
                        self.get(
                            cfg["url_template"].format(codigo=codigo),
                            params={
                                "formato": "json",
                                "dataInicial": inicio.strftime("%d/%m/%Y"),
                                "dataFinal": fim.strftime("%d/%m/%Y"),
                            },
                        ).decode("utf-8")
                    )
                parcial = parse_sgs(dados, codigo)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fluxo cambial: série %s falhou: %s", codigo, exc)
                continue

            if parcial.empty:
                continue
            parcial["segmento"] = serie["segmento"]
            parcial["medida"] = serie["medida"]
            registros.append(parcial)

        if not registros:
            return pd.DataFrame()

        return self.pivot(
            pd.concat(registros, ignore_index=True),
            periodicidade=cfg.get("periodicidade", "semanal"),
        )

    @staticmethod
    def pivot(longo: pd.DataFrame, periodicidade: str = "semanal") -> pd.DataFrame:
        """De (data, segmento, medida, valor) para uma linha por data/segmento."""
        largo = (
            longo.pivot_table(
                index=["data_ref", "segmento"],
                columns="medida",
                values="valor",
                aggfunc="last",
            )
            .reset_index()
            .rename_axis(None, axis=1)
        )

        for coluna in ("compras", "vendas", "saldo"):
            if coluna not in largo.columns:
                largo[coluna] = pd.NA

        # Quando o BCB publica só as pernas, o saldo se deduz delas.
        falta_saldo = largo["saldo"].isna() & largo["compras"].notna() & largo["vendas"].notna()
        largo.loc[falta_saldo, "saldo"] = (
            largo.loc[falta_saldo, "compras"] - largo.loc[falta_saldo, "vendas"]
        )

        largo["periodicidade"] = periodicidade
        return largo[
            ["data_ref", "periodicidade", "segmento", "compras", "vendas", "saldo"]
        ].sort_values(["data_ref", "segmento"]).reset_index(drop=True)
