"""Curva brasileira a partir do Tesouro Direto (Tesouro Transparente).

Fonte primária da curva BR: CSV público, sem chave, com histórico longo de
taxa e preço unitário por título e vencimento. Os prefixados (LTN e NTN-F)
formam a curva nominal; as NTN-B formam a curva real (IPCA+).

A taxa usada é a de venda da manhã — a que o investidor efetivamente recebe ao
comprar o título — e é essa a convenção exibida na interface.
"""

from __future__ import annotations

import datetime as dt
import io

import pandas as pd

from tesouraria.sources.base import Source, business_days, year_fraction

COLUNA_TAXA = "Taxa Venda Manha"
COLUNA_PRECO = "PU Venda Manha"
COLUNA_TAXA_ALT = "Taxa Compra Manha"
COLUNA_PRECO_ALT = "PU Compra Manha"


class TesouroDiretoSource(Source):
    name = "tesouro_direto"
    table = "curve_br"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        raw = self.get(cfg["url"], fixture=cfg.get("fixture"))
        frame = self.parse(raw)
        if since is not None:
            frame = frame[frame["data_ref"] >= since]
        return frame

    def parse(self, raw: bytes) -> pd.DataFrame:
        cfg = self.config
        df = pd.read_csv(
            io.BytesIO(raw),
            sep=cfg.get("separador", ";"),
            decimal=cfg.get("decimal", ","),
            thousands=".",
            encoding=cfg.get("encoding", "latin-1"),
        )
        df.columns = [c.strip() for c in df.columns]

        taxa_col = COLUNA_TAXA if COLUNA_TAXA in df.columns else COLUNA_TAXA_ALT
        preco_col = COLUNA_PRECO if COLUNA_PRECO in df.columns else COLUNA_PRECO_ALT

        out = pd.DataFrame(
            {
                "data_ref": pd.to_datetime(df["Data Base"], dayfirst=True, errors="coerce"),
                "vencimento": pd.to_datetime(
                    df["Data Vencimento"], dayfirst=True, errors="coerce"
                ),
                "instrumento": df["Tipo Titulo"].astype(str).str.strip(),
                "taxa": pd.to_numeric(df[taxa_col], errors="coerce"),
                "preco": pd.to_numeric(df[preco_col], errors="coerce"),
            }
        )

        tipos = cfg.get("tipos", {})
        out["tipo"] = out["instrumento"].map(tipos)
        out = out[out["tipo"].isin(cfg.get("tipos_curva", ["pre", "ipca"]))]

        out = out.dropna(subset=["data_ref", "vencimento", "taxa"])
        # Títulos já vencidos na data de referência não pertencem à curva.
        out = out[out["vencimento"] > out["data_ref"]]

        out["prazo_anos"] = year_fraction(out["data_ref"], out["vencimento"])
        out["prazo_du"] = business_days(out["data_ref"], out["vencimento"])
        out["fonte"] = "tesouro"

        # O mesmo tipo de título aparece com vários vencimentos; a chave inclui
        # o vencimento, então distinguir instrumentos homônimos basta.
        out["instrumento"] = (
            out["instrumento"] + " " + pd.to_datetime(out["vencimento"]).dt.strftime("%Y-%m-%d")
        )

        out["data_ref"] = out["data_ref"].dt.date
        out["vencimento"] = out["vencimento"].dt.date

        return out.sort_values(["data_ref", "tipo", "prazo_anos"]).reset_index(drop=True)
