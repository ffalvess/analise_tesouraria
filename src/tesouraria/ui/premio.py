"""Blocos de interface do prêmio do real.

Vive num módulo próprio porque aparece em dois lugares com profundidades
diferentes: uma leitura compacta no painel, para a pergunta do dia, e a seção
completa na página de câmbio, com histórico e beta móvel. Duplicar a lógica
entre os dois seria o caminho mais curto para os dois divergirem.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesouraria.analytics import currency
from tesouraria.ui import charts, common

# Só cestas que excluem o real. A `DTWEXBGS` (ampla) inclui o próprio real e
# amorteceria o resultado — ela continua no banco, mas não serve de régua aqui.
CESTAS = {"DTWEXAFEGS": "economias avançadas", "DTWEXEMEGS": "emergentes"}

EXPLICACAO = (
    "Separa duas coisas que o gráfico do dólar sozinho confunde: o dólar subiu "
    "**contra todo mundo**, ou o **real** ficou pior? O prêmio é o excesso do real "
    "sobre o que o movimento global explica — **positivo = desvalorização específica "
    "do Brasil**; negativo = o real andou melhor que a cesta."
)

SEM_DADOS = (
    "O prêmio do real precisa da PTAX e de ao menos uma cesta do FRED "
    "(`DTWEXAFEGS` ou `DTWEXEMEGS`). Rode `tesouraria ingest --source us_macro` "
    "com a `FRED_API_KEY` configurada."
)


def cestas_disponiveis() -> dict[str, str]:
    return {
        serie_id: rotulo
        for serie_id, rotulo in CESTAS.items()
        if not common.cache_serie(serie_id).empty
    }


def _bases(cambio: pd.DataFrame, cestas: dict[str, str]) -> dict[str, pd.DataFrame]:
    return {
        rotulo: currency.alinhar(cambio, common.cache_serie(serie_id))
        for serie_id, rotulo in cestas.items()
    }


def _metricas(bases: dict[str, pd.DataFrame], janela: str) -> list[currency.Premio]:
    resultados = [
        currency.calcular(base, janela, rotulo) for rotulo, base in bases.items() if not base.empty
    ]
    if not resultados:
        return []

    # O dólar é o mesmo nas duas comparações; só a cesta muda.
    colunas = st.columns(1 + 2 * len(resultados))
    colunas[0].metric("Dólar no período", charts.formatar_pct(resultados[0].var_brl))

    for indice, resultado in enumerate(resultados):
        colunas[1 + indice * 2].metric(
            f"Cesta — {resultado.cesta}", charts.formatar_pct(resultado.var_cesta)
        )
        colunas[2 + indice * 2].metric(
            f"Prêmio — {resultado.cesta}",
            charts.formatar_pct(resultado.premio_simples),
            (
                f"{resultado.premio_beta:+.2f} p.p. com β {resultado.beta:.2f}"
                if pd.notna(resultado.beta)
                else None
            ),
            help=(
                "Δ% do dólar menos Δ% da cesta. O valor menor desconta o beta "
                "histórico do real: andar mais que a cesta por ser alto beta não é "
                "prêmio de risco."
            ),
        )
    return resultados


def _grafico_base_100(bases: dict[str, pd.DataFrame], janela: str) -> None:
    validas = {r: b for r, b in bases.items() if not b.empty}
    if not validas:
        return

    fim = max(b["data_ref"].max() for b in validas.values())
    corte = pd.Timestamp(fim) - pd.Timedelta(days=currency.JANELAS[janela])

    primeira = next(iter(validas.values()))
    series = [
        ("Dólar (R$)", currency.normalizar(primeira, corte).rename(columns={"usdbrl_100": "valor"}))
    ]
    series += [
        (f"Cesta — {rotulo}", currency.normalizar(base, corte).rename(columns={"cesta_100": "valor"}))
        for rotulo, base in validas.items()
    ]

    st.plotly_chart(
        charts.grafico_series(
            series,
            titulo=f"Base 100 no início da janela ({janela})",
            eixo_y="índice",
            cores=[charts.BR, charts.US, charts.ROXO],
        ),
        width="stretch",
    )


def premio_do_dia(cambio: pd.DataFrame) -> float:
    """Prêmio simples do último pregão, contra a primeira cesta disponível.

    Serve ao indicador do topo do painel, que precisa de um número só.
    """
    cestas = cestas_disponiveis()
    if cambio.empty or not cestas:
        return float("nan")

    serie_id, rotulo = next(iter(cestas.items()))
    base = currency.alinhar(cambio, common.cache_serie(serie_id))
    return currency.calcular(base, "dia", rotulo).premio_simples


def bloco_compacto(cambio: pd.DataFrame, chave: str = "painel") -> None:
    """Leitura do dia, para o painel: seletor de janela, métricas e base 100."""
    st.subheader("Dólar × cesta de moedas")
    st.caption(EXPLICACAO)

    cestas = cestas_disponiveis()
    if cambio.empty or not cestas:
        st.info(SEM_DADOS)
        return

    janela = st.radio(
        "Janela", list(currency.JANELAS), horizontal=True, index=0, key=f"janela_{chave}"
    )
    bases = _bases(cambio, cestas)

    resultados = _metricas(bases, janela)
    if not resultados:
        st.info("Sem observações suficientes na janela escolhida.")
        return

    _grafico_base_100(bases, janela)
    st.caption(resultados[0].leitura)


def secao_premio(cambio: pd.DataFrame, chave: str = "cambio") -> None:
    """Seção completa: a leitura do dia mais o histórico e o beta móvel."""
    bloco_compacto(cambio, chave)

    cestas = cestas_disponiveis()
    if cambio.empty or not cestas:
        return

    bases = {r: b for r, b in _bases(cambio, cestas).items() if not b.empty}
    series_premio = {r: currency.serie_premio(b) for r, b in bases.items()}
    series_premio = {r: s for r, s in series_premio.items() if not s.empty}

    if not series_premio:
        st.caption(
            "O histórico do prêmio precisa de ao menos um ano de pregões em comum "
            "entre a PTAX e a cesta."
        )
        return

    st.subheader("Histórico do prêmio")
    st.plotly_chart(
        charts.grafico_series(
            [
                (f"Acumulado — {rotulo}", s.rename(columns={"premio_acumulado": "valor"}))
                for rotulo, s in series_premio.items()
            ],
            titulo="Prêmio do real acumulado, líquido do beta",
            eixo_y="p.p. acumulados",
            sufixo=" p.p.",
            cores=[charts.US, charts.ROXO],
        ),
        width="stretch",
    )
    st.caption(
        "Trechos de inclinação positiva são períodos em que o real perdeu além do "
        "que o dólar global explicava — risco fiscal, fluxo de saída ou prêmio "
        "político. Inclinação negativa é o real ganhando dos pares, normalmente "
        "quando o carrego atrai capital."
    )

    st.plotly_chart(
        charts.grafico_series(
            [
                (f"β — {rotulo}", s.rename(columns={"beta": "valor"}))
                for rotulo, s in series_premio.items()
            ],
            titulo="Beta móvel do real contra a cesta (252 pregões)",
            eixo_y="β",
            cores=[charts.US, charts.ROXO],
        ),
        width="stretch",
    )
    st.caption(
        "Beta acima de 1 significa que o real amplifica o movimento global do dólar — "
        "é a sensibilidade estrutural que o prêmio ajustado desconta. Quando o beta "
        "sobe, o mesmo choque externo passa a doer mais aqui."
    )
