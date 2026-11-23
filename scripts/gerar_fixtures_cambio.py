"""Gera as amostras offline da aba de câmbio: `yahoo.json` e `comex_pesos.json`.

Separado de `gerar_fixtures.py` para não mexer na sequência aleatória das
amostras que já existem (e nos testes que dependem delas).

Os dados são SINTÉTICOS. O USD/BRL é construído a partir de fatores com betas
conhecidos — o mesmo tipo de estrutura que a atribuição tenta recuperar — para
que a tela offline mostre algo com forma de mercado. Não são cotações reais.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "data" / "fixtures"

# Mesmo intervalo das demais amostras, para as séries se alinharem com a curva.
INICIO = dt.date(2024, 1, 2)
FIM = dt.date(2026, 8, 28)

rng = np.random.default_rng(20261123)
dias = pd.bdate_range(INICIO, FIM)
n = len(dias)

# ------------------------------------------------------------ fatores
dxy = rng.normal(0, 0.0045, n)
em_comum = 0.7 * dxy + rng.normal(0, 0.005, n)
cmd_comum = -0.4 * dxy + rng.normal(0, 0.009, n)

pares = {
    "MXN=X": (17.0, 1.0, 0.004, "Europe/London"),
    "CLP=X": (900.0, 0.9, 0.005, "Europe/London"),
    "COP=X": (4000.0, 1.0, 0.006, "Europe/London"),
    "ZAR=X": (18.5, 1.1, 0.006, "Europe/London"),
}
commodities = {
    "BZ=F": (80.0, 1.4, 0.015, "America/New_York"),
    "ZS=F": (1200.0, 0.9, 0.011, "America/Chicago"),
    "TIO=F": (110.0, 1.0, 0.014, "Asia/Singapore"),
    "ZC=F": (450.0, 0.8, 0.013, "America/Chicago"),
    "SB=F": (20.0, 0.7, 0.017, "America/New_York"),
    "KC=F": (220.0, 0.6, 0.019, "America/New_York"),
    "CT=F": (80.0, 0.7, 0.013, "America/New_York"),
    "LE=F": (180.0, 0.3, 0.008, "America/Chicago"),
}

retornos: dict[str, tuple[float, np.ndarray, str]] = {
    "DX-Y.NYB": (104.0, dxy, "America/New_York"),
}
for ticker, (nivel, beta, vol, fuso) in pares.items():
    retornos[ticker] = (nivel, beta * em_comum + rng.normal(0, vol, n), fuso)
for ticker, (nivel, beta, vol, fuso) in commodities.items():
    retornos[ticker] = (nivel, beta * cmd_comum + rng.normal(0, vol, n), fuso)

em_medio = np.mean([retornos[t][1] for t in pares], axis=0)
cmd_medio = np.mean([retornos[t][1] for t in ("BZ=F", "ZS=F", "TIO=F", "ZC=F")], axis=0)
# Betas "verdadeiros" do real; o choque idiossincrático é o componente Brasil.
brl = 0.00005 + 0.5 * dxy + 0.8 * em_medio - 0.25 * cmd_medio + rng.normal(0, 0.004, n)
retornos["BRL=X"] = (4.95, brl, "Europe/London")

vix = np.empty(n)
vix[0] = 15.0
for i in range(1, n):
    vix[i] = max(9.0, vix[i - 1] + 0.05 * (15 - vix[i - 1]) + 40 * dxy[i] + rng.normal(0, 0.8))


def niveis(inicial: float, ret: np.ndarray, phi: float = 0.99) -> np.ndarray:
    """Nível com leve reversão à média no log, para não derivar para fora da faixa."""
    desvio = np.empty(n)
    acumulado = 0.0
    for i, r in enumerate(ret):
        acumulado = phi * acumulado + r
        desvio[i] = acumulado
    return inicial * np.exp(desvio)


def grafico(ticker: str, valores: np.ndarray, fuso: str, ausentes: float = 0.0) -> dict:
    tz = ZoneInfo(fuso)
    tempos, fechamentos = [], []
    for dia, valor in zip(dias, valores, strict=True):
        if ausentes and rng.random() < ausentes:
            continue  # pregão faltando, como num feriado local
        momento = dt.datetime.combine(dia.date(), dt.time(9, 30), tzinfo=tz)
        tempos.append(int(momento.timestamp()))
        fechamentos.append(round(float(valor), 6))
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": ticker, "exchangeTimezoneName": fuso, "currency": "USD"},
                    "timestamp": tempos,
                    "indicators": {"quote": [{"close": fechamentos}]},
                }
            ],
            "error": None,
        }
    }


payload = {}
for ticker, (nivel, ret, fuso) in retornos.items():
    payload[ticker] = grafico(ticker, niveis(nivel, ret), fuso, ausentes=0.02)
payload["^VIX"] = grafico("^VIX", vix, "America/Chicago")

(DESTINO / "yahoo.json").write_text(json.dumps(payload), encoding="utf-8")
print(f"yahoo.json: {len(payload)} tickers x {n} pregões")

# ------------------------------------------------ Comex Stat por SH4
meses = pd.period_range("2025-02", "2026-07", freq="M")
base_bi = {  # US$ bilhões por mês, ordem de grandeza plausível
    "2709": 3.8, "1201": 3.6, "2601": 2.5, "1005": 0.9, "1701": 1.2,
    "0901": 1.1, "5201": 0.4, "0201": 0.9, "0202": 0.1, "4703": 0.8,
}
sazonal = {"1201": [0.5, 0.7, 1.4, 1.7, 1.7, 1.5, 1.2, 0.9, 0.7, 0.6, 0.5, 0.5],
           "1005": [0.6, 0.3, 0.2, 0.2, 0.3, 0.6, 1.1, 1.6, 1.8, 1.7, 1.5, 1.1]}
lista = []
for mes in meses:
    for codigo, valor in base_bi.items():
        fator = sazonal.get(codigo, [1.0] * 12)[mes.month - 1]
        lista.append(
            {
                "headingCode": codigo,
                "year": str(mes.year),
                "monthNumber": str(mes.month),
                "metricFOB": str(int(valor * fator * rng.uniform(0.85, 1.15) * 1e9)),
            }
        )
(DESTINO / "comex_pesos.json").write_text(
    json.dumps({"success": True, "data": {"list": lista}}), encoding="utf-8"
)
print(f"comex_pesos.json: {len(lista)} linhas")
