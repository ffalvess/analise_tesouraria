"""Exportação mensal por produto (SH4) no Comex Stat — base dos pesos da cesta.

Os pesos da cesta de commodities Brasil são a participação de cada produto no
valor exportado dos últimos 12 meses. Esta fonte guarda o valor FOB mensal por
código SH4 em `series_macro` (`comex_sh4_XXXX`); o cálculo dos pesos acontece na
leitura (`analytics/commodities.py`), sempre sobre os 12 últimos meses
completos. Assim os pesos e a data-base viajam junto com os snapshots e se
atualizam sozinhos a cada coleta — nada a digitar.

Os códigos vêm de `config/cambio.yaml` (`commodities.produtos.*.sh4`),
inclusive os dos produtos desligados, para que ligar um opcional não exija
nova coleta.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.analytics.commodities import serie_id_sh4
from tesouraria.settings import load_config
from tesouraria.sources.base import Source
from tesouraria.sources.validation import aceitar_serie

logger = logging.getLogger(__name__)

# 18 meses: os 12 da janela mais a defasagem de publicação do Comex.
MESES_COLETA = 18


def codigos_sh4() -> list[str]:
    produtos = load_config("cambio").get("commodities", {}).get("produtos", {})
    return sorted({str(c).zfill(4) for p in produtos.values() for c in p.get("sh4", [])})


class ComexPesosSource(Source):
    name = "comex_pesos"
    table = "series_macro"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        codigos = codigos_sh4()
        fim = dt.date.today().replace(day=1)
        inicio = (pd.Timestamp(fim) - pd.DateOffset(months=MESES_COLETA)).date()

        if self.offline:
            payload = json.loads(self.fixture(cfg["fixture"]).decode("utf-8"))
        else:
            payload = json.loads(
                self.get(
                    cfg["url"],
                    method="POST",
                    data=json.dumps(self.consulta(codigos, inicio, fim)),
                    headers={"Content-Type": "application/json"},
                    ttl_hours=cfg.get("ttl_horas"),
                ).decode("utf-8")
            )

        frame = self.parse(payload)
        if frame.empty:
            return frame
        frame = frame[frame["serie_id"].isin({serie_id_sh4(c) for c in codigos})]
        if not aceitar_serie(frame, cfg, "Comex Stat SH4"):
            return pd.DataFrame()
        frame["pais"] = "BR"
        frame["fonte"] = "comex"
        frame["nome"] = "Exportação SH4 " + frame["serie_id"].str[-4:] + " (Comex Stat)"
        frame["unidade"] = "US$ FOB"
        return frame

    @staticmethod
    def consulta(codigos: list[str], inicio: dt.date, fim: dt.date) -> dict:
        return {
            "flow": "export",
            "monthDetail": True,
            "period": {"from": inicio.strftime("%Y-%m"), "to": fim.strftime("%Y-%m")},
            "filters": [{"filter": "heading", "values": codigos}],
            "details": ["heading"],
            "metrics": ["metricFOB"],
        }

    @staticmethod
    def parse(payload: dict) -> pd.DataFrame:
        lista = payload.get("data", {}).get("list", []) if isinstance(payload, dict) else []
        if not lista:
            return pd.DataFrame(columns=["serie_id", "data_ref", "valor"])

        df = pd.DataFrame(lista)
        col_codigo = next((c for c in ("headingCode", "coSh4", "heading") if c in df.columns), None)
        col_mes = next((c for c in ("monthNumber", "month", "coMes") if c in df.columns), None)
        col_ano = next((c for c in ("year", "coAno") if c in df.columns), None)
        if not (col_codigo and col_mes and col_ano and "metricFOB" in df.columns):
            raise ValueError(f"Comex Stat (SH4) com colunas inesperadas: {sorted(df.columns)}")

        codigo = df[col_codigo].astype(str).str.extract(r"(\d{1,4})")[0].str.zfill(4)
        mes = df[col_mes].astype(str).str.extract(r"(\d{1,2})")[0].str.zfill(2)
        out = pd.DataFrame(
            {
                "serie_id": codigo.map(serie_id_sh4),
                "data_ref": pd.to_datetime(
                    df[col_ano].astype(str) + "-" + mes + "-01", errors="coerce"
                ).dt.date,
                "valor": pd.to_numeric(df["metricFOB"], errors="coerce"),
            }
        )
        out = out.dropna(subset=["data_ref", "valor", "serie_id"])
        return (
            out.groupby(["serie_id", "data_ref"], as_index=False)["valor"].sum()
            .sort_values(["serie_id", "data_ref"])
            .reset_index(drop=True)
        )
