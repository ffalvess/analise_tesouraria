"""Curva de juros do Brasil, do vencimento mais curto ao mais longo."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.ui import charts, common

common.configurar("Curva de juros — Brasil", "🇧🇷")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()
fonte, tipo = common.seletor_fonte_br("br")
data_ref = common.seletor_data("curve_br", "Data", "data_br", fonte, tipo)

if data_ref is None:
    st.stop()

dados = common.cache_curva_br(data_ref, fonte, tipo)
if dados.empty:
    st.warning(f"Sem observações para {fonte}/{tipo} em {data_ref}.")
    st.stop()

curva = curva_mod.build_curve(dados, data_ref, f"{fonte} · {tipo}")
grade = curva_mod.to_grid(curva, metodo=metodo)
metricas = curva_mod.metricas(curva, metodo=metodo)

colunas = st.columns(5)
colunas[0].metric("1 ano", charts.formatar_pct(metricas["curto_1a"]))
colunas[1].metric("5 anos", charts.formatar_pct(metricas["medio_5a"]))
colunas[2].metric("10 anos", charts.formatar_pct(metricas["longo_10a"]))
colunas[3].metric(
    "Inclinação 10a−2a",
    charts.formatar_bps(metricas["inclinacao_10a_2a"] * 100),
    help="Positiva: curva normal. Negativa: curva invertida, o mercado precifica queda de juros.",
)
colunas[4].metric(
    "Curvatura",
    charts.formatar_bps(metricas["curvatura"] * 100),
    help="2×5a − 2a − 10a. Mede a corcova do miolo da curva.",
)

esquerda, direita = st.columns([3, 2])

with esquerda:
    observados = pd.DataFrame({"prazo_anos": curva.prazos, "taxa": curva.taxas})
    st.plotly_chart(
        charts.grafico_curva(
            [("Vértices observados", observados), (f"Interpolada ({metodo})", grade)],
            titulo=f"Curva {tipo} em {data_ref}",
            cores=[charts.CINZA, charts.BR],
        ),
        width="stretch",
    )

with direita:
    st.subheader("Vértices da grade")
    st.dataframe(
        grade[["prazo_anos", "taxa"]]
        .rename(columns={"prazo_anos": "Prazo (anos)", "taxa": "Taxa (% a.a.)"})
        .round(3),
        width="stretch",
        hide_index=True,
        height=380,
    )

st.subheader("Títulos que formam a curva")
tabela = dados[["instrumento", "vencimento", "prazo_anos", "prazo_du", "taxa", "preco"]].copy()
tabela.columns = ["Instrumento", "Vencimento", "Prazo (anos)", "Prazo (d.u.)", "Taxa (%)", "PU"]
st.dataframe(charts.arredondar(tabela, 3), width="stretch", hide_index=True)

# Taxas a termo: separam expectativa de política monetária de prêmio de prazo.
st.subheader("Taxas a termo implícitas")
janelas = [(0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 10.0)]
forwards = pd.DataFrame(
    [
        {
            "Período": f"{inicio:g}a → {fim:g}a",
            "Taxa a termo (% a.a.)": round(curva_mod.forward(curva, inicio, fim, metodo), 3),
        }
        for inicio, fim in janelas
    ]
)
st.dataframe(forwards, width="stretch", hide_index=True)
st.caption(
    "A taxa a termo responde a: que juro o mercado embute para o período entre dois "
    "vértices? Uma sequência de forwards em queda indica corte de juros já precificado."
)

st.subheader("Histórico do vértice")
prazo_alvo = st.slider("Prazo (anos)", 0.5, 15.0, 5.0, 0.5)
historico = common.cache_historico(
    "curve_br", prazo_alvo, 0.4, {"fonte": fonte, "tipo": tipo}
)
if historico.empty:
    st.caption("Sem histórico suficiente perto desse prazo.")
else:
    st.plotly_chart(
        charts.grafico_series(
            [(f"Brasil {prazo_alvo:g}a", historico.rename(columns={"taxa": "valor"}))],
            titulo=f"Taxa de {prazo_alvo:g} anos ao longo do tempo",
            eixo_y="Taxa (% a.a.)",
            sufixo="%",
        ),
        width="stretch",
    )

common.rodape()
