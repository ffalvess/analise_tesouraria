"""Câmbio USD/BRL — por que o dólar se mexeu e em que regime estamos.

Atribuição e contexto, não previsão. Quatro blocos nesta fase: painel do dia,
cesta de commodities Brasil, atribuição diária a fatores ortogonalizados e
calendário dos próximos dias úteis. A lógica está em `ui/cambio.py` e em
`analytics/atribuicao.py`.
"""

from __future__ import annotations

import streamlit as st

from tesouraria.ui import cambio, common

common.configurar("Câmbio USD/BRL", "💱")

if not common.exigir_dados():
    st.stop()

st.caption(cambio.AVISO_ESCOPO)

cambio.botao_atualizar()
conf = cambio.cfg()["atribuicao"]
opcoes = sorted(set(conf["janelas_oferecidas"]) | {conf["janela"]})
janela = st.sidebar.selectbox(
    "Janela da regressão (sessões)",
    opcoes,
    index=opcoes.index(conf["janela"]),
    help="Sessões usadas para estimar betas e desvio do resíduo. Padrão em config/cambio.yaml.",
)

modelo = cambio.calcular_modelo(int(janela))

aba_painel, aba_cesta, aba_atribuicao, aba_calendario = st.tabs(
    ["Painel do dia", "Cesta de commodities", "Atribuição", "Calendário"]
)
with aba_painel:
    cambio.secao_painel(modelo)
with aba_cesta:
    cambio.secao_commodities(modelo)
with aba_atribuicao:
    cambio.secao_atribuicao(modelo, int(janela))
with aba_calendario:
    cambio.secao_calendario()

common.rodape()
