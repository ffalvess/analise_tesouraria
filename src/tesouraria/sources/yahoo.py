"""Cotações diárias do Yahoo Finance: câmbio, DXY, VIX e futuros de commodities.

Fonte **não oficial**. Usa o endpoint público de gráfico
(`/v8/finance/chart/{ticker}`) pelo cliente HTTP do projeto — com o mesmo
cache em disco, retry e modo offline das outras fontes — em vez do pacote
`yfinance`, que faria a mesma chamada por baixo com uma dependência a mais.

O que entra é o fechamento diário (`close`) de cada pregão, datado no fuso da
própria bolsa (`meta.exchangeTimezoneName`): o candle de 15/3 em Chicago é de
15/3, mesmo que o timestamp UTC caia em outro dia.

Uma série que falha não derruba as outras; o erro de cada ticker fica no log e,
se nenhum entrar, a fonte inteira sai como `erro` em `ingest_log`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.sources.base import Source
from tesouraria.sources.validation import aceitar_serie

logger = logging.getLogger(__name__)

INICIO_PADRAO = dt.date(2015, 1, 1)


class YahooSource(Source):
    name = "yahoo"
    table = "series_macro"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        inicio = since or INICIO_PADRAO
        # Folga para trás: o primeiro retorno do período precisa do fechamento anterior.
        inicio = inicio - dt.timedelta(days=7)
        fim = dt.date.today() + dt.timedelta(days=1)

        fixture_payload = None
        if self.offline:
            fixture_payload = json.loads(self.fixture(cfg["fixture"]).decode("utf-8"))

        quadros: list[pd.DataFrame] = []
        falhas: list[str] = []
        for serie in cfg.get("series", []):
            ticker = serie["ticker"]
            try:
                if fixture_payload is not None:
                    payload = fixture_payload.get(ticker, {})
                else:
                    payload = json.loads(
                        self.get(
                            cfg["url_template"].format(ticker=ticker),
                            params={
                                "period1": int(dt.datetime.combine(inicio, dt.time()).timestamp()),
                                "period2": int(dt.datetime.combine(fim, dt.time()).timestamp()),
                                "interval": "1d",
                                "includePrePost": "false",
                            },
                            headers={"User-Agent": cfg.get("user_agent", "Mozilla/5.0")},
                            ttl_hours=cfg.get("ttl_horas"),
                        ).decode("utf-8")
                    )
                parcial = self.parse(payload, ticker)
            except Exception as exc:  # noqa: BLE001 — um ticker não derruba os outros
                logger.warning("Yahoo %s falhou: %s", ticker, exc)
                falhas.append(f"{ticker}: {type(exc).__name__}")
                continue

            if parcial.empty or not aceitar_serie(parcial, serie, f"Yahoo {ticker}"):
                falhas.append(f"{ticker}: vazio ou fora da faixa")
                continue
            parcial["pais"] = "GLB"
            parcial["fonte"] = "yahoo"
            parcial["nome"] = serie.get("nome", ticker)
            parcial["unidade"] = serie.get("unidade")
            quadros.append(parcial)

        if falhas:
            logger.warning("Yahoo: %d ticker(s) sem dados — %s", len(falhas), "; ".join(falhas))
        if not quadros:
            raise RuntimeError("nenhum ticker do Yahoo retornou dados: " + "; ".join(falhas))
        return pd.concat(quadros, ignore_index=True)

    @staticmethod
    def parse(payload: dict, ticker: str) -> pd.DataFrame:
        """Payload do endpoint de gráfico → `serie_id, data_ref, valor`."""
        vazio = pd.DataFrame(columns=["serie_id", "data_ref", "valor"])
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            raise ValueError(f"Yahoo devolveu erro para {ticker}: {chart['error']}")
        resultados = chart.get("result") or []
        if not resultados:
            return vazio

        resultado = resultados[0]
        tempos = resultado.get("timestamp") or []
        cotacoes = (resultado.get("indicators") or {}).get("quote") or [{}]
        fechamentos = cotacoes[0].get("close") or []
        if not tempos or len(tempos) != len(fechamentos):
            return vazio

        fuso = (resultado.get("meta") or {}).get("exchangeTimezoneName") or "UTC"
        datas = pd.to_datetime(tempos, unit="s", utc=True).tz_convert(fuso).date
        out = pd.DataFrame(
            {
                "serie_id": ticker,
                "data_ref": datas,
                "valor": pd.to_numeric(pd.Series(fechamentos), errors="coerce"),
            }
        )
        out = out.dropna(subset=["valor"])
        out = out[out["valor"] > 0]
        # O último candle do dia pode vir repetido (barra diária + preço corrente).
        return out.drop_duplicates("data_ref", keep="last").sort_values("data_ref").reset_index(
            drop=True
        )
