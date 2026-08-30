"""Macro dos Estados Unidos pelo FRED (St. Louis Fed).

Inflação, desemprego, payroll, Fed Funds e o índice do dólar — o contexto que
move a ponta americana da curva. É a única fonte do projeto que exige chave; a
do FRED é gratuita e sai em minutos em https://fred.stlouisfed.org/docs/api/api_key.html

Sem `FRED_API_KEY` configurada, a fonte é registrada como `pulado` em
`ingest_log` e o resto da ingestão segue normalmente.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.settings import get_settings
from tesouraria.sources.base import Source
from tesouraria.sources.validation import aceitar_serie

logger = logging.getLogger(__name__)

INICIO_PADRAO = dt.date(2010, 1, 1)


class UsMacroSource(Source):
    name = "us_macro"
    table = "series_macro"

    def skip_reason(self) -> str | None:
        if self.offline:
            return None
        if not get_settings().fred_api_key:
            return "FRED_API_KEY não configurada; defina-a no .env para coletar as séries dos EUA"
        return None

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        inicio = since or INICIO_PADRAO

        fixture_payload = None
        if self.offline:
            fixture_payload = json.loads(self.fixture(cfg["fixture"]).decode("utf-8"))

        quadros: list[pd.DataFrame] = []
        for serie in cfg.get("series", []):
            serie_id = serie["serie_id"]
            try:
                if fixture_payload is not None:
                    payload = fixture_payload.get(serie_id, {})
                else:
                    payload = json.loads(
                        self.get(
                            cfg["url"],
                            params={
                                "series_id": serie_id,
                                "api_key": get_settings().fred_api_key,
                                "file_type": "json",
                                "observation_start": inicio.isoformat(),
                            },
                        ).decode("utf-8")
                    )
                parcial = self.parse(payload, serie_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("FRED %s falhou: %s", serie_id, exc)
                continue

            if parcial.empty or not aceitar_serie(parcial, serie, f"FRED {serie_id}"):
                continue
            parcial["pais"] = "US"
            parcial["fonte"] = "fred"
            parcial["nome"] = serie.get("nome", serie_id)
            parcial["unidade"] = serie.get("unidade")
            quadros.append(parcial)

        if not quadros:
            return pd.DataFrame()
        return pd.concat(quadros, ignore_index=True)

    @staticmethod
    def parse(payload: dict, serie_id: str) -> pd.DataFrame:
        observacoes = payload.get("observations", [])
        if not observacoes:
            return pd.DataFrame(columns=["serie_id", "data_ref", "valor"])

        df = pd.DataFrame(observacoes)
        out = pd.DataFrame(
            {
                "serie_id": serie_id,
                "data_ref": pd.to_datetime(df["date"], errors="coerce").dt.date,
                # O FRED marca dado ausente com '.'.
                "valor": pd.to_numeric(df["value"], errors="coerce"),
            }
        )
        return out.dropna(subset=["data_ref"]).reset_index(drop=True)
