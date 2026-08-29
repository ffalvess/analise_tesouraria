"""Balança comercial detalhada pelo Comex Stat (MDIC).

Complementa as séries agregadas de exportação, importação e saldo que já vêm
do SGS: aqui entra o valor mensal por fluxo, permitindo ver a corrente de
comércio e o ritmo de acumulação do superávit — que é a origem econômica do
fluxo cambial comercial.

Se esta fonte falhar, a página de balança comercial continua funcionando com
as séries 2255/2256/2257 do SGS; a falha fica registrada em `ingest_log`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.sources.base import Source

logger = logging.getLogger(__name__)

INICIO_PADRAO = dt.date(2010, 1, 1)
FLUXOS = {"export": "Exportação (Comex Stat)", "import": "Importação (Comex Stat)"}


class ComexSource(Source):
    name = "comex"
    table = "series_macro"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        inicio = since or INICIO_PADRAO
        fim = dt.date.today()

        fixture_payload = None
        if self.offline:
            fixture_payload = json.loads(self.fixture(cfg["fixture"]).decode("utf-8"))

        quadros: list[pd.DataFrame] = []
        for fluxo, nome in FLUXOS.items():
            try:
                if fixture_payload is not None:
                    payload = fixture_payload.get(fluxo, {})
                else:
                    payload = json.loads(
                        self.get(
                            cfg["url"],
                            method=cfg.get("metodo", "POST"),
                            data=json.dumps(self._consulta(fluxo, inicio, fim)),
                            headers={"Content-Type": "application/json"},
                        ).decode("utf-8")
                    )
                parcial = self.parse(payload, fluxo)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Comex Stat (%s) falhou: %s", fluxo, exc)
                continue

            if parcial.empty:
                continue
            parcial["pais"] = "BR"
            parcial["fonte"] = "comex"
            parcial["nome"] = nome
            parcial["unidade"] = "US$ FOB"
            quadros.append(parcial)

        if not quadros:
            return pd.DataFrame()

        frame = pd.concat(quadros, ignore_index=True)
        return self._acrescentar_saldo(frame)

    @staticmethod
    def _consulta(fluxo: str, inicio: dt.date, fim: dt.date) -> dict:
        return {
            "flow": fluxo,
            "monthDetail": True,
            "period": {"from": inicio.strftime("%Y-%m"), "to": fim.strftime("%Y-%m")},
            "filters": [],
            "details": ["year", "month"],
            "metrics": ["metricFOB"],
        }

    @staticmethod
    def parse(payload: dict, fluxo: str) -> pd.DataFrame:
        lista = payload.get("data", {}).get("list", []) if isinstance(payload, dict) else []
        if not lista:
            return pd.DataFrame(columns=["serie_id", "data_ref", "valor"])

        df = pd.DataFrame(lista)
        faltando = {"year", "month", "metricFOB"} - set(df.columns)
        if faltando:
            raise ValueError(f"Comex Stat sem as colunas {sorted(faltando)}")

        out = pd.DataFrame(
            {
                "serie_id": f"comex_{fluxo}",
                "data_ref": pd.to_datetime(
                    df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2) + "-01",
                    errors="coerce",
                ).dt.date,
                "valor": pd.to_numeric(df["metricFOB"], errors="coerce"),
            }
        )
        return out.dropna(subset=["data_ref"]).sort_values("data_ref").reset_index(drop=True)

    @staticmethod
    def _acrescentar_saldo(frame: pd.DataFrame) -> pd.DataFrame:
        """Deriva o saldo mensal, presente as duas pernas."""
        largo = frame.pivot_table(
            index="data_ref", columns="serie_id", values="valor", aggfunc="sum"
        )
        if not {"comex_export", "comex_import"}.issubset(largo.columns):
            return frame

        saldo = (largo["comex_export"] - largo["comex_import"]).dropna().reset_index()
        saldo.columns = ["data_ref", "valor"]
        saldo["serie_id"] = "comex_saldo"
        saldo["pais"] = "BR"
        saldo["fonte"] = "comex"
        saldo["nome"] = "Saldo comercial (Comex Stat)"
        saldo["unidade"] = "US$ FOB"
        return pd.concat([frame, saldo], ignore_index=True)
