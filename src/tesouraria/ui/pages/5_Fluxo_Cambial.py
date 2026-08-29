"""Fluxo cambial e o seu efeito sobre a cotação do dólar.

Convenção de sinal em toda a página: **saldo positivo = entrada líquida** de
moeda estrangeira. O coeficiente esperado na regressão é, portanto, negativo —
mais dólares entrando, dólar mais barato.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesouraria.analytics import fxflow
from tesouraria.ui import charts, common

common.configurar("Fluxo cambial × dólar", "💵")

if not common.exigir_dados():
    st.stop()

fluxo = common.cache_fluxo()
cambio = common.cache_serie("1")

if fluxo.empty:
    st.warning(
        "Nenhum dado de fluxo cambial no banco. Rode `tesouraria ingest --source fx_flow`. "
        "Se a fonte falhar, confira os códigos de série em `config/sources.yaml` — eles "
        "estão marcados para verificação."
    )
    st.stop()

if cambio.empty:
    st.warning("Sem a série de câmbio (PTAX, série 1 do SGS) para cruzar com o fluxo.")
    st.stop()

segmento = st.sidebar.selectbox(
    "Segmento",
    ["total", "comercial", "financeiro"],
    format_func=lambda s: {
        "total": "Total",
        "comercial": "Comercial (exportação e importação)",
        "financeiro": "Financeiro (investimentos e remessas)",
    }[s],
)
janela_beta = st.sidebar.slider("Janela do beta móvel (semanas)", 13, 104, 52, 13)

base = fxflow.base_semanal(fluxo, cambio, segmento)
if base.empty:
    st.warning(f"Sem observações para o segmento '{segmento}'.")
    st.stop()


# ------------------------------------------------------------- panorama
resumo = fxflow.resumo_por_segmento(fluxo, semanas=12)
acumulado = fxflow.acumulados(fluxo, segmento)

colunas = st.columns(4)
ultima = base.iloc[-1]
colunas[0].metric(
    "Saldo da última semana",
    f"US$ {ultima['saldo']:,.0f} mi".replace(",", "."),
    f"{ultima['var_cambio']:+.2f}% no dólar",
)
if not acumulado.empty:
    colunas[1].metric(
        "Acumulado no mês",
        f"US$ {acumulado['acum_mes'].iloc[-1]:,.0f} mi".replace(",", "."),
    )
    colunas[2].metric(
        "Acumulado no ano",
        f"US$ {acumulado['acum_ano'].iloc[-1]:,.0f} mi".replace(",", "."),
    )
colunas[3].metric("Dólar", f"R$ {ultima['cambio']:.4f}")


# ------------------------------------------------------- fluxo contra dólar
st.subheader("Fluxo semanal e cotação")
st.plotly_chart(
    charts.grafico_barras_linha(
        base,
        "data_ref",
        "saldo",
        "cambio",
        "Saldo do fluxo (US$ milhões)",
        "Dólar (R$)",
        titulo=f"Fluxo {segmento} × dólar",
    ),
    use_container_width=True,
)
st.caption(
    "Barras verdes são semanas de entrada líquida; vermelhas, de saída. A leitura útil "
    "não é a barra isolada, e sim a sequência: séries de barras vermelhas com o dólar "
    "subindo indicam pressão de fluxo; dólar subindo com barras verdes indica que o "
    "movimento vem de outro lugar."
)


# ------------------------------------------------------------------ análise
aba_regressao, aba_beta, aba_acumulado, aba_segmentos = st.tabs(
    ["Regressão", "Beta móvel", "Acumulados", "Comercial × financeiro"]
)

with aba_regressao:
    regressores = ["saldo"]
    extras: dict[str, pd.DataFrame] = {}

    dxy = common.cache_serie("DTWEXBGS")
    if not dxy.empty:
        extras["dxy"] = dxy
    if extras:
        base = fxflow.base_semanal(fluxo, cambio, segmento, extras)
        if "var_dxy" in base.columns:
            usar_dxy = st.checkbox(
                "Controlar pelo índice do dólar global (DXY)",
                value=True,
                help=(
                    "Sem esse controle, parte do movimento do real que na verdade é "
                    "movimento do dólar contra todas as moedas seria atribuída ao fluxo."
                ),
            )
            if usar_dxy:
                regressores.append("var_dxy")

    resultado = fxflow.regressao(base, regressores)

    if "erro" in resultado:
        st.info(resultado["erro"])
    else:
        colunas = st.columns(3)
        colunas[0].metric("R²", f"{resultado['r2']:.3f}")
        colunas[1].metric("R² ajustado", f"{resultado['r2_ajustado']:.3f}")
        colunas[2].metric("Observações", resultado["observacoes"])

        st.dataframe(
            resultado["coeficientes"].round(6),
            use_container_width=True,
            hide_index=True,
        )
        st.success(resultado["leitura"], icon="📊")
        st.caption(
            "A regressão é descritiva, não causal: fluxo e câmbio são determinados "
            "conjuntamente, e um choque de risco move os dois ao mesmo tempo. Leia o "
            "coeficiente como a associação média no período, não como o efeito de uma "
            "intervenção."
        )

with aba_beta:
    beta = fxflow.beta_movel(base, janela=janela_beta)
    if beta.empty:
        st.info(f"São necessárias ao menos {janela_beta} semanas de dados para o beta móvel.")
    else:
        st.plotly_chart(
            charts.grafico_series(
                [("Correlação móvel", beta.rename(columns={"correlacao": "valor"}))],
                titulo=f"Correlação móvel entre fluxo e dólar ({janela_beta} semanas)",
                eixo_y="Correlação",
                cores=[charts.AMBAR],
            ),
            use_container_width=True,
        )
        st.plotly_chart(
            charts.grafico_series(
                [("Beta móvel", beta.rename(columns={"beta": "valor"}))],
                titulo="Beta móvel: sensibilidade do dólar ao fluxo",
                eixo_y="Beta (% por US$ milhão)",
                cores=[charts.ROXO],
            ),
            use_container_width=True,
        )
        st.caption(
            "É aqui que se vê **quando** o fluxo explica o câmbio. Trechos em que o beta "
            "cruza o zero marcam regimes em que outro fator assumiu o comando — e são "
            "justamente os períodos em que a regressão cheia engana."
        )

with aba_acumulado:
    if acumulado.empty:
        st.info("Sem dados acumulados para este segmento.")
    else:
        st.plotly_chart(
            charts.grafico_series(
                [
                    ("Acumulado no mês", acumulado.rename(columns={"acum_mes": "valor"})),
                    ("Acumulado no ano", acumulado.rename(columns={"acum_ano": "valor"})),
                ],
                titulo=f"Fluxo {segmento} acumulado",
                eixo_y="US$ milhões",
                cores=[charts.BR, charts.VERDE],
            ),
            use_container_width=True,
        )
        st.dataframe(
            charts.arredondar(acumulado.tail(26), 1),
            use_container_width=True,
            hide_index=True,
        )

with aba_segmentos:
    if resumo.empty:
        st.info("Sem dados por segmento.")
    else:
        st.dataframe(charts.arredondar(resumo, 1), use_container_width=True, hide_index=True)
        st.caption("Saldo das últimas 12 semanas por segmento.")

    largo = fluxo.copy()
    largo["data_ref"] = pd.to_datetime(largo["data_ref"])
    series = [
        (nome, grupo.rename(columns={"saldo": "valor"}))
        for nome, grupo in largo.groupby("segmento")
        if nome != "total"
    ]
    if series:
        st.plotly_chart(
            charts.grafico_series(
                series,
                titulo="Saldo semanal por segmento",
                eixo_y="US$ milhões",
                cores=[charts.VERDE, charts.US],
            ),
            use_container_width=True,
        )
        st.caption(
            "Separar as duas pernas costuma explicar o caso mais desconcertante da mesa: "
            "câmbio subindo apesar de superávit comercial recorde. O comercial entra, mas "
            "o financeiro sai mais."
        )

common.rodape()
