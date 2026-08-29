"""Relatório Focus: para onde o consenso está caminhando.

O valor do Focus não está na projeção de hoje, e sim na **trajetória das
revisões**: uma mediana de Selic que sobe semana após semana já mudou o preço
da curva antes de qualquer decisão do Copom. Por isso a página mostra a
evolução das coletas, e não apenas a última leitura.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from tesouraria.ui import charts, common

common.configurar("Relatório Focus", "🔮")

if not common.exigir_dados():
    st.stop()

geral = common.cache_focus(None, "geral")
if geral.empty:
    st.warning("Sem dados do Focus. Rode `tesouraria ingest --source focus`.")
    st.stop()

indicadores = sorted(geral["indicador"].unique())
indicador = st.sidebar.selectbox("Indicador", indicadores)

anos = sorted(geral[geral["indicador"] == indicador]["data_referencia"].unique())
anos_escolhidos = st.sidebar.multiselect(
    "Anos de referência", anos, default=anos[-3:] if len(anos) >= 3 else anos
)

dados = geral[
    (geral["indicador"] == indicador) & (geral["data_referencia"].isin(anos_escolhidos))
].copy()

if dados.empty:
    st.info("Escolha ao menos um ano de referência.")
    st.stop()

dados["data_coleta"] = pd.to_datetime(dados["data_coleta"])


# ------------------------------------------------------------- último quadro
ultima_coleta = dados["data_coleta"].max()
recente = dados[dados["data_coleta"] == ultima_coleta].sort_values("data_referencia")

st.caption(f"Última coleta disponível: **{ultima_coleta.date()}**")
colunas = st.columns(max(len(recente), 1))
for coluna, linha in zip(colunas, recente.itertuples(), strict=False):
    quatro_semanas = dados[
        (dados["data_referencia"] == linha.data_referencia)
        & (dados["data_coleta"] <= ultima_coleta - pd.Timedelta(days=28))
    ]
    delta = None
    if not quatro_semanas.empty:
        anterior = quatro_semanas.sort_values("data_coleta")["mediana"].iloc[-1]
        delta = f"{linha.mediana - anterior:+.2f} em 4 semanas"
    coluna.metric(f"{indicador} {linha.data_referencia}", f"{linha.mediana:.2f}", delta)


# ------------------------------------------------------------- trajetórias
st.subheader("Trajetória das medianas")
series = [
    (f"{indicador} {ano}", grupo.rename(columns={"data_coleta": "data_ref", "mediana": "valor"}))
    for ano, grupo in dados.groupby("data_referencia")
]
st.plotly_chart(
    charts.grafico_series(
        series,
        titulo=f"Como a projeção de {indicador} evoluiu a cada coleta",
        eixo_y=indicador,
    ),
    use_container_width=True,
)
st.caption(
    "Cada linha é um ano-calendário projetado. Uma linha que sobe consistentemente "
    "significa revisão para cima semana após semana — o sinal que costuma anteceder "
    "movimento da curva."
)


# ---------------------------------------------------------- dispersão e Top5
aba_dispersao, aba_top5, aba_curva = st.tabs(
    ["Dispersão entre analistas", "Focus × Top 5", "Focus × curva de juros"]
)

with aba_dispersao:
    ano_foco = st.selectbox("Ano de referência", anos_escolhidos, key="ano_dispersao")
    recorte = dados[dados["data_referencia"] == ano_foco].sort_values("data_coleta")

    st.plotly_chart(
        charts.grafico_series(
            [
                ("Mediana", recorte.rename(columns={"data_coleta": "data_ref", "mediana": "valor"})),
                ("Mínimo", recorte.rename(columns={"data_coleta": "data_ref", "minimo": "valor"})),
                ("Máximo", recorte.rename(columns={"data_coleta": "data_ref", "maximo": "valor"})),
            ],
            titulo=f"Faixa de projeções para {indicador} {ano_foco}",
            eixo_y=indicador,
            cores=[charts.BR, charts.CINZA, charts.CINZA],
        ),
        use_container_width=True,
    )

    st.plotly_chart(
        charts.grafico_series(
            [("Desvio padrão", recorte.rename(columns={"data_coleta": "data_ref", "desvio": "valor"}))],
            titulo="Dispersão entre os analistas",
            eixo_y="Desvio padrão",
            cores=[charts.AMBAR],
        ),
        use_container_width=True,
    )
    st.caption(
        "Dispersão crescente indica que o mercado deixou de concordar sobre o cenário — "
        "condição que costuma vir acompanhada de maior volatilidade na curva."
    )

with aba_top5:
    top5 = common.cache_focus(indicador, "top5")
    if top5.empty:
        st.info("Sem dados do Top 5 para este indicador.")
    else:
        top5["data_coleta"] = pd.to_datetime(top5["data_coleta"])
        ano_foco = st.selectbox("Ano de referência", anos_escolhidos, key="ano_top5")

        geral_ano = dados[dados["data_referencia"] == ano_foco]
        top5_ano = top5[top5["data_referencia"] == ano_foco]

        st.plotly_chart(
            charts.grafico_series(
                [
                    ("Focus (mediana geral)", geral_ano.rename(columns={"data_coleta": "data_ref", "mediana": "valor"})),
                    ("Top 5", top5_ano.rename(columns={"data_coleta": "data_ref", "mediana": "valor"})),
                ],
                titulo=f"Consenso contra os cinco melhores previsores — {indicador} {ano_foco}",
                eixo_y=indicador,
                cores=[charts.BR, charts.ROXO],
            ),
            use_container_width=True,
        )
        st.caption(
            "Quando o Top 5 se descola do consenso, costuma ser o consenso que se move "
            "depois. A distância entre as duas linhas é um indicador antecedente barato."
        )

with aba_curva:
    st.markdown(
        "Comparação entre o que o **Focus projeta** para a Selic e o que a **curva "
        "precifica** no vértice de um ano. Divergência persistente entre as duas é "
        "oportunidade ou prêmio de risco — e distinguir uma coisa da outra é a decisão."
    )

    focus_selic = common.cache_focus("Selic", "geral")
    fonte_br, _ = common.seletor_fonte_br("focus")
    curva_1a = common.cache_historico("curve_br", 1.0, 0.35, {"fonte": fonte_br, "tipo": "pre"})

    if focus_selic.empty or curva_1a.empty:
        st.info("São necessários o Focus de Selic e o histórico do vértice de 1 ano da curva.")
    else:
        proximo_ano = str(dt.date.today().year + 1)
        recorte = focus_selic[focus_selic["data_referencia"] == proximo_ano].copy()
        if recorte.empty:
            recorte = focus_selic[
                focus_selic["data_referencia"] == str(dt.date.today().year)
            ].copy()
        recorte["data_ref"] = pd.to_datetime(recorte["data_coleta"])
        recorte["valor"] = recorte["mediana"]

        st.plotly_chart(
            charts.grafico_series(
                [
                    ("Focus — Selic projetada", recorte),
                    ("Curva — vértice de 1 ano", curva_1a.rename(columns={"taxa": "valor"})),
                ],
                titulo="O que o Focus espera × o que a curva precifica",
                eixo_y="% a.a.",
                sufixo="%",
                cores=[charts.ROXO, charts.BR],
            ),
            use_container_width=True,
        )
        st.caption(
            "As duas séries não são idênticas por construção: a curva embute prêmio de "
            "prazo e risco, o Focus não. O que importa é a **variação da distância** "
            "entre elas."
        )

common.rodape()
