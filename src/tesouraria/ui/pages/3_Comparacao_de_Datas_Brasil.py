"""Comparação da curva brasileira em datas diferentes.

A lógica da comparação está em `ui/comparacao.py`, compartilhada com a página
dos Estados Unidos; aqui fica só a escolha de fonte e tipo da curva.
"""

from __future__ import annotations

import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.ui import charts, common, comparacao

common.configurar("Comparação entre datas — Brasil", "🗓️")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()
fonte, tipo = common.seletor_fonte_br("cmp")


def carregar(data_ref):
    return curva_mod.build_curve(
        common.cache_curva_br(data_ref, fonte, tipo), data_ref, str(data_ref)
    )


comparacao.comparar(
    common.cache_datas("curve_br", fonte, tipo),
    carregar,
    descricao=f"Brasil · {fonte} · {tipo}",
    cor_base=charts.BR,
    metodo=metodo,
    chave="cmp_br",
)

common.rodape()
