"""Curva americana a partir do Departamento do Tesouro dos EUA.

Duas curvas: a *par yield curve* nominal e a *real yield curve* (TIPS). Ambas
vêm em CSV público, um arquivo por ano, sem chave de acesso. A diferença entre
as duas é a inflação implícita (breakeven) usada na página de comparação.
"""

from __future__ import annotations

import datetime as dt
import io

import pandas as pd

from tesouraria.sources.base import Source


def _normalizar_tenor(rotulo: str) -> str:
    """Uniformiza rótulos: o arquivo real usa '5 YR' e o nominal, '5 Yr'."""
    texto = rotulo.strip().upper().replace("MONTH", "MO").replace("MONTHS", "MO")
    texto = texto.replace("YEAR", "YR").replace("YEARS", "YR")
    return " ".join(texto.split())


class USTreasurySource(Source):
    name = "us_treasury"
    table = "curve_us"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        anos = self._anos(since)
        quadros: list[pd.DataFrame] = []

        for tipo, chave_api in cfg["tipos"].items():
            fixture = cfg.get("fixtures", {}).get(tipo)
            if self.offline:
                quadros.append(self.parse(self.get("", fixture=fixture), tipo=tipo))
                continue
            for ano in anos:
                raw = self.get(
                    cfg["url_base"] + f"/{ano}/all",
                    fixture=fixture,
                    params={
                        "type": chave_api,
                        "field_tdr_date_value": str(ano),
                        "page": "",
                        "_format": "csv",
                    },
                )
                quadros.append(self.parse(raw, tipo=tipo))

        if not quadros:
            return pd.DataFrame()

        frame = pd.concat(quadros, ignore_index=True)
        if since is not None:
            frame = frame[frame["data_ref"] >= since]
        return frame.drop_duplicates(subset=["data_ref", "tipo", "tenor"]).reset_index(drop=True)

    def parse(self, raw: bytes, tipo: str = "nominal") -> pd.DataFrame:
        cfg = self.config
        tenores = {_normalizar_tenor(k): v for k, v in cfg["tenores"].items()}

        df = pd.read_csv(io.BytesIO(raw))
        df.columns = [c.strip() for c in df.columns]
        if "Date" not in df.columns:
            raise ValueError("CSV do Treasury sem coluna 'Date'")

        longo = df.melt(id_vars="Date", var_name="tenor", value_name="taxa")
        longo["tenor_norm"] = longo["tenor"].map(_normalizar_tenor)
        longo["prazo_anos"] = longo["tenor_norm"].map(tenores)

        # Rótulo desconhecido (o Treasury já acrescentou vértices novos) é
        # descartado em vez de virar prazo nulo e contaminar a interpolação.
        longo = longo.dropna(subset=["prazo_anos"])

        longo["taxa"] = pd.to_numeric(longo["taxa"], errors="coerce")
        longo = longo.dropna(subset=["taxa"])

        out = pd.DataFrame(
            {
                "data_ref": pd.to_datetime(
                    longo["Date"], format="%m/%d/%Y", errors="coerce"
                ).dt.date,
                "tipo": tipo,
                "tenor": longo["tenor_norm"],
                "prazo_anos": longo["prazo_anos"].astype(float),
                "taxa": longo["taxa"].astype(float),
            }
        )
        out = out.dropna(subset=["data_ref"])
        return out.sort_values(["data_ref", "prazo_anos"]).reset_index(drop=True)

    @staticmethod
    def _anos(since: dt.date | None) -> list[int]:
        fim = dt.date.today().year
        inicio = since.year if since else fim
        return list(range(inicio, fim + 1))
