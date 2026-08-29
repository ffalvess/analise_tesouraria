"""Relação entre o fluxo cambial e a cotação do dólar.

A pergunta prática é direta: quando saem mais dólares do país do que entram, o
dólar sobe — e quanto? A resposta não é constante. Há períodos em que o fluxo
explica quase toda a variação semanal do câmbio e períodos em que o diferencial
de juros ou o dólar global dominam, e o fluxo vira ruído.

Por isso o módulo entrega três coisas, e não um número só:

* a **regressão cheia**, que dá a magnitude média do efeito;
* o **beta móvel**, que mostra quando esse efeito é forte e quando não é;
* os **acumulados**, que situam a semana no contexto do mês e do ano.

Convenção de sinal: `saldo` positivo significa entrada líquida de moeda
estrangeira, e a variação do câmbio é o retorno percentual do dólar. O
coeficiente esperado do saldo é, portanto, **negativo**.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

REGRESSOR_PADRAO = "saldo"


def base_semanal(
    fluxo: pd.DataFrame,
    cambio: pd.DataFrame,
    segmento: str = "total",
    extras: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Alinha fluxo cambial e dólar numa base semanal comparável.

    O fluxo é publicado por semana; o câmbio, por dia. A cotação é reduzida ao
    fechamento de cada semana antes de calcular o retorno, de modo que as duas
    séries falem do mesmo intervalo.
    """
    if fluxo.empty or cambio.empty:
        return pd.DataFrame()

    f = fluxo[fluxo["segmento"] == segmento].copy()
    if f.empty:
        return pd.DataFrame()
    f["data_ref"] = pd.to_datetime(f["data_ref"])
    f = (
        f.set_index("data_ref")[["compras", "vendas", "saldo"]]
        .apply(pd.to_numeric, errors="coerce")
        .resample("W")
        .sum(min_count=1)
    )

    c = cambio[["data_ref", "valor"]].copy()
    c["data_ref"] = pd.to_datetime(c["data_ref"])
    c = c.set_index("data_ref")["valor"].resample("W").last().rename("cambio")

    base = pd.concat([f, c], axis=1).dropna(subset=["saldo", "cambio"])
    base["var_cambio"] = base["cambio"].pct_change() * 100

    for nome, quadro in (extras or {}).items():
        if quadro is None or quadro.empty:
            continue
        serie = quadro[["data_ref", "valor"]].copy()
        serie["data_ref"] = pd.to_datetime(serie["data_ref"])
        semanal = serie.set_index("data_ref")["valor"].resample("W").last()
        base[nome] = semanal
        # Variáveis de nível entram na regressão em variação, para evitar
        # regressão espúria entre séries não estacionárias.
        base[f"var_{nome}"] = base[nome].pct_change() * 100

    return base.dropna(subset=["var_cambio"]).reset_index().rename(columns={"index": "data_ref"})


def regressao(
    base: pd.DataFrame,
    regressores: list[str] | None = None,
    dependente: str = "var_cambio",
) -> dict:
    """Ajusta `var_cambio ~ regressores` por mínimos quadrados ordinários.

    Devolve um dicionário com a tabela de coeficientes, R², número de
    observações e uma leitura em português do coeficiente do fluxo — porque o
    valor bruto ("-0,0021") não diz nada sem a escala.
    """
    regressores = regressores or [REGRESSOR_PADRAO]
    disponiveis = [r for r in regressores if r in base.columns]
    if base.empty or not disponiveis:
        return {"erro": "dados insuficientes para a regressão"}

    dados = base[[dependente, *disponiveis]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(dados) < len(disponiveis) + 5:
        return {"erro": f"apenas {len(dados)} observações completas; insuficiente"}

    X = sm.add_constant(dados[disponiveis])
    modelo = sm.OLS(dados[dependente], X).fit()

    coeficientes = pd.DataFrame(
        {
            "variavel": modelo.params.index,
            "coeficiente": modelo.params.to_numpy(),
            "erro_padrao": modelo.bse.to_numpy(),
            "estatistica_t": modelo.tvalues.to_numpy(),
            "p_valor": modelo.pvalues.to_numpy(),
        }
    )

    return {
        "coeficientes": coeficientes,
        "r2": float(modelo.rsquared),
        "r2_ajustado": float(modelo.rsquared_adj),
        "observacoes": int(modelo.nobs),
        "leitura": _leitura(modelo, disponiveis),
    }


def _leitura(modelo, regressores: list[str]) -> str:
    """Traduz o coeficiente do fluxo para uma frase acionável."""
    if REGRESSOR_PADRAO not in regressores:
        return ""

    beta = float(modelo.params[REGRESSOR_PADRAO])
    p = float(modelo.pvalues[REGRESSOR_PADRAO])
    efeito = beta * 1000  # efeito de US$ 1 bilhão, dado que o fluxo vem em US$ milhões

    direcao = "queda" if efeito < 0 else "alta"
    significancia = (
        "estatisticamente significante a 5%"
        if p < 0.05
        else f"não significante a 5% (p = {p:.2f})"
    )
    return (
        f"Cada US$ 1 bilhão de entrada líquida está associado a uma {direcao} de "
        f"{abs(efeito):.2f}% no dólar na semana — {significancia}."
    )


def beta_movel(
    base: pd.DataFrame, janela: int = 52, regressor: str = REGRESSOR_PADRAO
) -> pd.DataFrame:
    """Beta e correlação móveis entre o fluxo e a variação do dólar.

    Mais informativo que a regressão cheia: mostra *quando* o fluxo passou a
    explicar o câmbio. Trechos em que o beta cruza o zero indicam regimes em que
    outro fator — risco fiscal, dólar global — assumiu o comando.
    """
    if base.empty or regressor not in base.columns:
        return pd.DataFrame(columns=["data_ref", "beta", "correlacao"])

    dados = base[["data_ref", "var_cambio", regressor]].dropna().reset_index(drop=True)
    if len(dados) < janela:
        return pd.DataFrame(columns=["data_ref", "beta", "correlacao"])

    x = dados[regressor]
    y = dados["var_cambio"]

    covariancia = y.rolling(janela).cov(x)
    variancia = x.rolling(janela).var()
    dados["beta"] = (covariancia / variancia.replace(0, np.nan)).round(6)
    dados["correlacao"] = y.rolling(janela).corr(x).round(4)

    return dados[["data_ref", "beta", "correlacao"]].dropna().reset_index(drop=True)


def acumulados(fluxo: pd.DataFrame, segmento: str = "total") -> pd.DataFrame:
    """Fluxo acumulado no mês e no ano, por segmento.

    É a leitura que a imprensa e a mesa usam no dia a dia: "o mês está negativo
    em US$ 3,2 bilhões" diz mais sobre a pressão sobre o câmbio do que o número
    de uma semana isolada.
    """
    if fluxo.empty:
        return pd.DataFrame()

    f = fluxo[fluxo["segmento"] == segmento].copy()
    if f.empty:
        return pd.DataFrame()

    f["data_ref"] = pd.to_datetime(f["data_ref"])
    f = f.sort_values("data_ref")
    f["saldo"] = pd.to_numeric(f["saldo"], errors="coerce")

    f["acum_mes"] = f.groupby(f["data_ref"].dt.to_period("M"))["saldo"].cumsum()
    f["acum_ano"] = f.groupby(f["data_ref"].dt.year)["saldo"].cumsum()

    return f[["data_ref", "segmento", "compras", "vendas", "saldo", "acum_mes", "acum_ano"]]


def resumo_por_segmento(fluxo: pd.DataFrame, semanas: int = 12) -> pd.DataFrame:
    """Saldo das últimas N semanas por segmento — comercial contra financeiro.

    Separar as duas pernas costuma ser o que explica um câmbio que sobe apesar
    de superávit comercial recorde: o comercial entra, o financeiro sai mais.
    """
    if fluxo.empty:
        return pd.DataFrame()

    f = fluxo.copy()
    f["data_ref"] = pd.to_datetime(f["data_ref"])
    corte = f["data_ref"].max() - pd.Timedelta(weeks=semanas)
    recente = f[f["data_ref"] > corte]

    return (
        recente.groupby("segmento", as_index=False)
        .agg(
            saldo_periodo=("saldo", "sum"),
            saldo_medio=("saldo", "mean"),
            semanas=("saldo", "count"),
        )
        .sort_values("segmento")
    )
