"""Gera as amostras offline em data/fixtures/.

Os dados são SINTÉTICOS: reproduzem a forma e o formato de cada fonte real
(colunas, separadores, codificação, ordens de grandeza plausíveis) para que os
parsers e a interface possam ser exercitados sem rede. Não são cotações reais e
não devem ser usados para decisão.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "data" / "fixtures"
DESTINO.mkdir(parents=True, exist_ok=True)

INICIO = dt.date(2024, 1, 2)
FIM = dt.date(2026, 8, 28)

rng = np.random.default_rng(20260828)
dias = pd.bdate_range(INICIO, FIM)
n = len(dias)


def passeio(inicial: float, vol: float, reversao: float = 0.0) -> np.ndarray:
    """Passeio aleatório com leve reversão à média, para séries com jeito de mercado."""
    valores = np.empty(n)
    valores[0] = inicial
    for i in range(1, n):
        arraste = reversao * (inicial - valores[i - 1])
        valores[i] = valores[i - 1] + arraste + rng.normal(0, vol)
    return valores


# ---------------------------------------------------------------- fatores BR
nivel_br = passeio(13.8, 0.045, 0.004)      # nível da curva pré
inclin_br = passeio(-0.6, 0.030, 0.010)     # 10a menos 1a
nivel_real = passeio(7.1, 0.030, 0.006)     # curva IPCA+

# ---------------------------------------------------------------- fatores US
nivel_us = passeio(4.05, 0.030, 0.005)
inclin_us = passeio(0.15, 0.025, 0.010)
nivel_tips = passeio(1.85, 0.025, 0.006)

# ------------------------------------------------------------------- câmbio
choque_cambio = rng.normal(0, 0.006, n)


def taxa_pre(prazo: float, i: int) -> float:
    """Curva pré: nível, inclinação log no prazo e uma corcova curta."""
    return (
        nivel_br[i]
        + inclin_br[i] * np.log1p(prazo) / np.log(11)
        + 0.25 * np.exp(-prazo / 1.5)
    )


def taxa_ipca(prazo: float, i: int) -> float:
    return nivel_real[i] + 0.35 * np.log1p(prazo) / np.log(11) - 0.20 * np.exp(-prazo / 2)


def taxa_us(prazo: float, i: int) -> float:
    return (
        nivel_us[i]
        + inclin_us[i] * np.log1p(prazo) / np.log(11)
        + 0.30 * np.exp(-prazo / 0.8)
    )


def taxa_tips(prazo: float, i: int) -> float:
    return nivel_tips[i] + 0.30 * np.log1p(prazo) / np.log(11)


# =========================================================== Tesouro Direto
TITULOS = [
    ("Tesouro Prefixado", "2027-01-01"),
    ("Tesouro Prefixado", "2029-01-01"),
    ("Tesouro Prefixado", "2031-01-01"),
    ("Tesouro Prefixado com Juros Semestrais", "2033-01-01"),
    ("Tesouro Prefixado com Juros Semestrais", "2035-01-01"),
    ("Tesouro Prefixado com Juros Semestrais", "2037-01-01"),
    ("Tesouro IPCA+", "2029-05-15"),
    ("Tesouro IPCA+", "2035-05-15"),
    ("Tesouro IPCA+", "2045-05-15"),
    ("Tesouro IPCA+ com Juros Semestrais", "2032-08-15"),
    ("Tesouro IPCA+ com Juros Semestrais", "2040-08-15"),
    ("Tesouro IPCA+ com Juros Semestrais", "2055-05-15"),
    ("Tesouro Selic", "2029-03-01"),
]

linhas = []
for i, dia in enumerate(dias):
    for nome, vencimento in TITULOS:
        venc = pd.Timestamp(vencimento)
        prazo = (venc - dia).days / 365.25
        if prazo <= 0.05:
            continue

        if nome.startswith("Tesouro Prefixado"):
            taxa = taxa_pre(prazo, i) + rng.normal(0, 0.02)
            pu = 1000 / (1 + taxa / 100) ** prazo
        elif nome.startswith("Tesouro IPCA+"):
            taxa = taxa_ipca(prazo, i) + rng.normal(0, 0.02)
            pu = 4200 / (1 + taxa / 100) ** prazo
        else:  # Selic
            taxa = 0.03 + rng.normal(0, 0.005)
            pu = 14000 + i * 3.1

        linhas.append(
            {
                "Tipo Titulo": nome,
                "Data Vencimento": venc.strftime("%d/%m/%Y"),
                "Data Base": dia.strftime("%d/%m/%Y"),
                "Taxa Compra Manha": round(taxa + 0.03, 2),
                "Taxa Venda Manha": round(taxa, 2),
                "PU Compra Manha": round(pu * 0.998, 2),
                "PU Venda Manha": round(pu, 2),
                "PU Base Manha": round(pu * 0.999, 2),
            }
        )

tesouro = pd.DataFrame(linhas)
csv = tesouro.to_csv(sep=";", index=False, decimal=",", float_format="%.2f")
(DESTINO / "tesouro_direto.csv").write_bytes(csv.encode("latin-1"))
print(f"tesouro_direto.csv: {len(tesouro)} linhas")


# ============================================================= US Treasury
TENORES_NOMINAL = {
    "1 Mo": 1 / 12, "2 Mo": 2 / 12, "3 Mo": 0.25, "4 Mo": 4 / 12, "6 Mo": 0.5,
    "1 Yr": 1, "2 Yr": 2, "3 Yr": 3, "5 Yr": 5, "7 Yr": 7, "10 Yr": 10,
    "20 Yr": 20, "30 Yr": 30,
}
TENORES_REAL = {"5 YR": 5, "7 YR": 7, "10 YR": 10, "20 YR": 20, "30 YR": 30}


def escrever_treasury(nome: str, tenores: dict[str, float], funcao) -> None:
    registros = []
    for i, dia in enumerate(dias):
        linha = {"Date": dia.strftime("%m/%d/%Y")}
        for rotulo, prazo in tenores.items():
            linha[rotulo] = round(funcao(prazo, i) + rng.normal(0, 0.015), 2)
        registros.append(linha)
    df = pd.DataFrame(registros).iloc[::-1]  # o Treasury publica do mais recente
    (DESTINO / nome).write_text(df.to_csv(index=False), encoding="utf-8")
    print(f"{nome}: {len(df)} linhas")


escrever_treasury("us_treasury_nominal.csv", TENORES_NOMINAL, taxa_us)
escrever_treasury("us_treasury_real.csv", TENORES_REAL, taxa_tips)


# =================================================================== ANBIMA
ultimo = len(dias) - 1
data_anbima = dias[ultimo]
vertices = [21, 42, 63, 126, 189, 252, 378, 504, 756, 1008, 1260, 1512, 2016, 2520, 3780, 5040]

linhas_txt = [
    "ANBIMA - ESTRUTURA A TERMO DAS TAXAS DE JUROS",
    f"Data de referencia: {data_anbima.strftime('%d/%m/%Y')}",
    "AMOSTRA SINTETICA PARA DESENVOLVIMENTO - NAO SAO TAXAS REAIS",
    "",
    "Vertices;ETTJ IPCA;ETTJ PREF;Inflacao Implicita",
]
for v in vertices:
    prazo = v / 252
    ipca = taxa_ipca(prazo, ultimo)
    pre = taxa_pre(prazo, ultimo)
    implicita = ((1 + pre / 100) / (1 + ipca / 100) - 1) * 100
    linhas_txt.append(
        f"{v};{ipca:.4f};{pre:.4f};{implicita:.4f}".replace(".", ",")
    )

(DESTINO / "anbima_ettj.txt").write_bytes(("\n".join(linhas_txt) + "\n").encode("latin-1"))
print(f"anbima_ettj.txt: {len(vertices)} vértices")


# ======================================================================= B3
CODIGOS_MES = {1: "F", 2: "G", 4: "J", 7: "N", 10: "V"}
contratos = []
base = data_anbima
for ano in range(base.year, base.year + 11):
    for mes, codigo in CODIGOS_MES.items():
        venc = pd.Timestamp(year=ano, month=mes, day=1)
        if venc <= base:
            continue
        du = max(int((venc - base).days / 365.25 * 252), 1)
        prazo = du / 252
        taxa = taxa_pre(prazo, ultimo)
        pu = 100_000 / (1 + taxa / 100) ** (du / 252)
        contratos.append(
            {
                "VENCTO": f"{codigo}{str(ano)[2:]}",
                "CONTR. ABERT.(1)": 120_000,
                "CONTR. FECH.(2)": 118_500,
                "PREÇO MÉD.": round(pu, 2),
                "ÚLT. PREÇO": round(pu, 2),
                "AJUSTE": round(pu, 2),
                "VAR. PTOS.": 12.5,
            }
        )

tabela_b3 = pd.DataFrame(contratos)
html = f"""<html><head><meta charset="iso-8859-1"></head><body>
<h3>BM&amp;FBOVESPA - Mercadoria: DI1 - {base.strftime('%d/%m/%Y')}</h3>
<p>AMOSTRA SINTETICA PARA DESENVOLVIMENTO - NAO SAO AJUSTES REAIS</p>
{tabela_b3.to_html(index=False, decimal=',')}
</body></html>"""
(DESTINO / "b3_di.html").write_bytes(html.encode("latin-1", errors="replace"))
print(f"b3_di.html: {len(tabela_b3)} contratos")


# ================================================================= BCB SGS
def sgs(datas, valores) -> list[dict]:
    return [
        {"data": d.strftime("%d/%m/%Y"), "valor": f"{v:.4f}"}
        for d, v in zip(datas, valores, strict=True)
        if np.isfinite(v)
    ]


meses = pd.date_range(INICIO, FIM, freq="MS")
selic_meta = np.clip(nivel_br[:: max(n // len(meses), 1)][: len(meses)] + 0.6, 8, 20)
cambio = 5.40 * np.exp(np.cumsum(choque_cambio))
cdi_diario = (1 + (nivel_br - 0.15) / 100) ** (1 / 252) - 1

series_sgs = {
    "432": sgs(dias, np.repeat(np.round(selic_meta * 4) / 4, 30)[:n]),
    "11": sgs(dias, cdi_diario * 100),
    "12": sgs(dias, cdi_diario * 100),
    "4389": sgs(dias, nivel_br - 0.15),
    "1": sgs(dias, cambio),
    "10813": sgs(dias, cambio - 0.001),
    "433": sgs(meses, rng.normal(0.38, 0.18, len(meses))),
    "13522": sgs(meses, passeio(4.4, 0.10, 0.02)[: len(meses)]),
    "189": sgs(meses, rng.normal(0.30, 0.45, len(meses))),
    "24363": sgs(meses, 145 + np.cumsum(rng.normal(0.15, 0.5, len(meses)))),
    "24364": sgs(meses, 146 + np.cumsum(rng.normal(0.15, 0.4, len(meses)))),
    "24369": sgs(meses, np.clip(7.2 - np.cumsum(rng.normal(0.02, 0.09, len(meses))), 5, 12)),
    "2255": sgs(meses, rng.normal(28_500, 2_600, len(meses))),
    "2256": sgs(meses, rng.normal(21_800, 2_100, len(meses))),
    "2257": sgs(meses, rng.normal(6_700, 2_000, len(meses))),
    "4192": sgs(meses, rng.normal(345_000, 6_000, len(meses))),
    "4503": sgs(meses, 61 + np.cumsum(rng.normal(0.08, 0.25, len(meses)))),
}
(DESTINO / "bcb_sgs.json").write_text(json.dumps(series_sgs), encoding="utf-8")
print(f"bcb_sgs.json: {len(series_sgs)} séries")


# ============================================================ fluxo cambial
semanas = pd.date_range(INICIO, FIM, freq="W-FRI")
cambio_semanal = pd.Series(cambio, index=dias).resample("W-FRI").last().reindex(semanas).ffill()
var_cambio = cambio_semanal.pct_change().fillna(0).to_numpy() * 100

# O fluxo é construído com relação negativa com a variação do dólar mais ruído,
# para que a página de regressão tenha um sinal a encontrar nesta amostra.
saldo_total = -var_cambio * 620 + rng.normal(150, 700, len(semanas))
saldo_comercial = np.abs(rng.normal(1_450, 620, len(semanas)))
saldo_financeiro = saldo_total - saldo_comercial

compras_com = saldo_comercial + np.abs(rng.normal(4_800, 500, len(semanas)))
compras_fin = np.abs(rng.normal(9_500, 900, len(semanas)))

series_fluxo = {
    "22707": sgs(semanas, compras_com),
    "22708": sgs(semanas, compras_com - saldo_comercial),
    "22709": sgs(semanas, saldo_comercial),
    "22710": sgs(semanas, compras_fin),
    "22711": sgs(semanas, compras_fin - saldo_financeiro),
    "22712": sgs(semanas, saldo_financeiro),
    "22713": sgs(semanas, compras_com + compras_fin),
    "22714": sgs(semanas, compras_com + compras_fin - saldo_total),
    "22715": sgs(semanas, saldo_total),
}
(DESTINO / "fx_flow.json").write_text(json.dumps(series_fluxo), encoding="utf-8")
print(f"fx_flow.json: {len(semanas)} semanas")


# ==================================================================== Focus
coletas = pd.date_range(INICIO, FIM, freq="W-FRI")
anos_ref = [str(a) for a in range(2024, 2029)]
niveis = {"IPCA": 4.2, "Selic": 11.5, "Câmbio": 5.5, "PIB Total": 2.1}
ruidos = {"IPCA": 0.05, "Selic": 0.09, "Câmbio": 0.04, "PIB Total": 0.05}

registros_focus = []
for indicador, nivel in niveis.items():
    for ano in anos_ref:
        trajetoria = passeio(nivel + (int(ano) - 2024) * 0.05, ruidos[indicador], 0.01)
        passo = max(len(trajetoria) // len(coletas), 1)
        for j, data in enumerate(coletas):
            mediana = trajetoria[min(j * passo, len(trajetoria) - 1)]
            registros_focus.append(
                {
                    "Indicador": indicador,
                    "Data": data.strftime("%Y-%m-%d"),
                    "DataReferencia": ano,
                    "Media": round(mediana + 0.02, 4),
                    "Mediana": round(mediana, 4),
                    "DesvioPadrao": round(abs(rng.normal(0.25, 0.05)), 4),
                    "Minimo": round(mediana - 0.9, 4),
                    "Maximo": round(mediana + 0.9, 4),
                    "numeroRespondentes": int(rng.integers(45, 120)),
                }
            )

(DESTINO / "focus_geral.json").write_text(
    json.dumps({"value": registros_focus}), encoding="utf-8"
)

top5 = []
for registro in registros_focus[::3]:
    copia = dict(registro)
    copia["Mediana"] = round(copia["Mediana"] + rng.normal(0, 0.12), 4)
    copia["Media"] = copia["Mediana"]
    copia["numeroRespondentes"] = 5
    top5.append(copia)
(DESTINO / "focus_top5.json").write_text(json.dumps({"value": top5}), encoding="utf-8")
print(f"focus: {len(registros_focus)} geral / {len(top5)} top5")


# ================================================================ Comex Stat
comex = {}
for fluxo, nivel in (("export", 28_500), ("import", 21_800)):
    comex[fluxo] = {
        "data": {
            "list": [
                {
                    "year": int(m.year),
                    "month": int(m.month),
                    "metricFOB": float(round(rng.normal(nivel, nivel * 0.09) * 1e6, 0)),
                }
                for m in meses
            ]
        }
    }
(DESTINO / "comex.json").write_text(json.dumps(comex), encoding="utf-8")
print(f"comex.json: {len(meses)} meses x 2 fluxos")


# ===================================================================== SIDRA
def sidra(rotulo_periodo: str, periodos, valores) -> list[dict]:
    cabecalho = {
        "NC": "Nível Territorial (Código)",
        "NN": "Nível Territorial",
        "D1C": "Brasil (Código)",
        "D1N": "Brasil",
        "D2C": f"{rotulo_periodo} (Código)",
        "D2N": rotulo_periodo,
        "V": "Valor",
    }
    linhas = [cabecalho]
    for periodo, valor in zip(periodos, valores, strict=True):
        linhas.append(
            {
                "NC": "1", "NN": "Brasil", "D1C": "1", "D1N": "Brasil",
                "D2C": periodo, "D2N": periodo, "V": f"{valor:.2f}",
            }
        )
    return linhas


codigos_mes = [m.strftime("%Y%m") for m in meses]
trimestres = [m.strftime("%Y%m") for m in meses]
anos = [str(a) for a in range(2015, 2027)]

sidra_payload = {
    "1737-63": sidra("Mês", codigos_mes, rng.normal(0.38, 0.18, len(meses))),
    "1737-2265": sidra("Mês", codigos_mes, passeio(4.4, 0.10, 0.02)[: len(meses)]),
    "6381-4099": sidra(
        "Trimestre Móvel", trimestres,
        np.clip(7.2 - np.cumsum(rng.normal(0.02, 0.09, len(meses))), 5, 12),
    ),
    "5932-6564": sidra("Ano", anos, rng.normal(2.0, 1.4, len(anos))),
    "8880-7169": sidra("Mês", codigos_mes, 100 + np.cumsum(rng.normal(0.1, 0.6, len(meses)))),
}
(DESTINO / "ibge_sidra.json").write_text(json.dumps(sidra_payload), encoding="utf-8")
print(f"ibge_sidra.json: {len(sidra_payload)} séries")


# ====================================================================== FRED
def fred(datas, valores) -> dict:
    return {
        "observations": [
            {"date": d.strftime("%Y-%m-%d"), "value": f"{v:.4f}"}
            for d, v in zip(datas, valores, strict=True)
        ]
    }


fred_payload = {
    "FEDFUNDS": fred(meses, np.clip(nivel_us[:: max(n // len(meses), 1)][: len(meses)] + 0.3, 0, 8)),
    "DFF": fred(dias, np.clip(nivel_us + 0.3, 0, 8)),
    "CPIAUCSL": fred(meses, 310 + np.cumsum(np.abs(rng.normal(0.7, 0.3, len(meses))))),
    "CPILFESL": fred(meses, 315 + np.cumsum(np.abs(rng.normal(0.6, 0.25, len(meses))))),
    "UNRATE": fred(meses, np.clip(4.1 + np.cumsum(rng.normal(0.01, 0.08, len(meses))), 3, 8)),
    "PAYEMS": fred(meses, 158_000 + np.cumsum(rng.normal(150, 60, len(meses)))),
    # As tres cestas do dolar. Passeio com reversao a media, para os valores
    # ficarem dentro da faixa plausivel declarada em config/sources.yaml -- que
    # e justamente o que a validacao de ingestao confere.
    "DTWEXBGS": fred(dias, passeio(121, 0.12, 0.004)),
    "DTWEXAFEGS": fred(dias, passeio(112, 0.14, 0.004)),
    # Emergentes construida para andar junto com o dolar/real: e o descolamento
    # entre as duas que a pagina do premio mede.
    "DTWEXEMEGS": fred(dias, passeio(158, 0.10, 0.004)),
    "T10Y2Y": fred(dias, inclin_us * 0.8),
}


# ------------------------------------------------------ séries da Macro EUA
# Gerador próprio: tirar números do `rng` principal deslocaria tudo o que vem
# depois e mudaria as outras amostras sem motivo.
rng_eua = np.random.default_rng(20261009)
trimestres_us = pd.date_range("2024-01-01", "2026-06-30", freq="QS")
semanas = pd.date_range("2024-01-06", FIM, freq="W-SAT")
q = len(trimestres_us)


def indice_precos(inicial: float, inflacao_anual: float, vol: float) -> np.ndarray:
    """Índice mensal que cresce à inflação pedida, com ruído."""
    mensal = (1 + inflacao_anual) ** (1 / 12) - 1
    return inicial * np.cumprod(1 + mensal + rng_eua.normal(0, vol, len(meses)))


def encadear(inicial: float, crescimento_anualizado: np.ndarray) -> np.ndarray:
    """Nível trimestral a partir do crescimento anualizado (% a.a.)."""
    fatores = (1 + np.asarray(crescimento_anualizado) / 100) ** 0.25
    return inicial * np.cumprod(fatores)


# PIB pela despesa: crescimento anualizado de cada componente e a
# contribuição = participação x crescimento, como o BEA publica. O PIB cresce a
# soma das contribuições, de modo que as amostras fecham como o dado real.
participacao = {"C": 0.68, "I": 0.18, "G": 0.17, "X": 0.11, "M": -0.14}
crescimento = {
    "C": rng_eua.normal(2.3, 0.8, q),
    "I": rng_eua.normal(2.0, 4.0, q),
    "G": rng_eua.normal(1.5, 1.0, q),
    "X": rng_eua.normal(2.0, 4.0, q),
    "M": rng_eua.normal(3.0, 5.0, q),
}
contribuicao = {k: participacao[k] * crescimento[k] for k in participacao}
crescimento_pib = sum(contribuicao.values())

# PIB pela produção: o total é o mesmo PIB; os setores somam a ele.
setores = {
    "AFH": rng_eua.normal(0.02, 0.15, q),
    "M": rng_eua.normal(0.03, 0.20, q),  # extrativa: só entra dentro dos bens
    "U": rng_eua.normal(0.02, 0.12, q),
    "C": rng_eua.normal(0.05, 0.12, q),
    "MD": rng_eua.normal(0.10, 0.25, q),
    "MN": rng_eua.normal(0.05, 0.15, q),
    "GOV": rng_eua.normal(0.15, 0.10, q),
}
bens = setores["AFH"] + setores["M"] + setores["C"] + setores["MD"] + setores["MN"]
servicos_privados = crescimento_pib - bens - setores["GOV"]

fred_payload.update(
    {
        "PCEPI": fred(meses, indice_precos(122, 0.026, 0.0012)),
        "PCEPILFE": fred(meses, indice_precos(121, 0.028, 0.0010)),
        "CPIUFDSL": fred(meses, indice_precos(330, 0.024, 0.0020)),
        "CPIENGSL": fred(meses, indice_precos(280, 0.000, 0.0150)),
        "CUSR0000SACL1E": fred(meses, indice_precos(160, 0.003, 0.0015)),
        "CUSR0000SAH1": fred(meses, indice_precos(390, 0.045, 0.0015)),
        "CUSR0000SASLE": fred(meses, indice_precos(410, 0.037, 0.0012)),
        "U6RATE": fred(meses, np.clip(7.6 + np.cumsum(rng_eua.normal(0.01, 0.10, len(meses))), 6, 12)),
        "ICSA": fred(semanas, np.clip(225_000 + np.cumsum(rng_eua.normal(0, 4_000, len(semanas))), 180_000, 320_000)),
        "CIVPART": fred(meses, np.clip(62.6 + np.cumsum(rng_eua.normal(0, 0.05, len(meses))), 61.5, 63.5)),
        "CES0500000003": fred(meses, 34.5 * 1.0033 ** np.arange(len(meses)) + rng_eua.normal(0, 0.03, len(meses))),
        "JTSJOL": fred(meses, np.clip(8_000 - 60 * np.arange(len(meses)) + rng_eua.normal(0, 150, len(meses)), 6_000, 9_000)),
        "UNEMPLOY": fred(meses, 6_800 + np.cumsum(rng_eua.normal(15, 60, len(meses)))),
        "GDPC1": fred(trimestres_us, encadear(23_000, crescimento_pib)),
        "PCECC96": fred(trimestres_us, encadear(15_600, crescimento["C"])),
        "GPDIC1": fred(trimestres_us, encadear(4_200, crescimento["I"])),
        "GCEC1": fred(trimestres_us, encadear(3_900, crescimento["G"])),
        "EXPGSC1": fred(trimestres_us, encadear(2_550, crescimento["X"])),
        "IMPGSC1": fred(trimestres_us, encadear(3_450, crescimento["M"])),
        "DPCERY2Q224SBEA": fred(trimestres_us, contribuicao["C"]),
        "A006RY2Q224SBEA": fred(trimestres_us, contribuicao["I"]),
        "A822RY2Q224SBEA": fred(trimestres_us, contribuicao["G"]),
        "A020RY2Q224SBEA": fred(trimestres_us, contribuicao["X"]),
        "A021RY2Q224SBEA": fred(trimestres_us, contribuicao["M"]),
        "CPGDPAI": fred(trimestres_us, crescimento_pib),
        "CPGDPGPI": fred(trimestres_us, bens),
        "CPGDPAFH": fred(trimestres_us, setores["AFH"]),
        "CPGDPU": fred(trimestres_us, setores["U"]),
        "CPGDPC": fred(trimestres_us, setores["C"]),
        "CPGDPMD": fred(trimestres_us, setores["MD"]),
        "CPGDPMN": fred(trimestres_us, setores["MN"]),
        # Volume de cada setor: crescimento = contribuição / participação no PIB.
        "VAQIAFH": fred(trimestres_us, encadear(105, setores["AFH"] / 0.009)),
        "VAQIM": fred(trimestres_us, encadear(135, setores["M"] / 0.013)),
        "VAQIC": fred(trimestres_us, encadear(104, setores["C"] / 0.042)),
        "VAQIMA": fred(trimestres_us, encadear(112, (setores["MD"] + setores["MN"]) / 0.10)),
        "RVASPI": fred(trimestres_us, encadear(17_300, servicos_privados / 0.70)),
        "INDPRO": fred(meses, 102 + np.cumsum(rng_eua.normal(0.03, 0.40, len(meses)))),
        "IPMAN": fred(meses, 100 + np.cumsum(rng_eua.normal(0.02, 0.45, len(meses)))),
    }
)
(DESTINO / "us_macro.json").write_text(json.dumps(fred_payload), encoding="utf-8")
print(f"us_macro.json: {len(fred_payload)} séries")


# ================================================================ documentos
FRASES_HAWK_PT = (
    "O Comitê avalia que o cenário exige política monetária em terreno "
    "contracionista por período bastante prolongado. As expectativas de "
    "inflação seguem desancoradas e os riscos de alta predominam, com mercado "
    "de trabalho aquecido e atividade resiliente. O Copom manterá vigilância."
)
FRASES_DOVE_PT = (
    "O processo desinflacionário segue em curso e as expectativas de inflação "
    "voltaram a se ancorar. Observa-se arrefecimento da atividade e maior "
    "ociosidade nos fatores de produção, o que abre espaço para a continuidade "
    "do ciclo de cortes da taxa básica."
)
FRASES_HAWK_EN = (
    "The Committee judges that a restrictive stance remains appropriate and is "
    "prepared to consider additional firming. Upside risks to inflation persist "
    "and the labor market remains tight, so policy will stay higher for longer."
)
FRASES_DOVE_EN = (
    "Disinflation has broadened and the labor market is cooling. With inflation "
    "expectations well anchored, the Committee sees scope for policy easing and "
    "expects a gradual easing cycle over the coming quarters."
)

AUTORIDADES = {
    "BCB": ["Gabriel Galípolo", "Diogo Guillen", "Nilton David", "Ailton de Aquino"],
    "Fed": ["Jerome Powell", "John Williams", "Christopher Waller", "Lisa Cook"],
}


def documentos_sinteticos(quantidade: int, secao: str) -> list[dict]:
    itens = []
    for k in range(quantidade):
        instituicao = "BCB" if k % 2 == 0 else "Fed"
        idioma = "pt" if instituicao == "BCB" else "en"
        hawk = rng.random() < 0.5
        if idioma == "pt":
            texto = FRASES_HAWK_PT if hawk else FRASES_DOVE_PT
        else:
            texto = FRASES_HAWK_EN if hawk else FRASES_DOVE_EN

        data = (pd.Timestamp(FIM) - pd.Timedelta(days=k * 6)).date()
        url = f"https://exemplo.invalido/{secao}/{instituicao.lower()}/{k}"
        itens.append(
            {
                "id": f"{secao}{k:04d}" + "0" * 20,
                "fonte": f"AMOSTRA {instituicao}",
                "instituicao": instituicao if secao == "discursos" else
                               ["FMI", "BIS", "Banco Mundial", "Fitch"][k % 4],
                "autor": AUTORIDADES[instituicao][k % 4],
                "titulo": (
                    f"{'Discurso' if secao == 'discursos' else 'Relatório'} "
                    f"{'sobre política monetária' if hawk else 'sobre o cenário de inflação'} "
                    f"({data.isoformat()})"
                ),
                "data_pub": data.isoformat(),
                "url": url,
                "tipo": "discurso" if secao == "discursos" else "relatorio",
                "idioma": idioma,
                "texto": texto + " " + texto,
            }
        )
    return itens


(DESTINO / "feeds_discursos.json").write_text(
    json.dumps(documentos_sinteticos(90, "discursos")), encoding="utf-8"
)
(DESTINO / "feeds_research.json").write_text(
    json.dumps(documentos_sinteticos(40, "research")), encoding="utf-8"
)
print("feeds_discursos.json: 90 documentos / feeds_research.json: 40 documentos")

print("\nFixtures geradas em", DESTINO)
