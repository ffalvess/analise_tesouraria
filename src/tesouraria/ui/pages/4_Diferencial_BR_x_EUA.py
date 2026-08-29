"""Diferencial entre as curvas brasileira e americana — o preço do carrego.

Esta é a página central do aplicativo: quanto um investidor ganha por manter
risco Brasil em vez de Treasury, como esse prêmio se distribui ao longo da
curva, e como ele se move junto com o dólar.

Todas as contas usam taxa efetiva anual. A curva brasileira é base 252 dias
úteis; o *par yield* americano é semestral. Sem essa conversão, o diferencial
sairia errado em mais de 5 pontos-base já em juros de 4,5% ao ano.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from tesouraria.analytics import differentials as dif
from tesouraria.ui import charts, common

common.configurar("Diferencial de juros — Brasil × EUA", "⚖️")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()
fonte_br, _ = common.seletor_fonte_br("dif")

datas_br = common.cache_datas("curve_br", fonte_br, "pre")
datas_us = common.cache_datas("curve_us", tipo="nominal")

if not datas_br or not datas_us:
    st.warning("São necessárias as duas curvas para calcular o diferencial.")
    st.stop()

data_br = st.sidebar.selectbox("Data (Brasil)", datas_br, index=0)
data_us = st.sidebar.selectbox("Data (EUA)", datas_us, index=0)

if data_br != data_us:
    st.caption(
        f"⚠️ As datas escolhidas são diferentes ({data_br} contra {data_us}). "
        "O diferencial só é comparável quando as duas curvas são do mesmo dia."
    )

curva_br = dif.curva_efetiva(
    common.cache_curva_br(data_br, fonte_br, "pre"), "BR", "Brasil pré", data_br
)
curva_us = dif.curva_efetiva(
    common.cache_curva_us(data_us, "nominal"), "US", "EUA nominal", data_us
)

nominal = dif.diferencial_por_vertice(curva_br, curva_us, metodo=metodo)


def _vertice(prazo: float, coluna: str = "diferencial_bps") -> float:
    linha = nominal[np.isclose(nominal["prazo_anos"], prazo)]
    return float(linha[coluna].iloc[0]) if not linha.empty else float("nan")


colunas = st.columns(4)
for coluna, prazo in zip(colunas, [1.0, 2.0, 5.0, 10.0], strict=True):
    coluna.metric(f"Diferencial {prazo:g}a", charts.formatar_bps(_vertice(prazo)))

aba_nominal, aba_real, aba_historico, aba_carry = st.tabs(
    ["Nominal", "Real (IPCA+ × TIPS)", "Histórico", "Carrego de curto prazo"]
)


# ------------------------------------------------------------------ nominal
with aba_nominal:
    esquerda, direita = st.columns([3, 2])

    with esquerda:
        st.plotly_chart(
            charts.grafico_curva(
                [
                    ("Brasil (efetiva)", nominal.rename(columns={"taxa_br": "taxa"})),
                    ("EUA (efetiva)", nominal.rename(columns={"taxa_us": "taxa"})),
                ],
                titulo="As duas curvas na mesma base",
                cores=[charts.BR, charts.US],
            ),
            use_container_width=True,
        )
        st.plotly_chart(
            charts.grafico_curva(
                [("Diferencial", nominal.rename(columns={"diferencial_pp": "taxa"}))],
                titulo="Diferencial por vértice",
                cores=[charts.VERDE],
                eixo_y="Diferencial (p.p.)",
            ),
            use_container_width=True,
        )

    with direita:
        exibir = nominal[["prazo_anos", "taxa_br", "taxa_us", "diferencial_bps"]].copy()
        exibir.columns = ["Prazo (anos)", "Brasil (%)", "EUA (%)", "Δ (bps)"]
        st.dataframe(charts.arredondar(exibir), use_container_width=True, hide_index=True, height=460)
        st.caption(
            "Vértices em branco são aqueles em que uma das curvas não tem observação. "
            "O aplicativo não extrapola: um diferencial ausente é informação diferente "
            "de um diferencial nulo."
        )


# --------------------------------------------------------------------- real
with aba_real:
    br_real = common.cache_curva_br(data_br, fonte_br, "ipca")
    us_real = common.cache_curva_us(data_us, "real")

    if br_real.empty or us_real.empty:
        st.info(
            "Faltam dados para o diferencial real: são necessárias a curva IPCA+ do "
            "Brasil e a curva TIPS dos Estados Unidos na data escolhida."
        )
    else:
        curva_br_real = dif.curva_efetiva(br_real, "BR", "Brasil IPCA+", data_br)
        curva_us_real = dif.curva_efetiva(us_real, "US", "EUA TIPS", data_us)
        real = dif.diferencial_por_vertice(curva_br_real, curva_us_real, metodo=metodo)

        esquerda, direita = st.columns([3, 2])
        with esquerda:
            st.plotly_chart(
                charts.grafico_curva(
                    [
                        ("Brasil IPCA+", real.rename(columns={"taxa_br": "taxa"})),
                        ("EUA TIPS", real.rename(columns={"taxa_us": "taxa"})),
                    ],
                    titulo="Juro real: Brasil × Estados Unidos",
                    cores=[charts.BR, charts.ROXO],
                ),
                use_container_width=True,
            )
        with direita:
            exibir = real[["prazo_anos", "taxa_br", "taxa_us", "diferencial_bps"]].copy()
            exibir.columns = ["Prazo (anos)", "Brasil (%)", "EUA (%)", "Δ (bps)"]
            st.dataframe(charts.arredondar(exibir), use_container_width=True, hide_index=True, height=420)

        st.caption(
            "O diferencial real é o carrego limpo de inflação esperada — é ele, e não o "
            "nominal, que sustenta posições de câmbio de prazo mais longo."
        )

        implicita_br = dif.inflacao_implicita(curva_br, curva_br_real, metodo=metodo)
        implicita_us = dif.inflacao_implicita(curva_us, curva_us_real, metodo=metodo)
        st.plotly_chart(
            charts.grafico_curva(
                [
                    ("Implícita Brasil", implicita_br.rename(columns={"implicita": "taxa"})),
                    ("Breakeven EUA", implicita_us.rename(columns={"implicita": "taxa"})),
                ],
                titulo="Inflação implícita nos dois países",
                cores=[charts.BR, charts.US],
                eixo_y="Inflação implícita (% a.a.)",
            ),
            use_container_width=True,
        )


# ---------------------------------------------------------------- histórico
with aba_historico:
    prazo = st.select_slider("Vértice", options=[1.0, 2.0, 3.0, 5.0, 7.0, 10.0], value=5.0)
    tolerancia = 0.5

    hist_br = common.cache_historico(
        "curve_br", prazo, tolerancia, {"fonte": fonte_br, "tipo": "pre"}
    )
    hist_us = common.cache_historico("curve_us", prazo, tolerancia, {"tipo": "nominal"})
    serie = dif.serie_diferencial(hist_br, hist_us)

    if serie.empty:
        st.info("Sem histórico coincidente entre as duas curvas nesse vértice.")
    else:
        st.plotly_chart(
            charts.grafico_series(
                [
                    ("Brasil", serie.rename(columns={"taxa_br": "valor"})),
                    ("EUA", serie.rename(columns={"taxa_us": "valor"})),
                ],
                titulo=f"Taxas de {prazo:g} anos, em base efetiva anual",
                eixo_y="Taxa (% a.a.)",
                sufixo="%",
                cores=[charts.BR, charts.US],
            ),
            use_container_width=True,
        )
        st.plotly_chart(
            charts.grafico_series(
                [("Diferencial", serie.rename(columns={"diferencial_pp": "valor"}))],
                titulo=f"Diferencial de {prazo:g} anos ao longo do tempo",
                eixo_y="Diferencial (p.p.)",
                sufixo=" p.p.",
                cores=[charts.VERDE],
            ),
            use_container_width=True,
        )

        atual = serie["diferencial_pp"].iloc[-1]
        media = serie["diferencial_pp"].mean()
        desvio = serie["diferencial_pp"].std()
        z = (atual - media) / desvio if desvio else float("nan")

        colunas = st.columns(4)
        colunas[0].metric("Diferencial atual", f"{atual:.2f} p.p.")
        colunas[1].metric("Média do período", f"{media:.2f} p.p.")
        colunas[2].metric("Desvio padrão", f"{desvio:.2f} p.p.")
        colunas[3].metric(
            "Desvio em z",
            f"{z:+.2f}" if np.isfinite(z) else "—",
            help="Quantos desvios padrão o diferencial de hoje está da sua média histórica.",
        )

        # Correlação com o câmbio: o sinal esperado é negativo.
        cambio = common.cache_serie("1")
        correlacao = dif.correlacao_com_cambio(serie, cambio)
        if not correlacao.empty and correlacao["correlacao"].notna().any():
            st.plotly_chart(
                charts.grafico_series(
                    [("Correlação móvel (63 pregões)", correlacao.rename(columns={"correlacao": "valor"}))],
                    titulo="Correlação entre o diferencial e o dólar",
                    eixo_y="Correlação",
                    cores=[charts.AMBAR],
                ),
                use_container_width=True,
            )
            st.caption(
                "O sinal esperado é **negativo**: diferencial maior atrai capital e derruba "
                "o dólar. Trechos com correlação positiva costumam ser períodos em que risco "
                "fiscal ou externo passou a dominar o carrego."
            )


# -------------------------------------------------------------------- carry
with aba_carry:
    cdi = common.cache_serie("12")
    fed = common.cache_serie("DFF")
    if fed.empty:
        fed = common.cache_serie("FEDFUNDS")

    if cdi.empty or fed.empty:
        st.info(
            "O carrego de curto prazo precisa do CDI (série 12 do SGS) e do Fed Funds "
            "(FRED). Sem `FRED_API_KEY` configurada, as séries americanas não são coletadas."
        )
    else:
        carry = dif.carry_curto(cdi, fed)
        if carry.empty:
            st.info("Não houve sobreposição de datas entre as duas séries.")
        else:
            st.plotly_chart(
                charts.grafico_series(
                    [
                        ("CDI anualizado", carry.rename(columns={"cdi_anual": "valor", "data_ref": "data_ref"})),
                        ("Fed Funds", carry.rename(columns={"fed_funds": "valor"})),
                    ],
                    titulo="Juro de curtíssimo prazo nos dois países",
                    eixo_y="Taxa (% a.a.)",
                    sufixo="%",
                    cores=[charts.BR, charts.US],
                ),
                use_container_width=True,
            )
            st.plotly_chart(
                charts.grafico_series(
                    [("Carrego", carry.rename(columns={"carry_pp": "valor"}))],
                    titulo="Carrego: CDI menos Fed Funds",
                    eixo_y="Diferencial (p.p.)",
                    sufixo=" p.p.",
                    cores=[charts.VERDE],
                ),
                use_container_width=True,
            )
            ultimo = carry.iloc[-1]
            st.metric(
                f"Carrego em {pd.to_datetime(ultimo['data_ref']).date()}",
                f"{ultimo['carry_pp']:.2f} p.p.",
                help="CDI anualizado em base 252 menos a taxa efetiva do Fed Funds.",
            )

st.caption(
    f"Página gerada com dados até {max(datas_br[0], datas_us[0])}. "
    f"Interpolação: {metodo}."
)

common.rodape()
