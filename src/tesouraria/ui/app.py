"""Painel — visão geral do dia.

Responde de relance às três perguntas que abrem o dia numa mesa: como estão as
duas curvas, quanto vale o carrego entre elas, e o que o fluxo cambial fez na
última semana.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.analytics import differentials as dif
from tesouraria.ui import charts, common

common.configurar("Painel", "🧭")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()
fonte_br, tipo_br = common.seletor_fonte_br("painel")

datas_br = common.cache_datas("curve_br", fonte_br, tipo_br)
datas_us = common.cache_datas("curve_us", tipo="nominal")

if not datas_br or not datas_us:
    st.warning("Faltam dados de curva para montar o painel.")
    st.stop()

data_br, data_us = datas_br[0], datas_us[0]

br = common.cache_curva_br(data_br, fonte_br, tipo_br)
us = common.cache_curva_us(data_us, "nominal")

curva_br = dif.curva_efetiva(br, "BR", f"Brasil {data_br}", data_br)
curva_us = dif.curva_efetiva(us, "US", f"EUA {data_us}", data_us)

diferencial = dif.diferencial_por_vertice(curva_br, curva_us, metodo=metodo)


# ------------------------------------------------------- indicadores do topo
def _no_vertice(prazo: float, coluna: str) -> float:
    linha = diferencial[np.isclose(diferencial["prazo_anos"], prazo)]
    return float(linha[coluna].iloc[0]) if not linha.empty else float("nan")


cambio = common.cache_serie("1")
ultimo_cambio = float(cambio["valor"].iloc[-1]) if not cambio.empty else float("nan")
variacao_cambio = (
    float(cambio["valor"].iloc[-1] / cambio["valor"].iloc[-2] - 1) * 100
    if len(cambio) > 1
    else float("nan")
)

fluxo = common.cache_fluxo()
fluxo_total = fluxo[fluxo["segmento"] == "total"] if not fluxo.empty else pd.DataFrame()
ultimo_fluxo = (
    float(pd.to_numeric(fluxo_total["saldo"], errors="coerce").iloc[-1])
    if not fluxo_total.empty
    else float("nan")
)

colunas = st.columns(5)
colunas[0].metric(
    "Dólar (PTAX)",
    f"R$ {ultimo_cambio:.4f}" if np.isfinite(ultimo_cambio) else "—",
    f"{variacao_cambio:+.2f}%" if np.isfinite(variacao_cambio) else None,
)
colunas[1].metric("Diferencial 2 anos", charts.formatar_bps(_no_vertice(2.0, "diferencial_bps")))
colunas[2].metric("Diferencial 5 anos", charts.formatar_bps(_no_vertice(5.0, "diferencial_bps")))
colunas[3].metric("Diferencial 10 anos", charts.formatar_bps(_no_vertice(10.0, "diferencial_bps")))
colunas[4].metric(
    "Fluxo cambial (semana)",
    f"US$ {ultimo_fluxo:,.0f} mi".replace(",", ".") if np.isfinite(ultimo_fluxo) else "—",
    help="Saldo entre compras e vendas de moeda estrangeira. Positivo = entrada líquida.",
)

st.caption(
    f"Curvas de {data_br} (Brasil, {fonte_br}) e {data_us} (EUA). "
    "Diferenciais calculados sobre taxas efetivas anuais, com as duas convenções "
    "já normalizadas."
)


# --------------------------------------------------------------- as curvas
esquerda, direita = st.columns(2)

with esquerda:
    st.plotly_chart(
        charts.grafico_curva(
            [(f"Brasil · {tipo_br}", curva_mod.to_grid(curva_br, metodo=metodo))],
            titulo="Curva de juros — Brasil",
            cores=[charts.BR],
        ),
        use_container_width=True,
    )

with direita:
    st.plotly_chart(
        charts.grafico_curva(
            [("EUA · nominal", curva_mod.to_grid(curva_us, metodo=metodo))],
            titulo="Curva de juros — Estados Unidos",
            cores=[charts.US],
        ),
        use_container_width=True,
    )


# ------------------------------------------------------- diferencial e tom
esquerda, direita = st.columns([3, 2])

with esquerda:
    grafico = charts.grafico_curva(
        [("Diferencial BR − EUA", diferencial.rename(columns={"diferencial_pp": "taxa"}))],
        titulo="Diferencial de juros por vértice",
        cores=[charts.VERDE],
        eixo_y="Diferencial (p.p.)",
    )
    st.plotly_chart(grafico, use_container_width=True)

with direita:
    documentos = common.cache_documentos(desde=dt.date.today() - dt.timedelta(days=90), limite=8)
    st.subheader("Comunicação recente")
    if documentos.empty:
        st.caption("Nenhum discurso ou relatório ingerido nos últimos 90 dias.")
    else:
        for linha in documentos.itertuples():
            tom = linha.score_tom
            marcador = "🔴" if tom and tom > 0.25 else ("🟢" if tom and tom < -0.25 else "⚪")
            st.markdown(
                f"{marcador} **{linha.instituicao}** · {linha.data_pub} — "
                f"[{(linha.titulo or '')[:90]}]({linha.url})  \n"
                f"<span style='color:#7A8798;font-size:0.85em'>tom {tom:+.2f}</span>",
                unsafe_allow_html=True,
            )
        st.caption("🔴 hawkish · 🟢 dovish · ⚪ neutro")

st.info(
    "**Como ler o painel.** O diferencial é o prêmio por manter risco Brasil em vez de "
    "Treasury. Quando ele se abre e o fluxo cambial acompanha, o real tende a se "
    "apreciar; quando o diferencial se mantém alto mas o fluxo é negativo, o câmbio "
    "costuma estar respondendo a risco fiscal ou externo, não a carrego. As páginas "
    "*Diferencial* e *Fluxo cambial* detalham cada uma dessas leituras.",
    icon="💡",
)

common.rodape()
