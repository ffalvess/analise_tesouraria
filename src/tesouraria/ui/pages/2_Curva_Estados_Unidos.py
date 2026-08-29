"""Curva de juros dos Estados Unidos: nominal, real (TIPS) e inflação implícita."""

from __future__ import annotations

import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.analytics import differentials as dif
from tesouraria.ui import charts, common

common.configurar("Curva de juros — Estados Unidos", "🇺🇸")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()
data_ref = common.seletor_data("curve_us", "Data", "data_us", tipo="nominal")

if data_ref is None:
    st.stop()

nominal = common.cache_curva_us(data_ref, "nominal")
real = common.cache_curva_us(data_ref, "real")

if nominal.empty:
    st.warning(f"Sem curva nominal em {data_ref}.")
    st.stop()

curva_nominal = curva_mod.build_curve(nominal, data_ref, "Nominal")
curva_real = curva_mod.build_curve(real, data_ref, "Real (TIPS)") if not real.empty else None
metricas = curva_mod.metricas(curva_nominal, metodo=metodo)

colunas = st.columns(5)
colunas[0].metric("1 ano", charts.formatar_pct(metricas["curto_1a"]))
colunas[1].metric("5 anos", charts.formatar_pct(metricas["medio_5a"]))
colunas[2].metric("10 anos", charts.formatar_pct(metricas["longo_10a"]))
colunas[3].metric(
    "Inclinação 10a−2a",
    charts.formatar_bps(metricas["inclinacao_10a_2a"] * 100),
    help="A inversão dessa inclinação é o indicador de recessão mais acompanhado do mercado.",
)
colunas[4].metric("Curvatura", charts.formatar_bps(metricas["curvatura"] * 100))

curvas = [("Nominal", curva_mod.to_grid(curva_nominal, metodo=metodo))]
cores = [charts.US]
if curva_real is not None:
    curvas.append(("Real (TIPS)", curva_mod.to_grid(curva_real, metodo=metodo)))
    cores.append(charts.ROXO)

st.plotly_chart(
    charts.grafico_curva(curvas, titulo=f"Curva americana em {data_ref}", cores=cores),
    use_container_width=True,
)

if curva_real is not None:
    st.subheader("Inflação implícita (breakeven)")
    breakeven = dif.inflacao_implicita(curva_nominal, curva_real, metodo=metodo)
    esquerda, direita = st.columns([3, 2])
    with esquerda:
        st.plotly_chart(
            charts.grafico_curva(
                [("Breakeven", breakeven.rename(columns={"implicita": "taxa"}))],
                titulo="Inflação implícita por vértice",
                cores=[charts.VERDE],
                eixo_y="Inflação implícita (% a.a.)",
            ),
            use_container_width=True,
        )
    with direita:
        st.dataframe(
            breakeven.dropna(subset=["implicita"])
            .rename(
                columns={
                    "prazo_anos": "Prazo (anos)",
                    "nominal": "Nominal (%)",
                    "real": "Real (%)",
                    "implicita": "Implícita (%)",
                }
            )
            .round(3),
            use_container_width=True,
            hide_index=True,
            height=340,
        )
    st.caption(
        "O breakeven usa a relação de Fisher exata — (1+nominal)/(1+real) − 1 — e não a "
        "subtração simples. É a inflação que o mercado precisa ver para que comprar "
        "nominal e comprar TIPS dê o mesmo retorno."
    )
else:
    st.caption("Curva real (TIPS) não ingerida para esta data; o breakeven fica indisponível.")

st.subheader("Vértices observados")
tabela = nominal[["tenor", "prazo_anos", "taxa"]].copy()
tabela.columns = ["Vencimento", "Prazo (anos)", "Taxa (% a.a.)"]
st.dataframe(charts.arredondar(tabela, 3), use_container_width=True, hide_index=True)

st.subheader("Histórico do vértice")
prazo_alvo = st.slider("Prazo (anos)", 0.25, 30.0, 10.0, 0.25)
historico = common.cache_historico("curve_us", prazo_alvo, 0.3, {"tipo": "nominal"})
if historico.empty:
    st.caption("Sem histórico perto desse prazo.")
else:
    st.plotly_chart(
        charts.grafico_series(
            [(f"EUA {prazo_alvo:g}a", historico.rename(columns={"taxa": "valor"}))],
            titulo=f"Treasury de {prazo_alvo:g} anos ao longo do tempo",
            eixo_y="Taxa (% a.a.)",
            sufixo="%",
            cores=[charts.US],
        ),
        use_container_width=True,
    )

st.caption(
    "As taxas desta página são *par yields* em convenção semestral, como o Tesouro "
    "americano publica. A conversão para taxa efetiva anual só é aplicada na página de "
    "comparação, onde as duas curvas precisam estar na mesma base."
)

common.rodape()
