"""Comparação da mesma curva em datas diferentes.

Duas linhas sobrepostas dizem pouco sozinhas: o que interessa é se o movimento
foi de nível (a curva inteira subiu) ou de inclinação (a ponta curta subiu e a
longa não). Por isso a página traz sempre a variação em pontos-base por
vértice ao lado do gráfico.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.ui import charts, common

common.configurar("Comparação entre datas", "🗓️")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()

pais = st.sidebar.radio("Curva", ["Brasil", "Estados Unidos"], horizontal=True)

if pais == "Brasil":
    fonte, tipo = common.seletor_fonte_br("cmp")
    datas = common.cache_datas("curve_br", fonte, tipo)
    cor_base = charts.BR
    carregar = lambda d: common.cache_curva_br(d, fonte, tipo)  # noqa: E731
    descricao = f"Brasil · {fonte} · {tipo}"
else:
    tipo_us = st.sidebar.selectbox(
        "Tipo", ["nominal", "real"], format_func=lambda t: {"nominal": "Nominal", "real": "Real (TIPS)"}[t]
    )
    datas = common.cache_datas("curve_us", tipo=tipo_us)
    cor_base = charts.US
    carregar = lambda d: common.cache_curva_us(d, tipo_us)  # noqa: E731
    descricao = f"Estados Unidos · {tipo_us}"

if not datas:
    st.warning("Sem datas disponíveis para esta curva.")
    st.stop()


# ------------------------------------------------------------------ presets
def data_proxima(alvo: dt.date) -> dt.date:
    """Data disponível igual ou imediatamente anterior ao alvo."""
    anteriores = [d for d in datas if d <= alvo]
    return anteriores[0] if anteriores else datas[-1]


mais_recente = datas[0]
PRESETS = {
    "Último dia": [mais_recente, datas[1] if len(datas) > 1 else mais_recente],
    "1 semana": [mais_recente, data_proxima(mais_recente - dt.timedelta(days=7))],
    "1 mês": [mais_recente, data_proxima(mais_recente - dt.timedelta(days=30))],
    "3 meses": [mais_recente, data_proxima(mais_recente - dt.timedelta(days=91))],
    "Início do ano": [mais_recente, data_proxima(dt.date(mais_recente.year, 1, 1))],
    "12 meses": [mais_recente, data_proxima(mais_recente - dt.timedelta(days=365))],
}

st.caption(f"Curva selecionada: **{descricao}** · {len(datas)} datas com dados.")

preset = st.radio(
    "Comparação rápida", ["Escolher manualmente", *PRESETS], horizontal=True, index=3
)

if preset == "Escolher manualmente":
    selecionadas = st.multiselect(
        "Datas a sobrepor",
        datas,
        default=PRESETS["1 mês"],
        help="Escolha quantas datas quiser; a mais antiga sai em tom mais claro.",
    )
else:
    selecionadas = PRESETS[preset]

selecionadas = sorted(set(selecionadas))
if len(selecionadas) < 1:
    st.info("Escolha ao menos uma data.")
    st.stop()


# ------------------------------------------------------------------- curvas
curvas: list[tuple[str, pd.DataFrame]] = []
objetos: list[curva_mod.Curva] = []
for data_ref in selecionadas:
    dados = carregar(data_ref)
    if dados.empty:
        continue
    curva = curva_mod.build_curve(dados, data_ref, str(data_ref))
    objetos.append(curva)
    curvas.append((str(data_ref), curva_mod.to_grid(curva, metodo=metodo)))

if not curvas:
    st.warning("Nenhuma das datas escolhidas tem observações.")
    st.stop()

st.plotly_chart(
    charts.grafico_curva(
        curvas,
        titulo=f"{descricao} — {len(curvas)} datas sobrepostas",
        cores=charts.degrade(len(curvas), cor_base),
    ),
    use_container_width=True,
)


# --------------------------------------------------------------- variações
if len(objetos) >= 2:
    st.subheader("Variação por vértice")

    antiga, recente = objetos[0], objetos[-1]
    variacao = curva_mod.variacao_bps(antiga, recente, metodo=metodo)

    esquerda, direita = st.columns([2, 3])

    with esquerda:
        exibir = variacao.copy()
        exibir.columns = ["Prazo (anos)", str(antiga.data_ref), str(recente.data_ref), "Δ (bps)"]
        st.dataframe(charts.arredondar(exibir), use_container_width=True, hide_index=True, height=420)

    with direita:
        st.plotly_chart(
            charts.grafico_curva(
                [("Variação", variacao.rename(columns={"variacao_bps": "taxa"}))],
                titulo=f"{antiga.data_ref} → {recente.data_ref}",
                cores=[charts.VERDE],
                eixo_y="Variação (bps)",
            ),
            use_container_width=True,
        )

    # Leitura automática do movimento: nível contra inclinação.
    curto = variacao[variacao["prazo_anos"] <= 2]["variacao_bps"].mean()
    longo = variacao[variacao["prazo_anos"] >= 7]["variacao_bps"].mean()
    if pd.notna(curto) and pd.notna(longo):
        if abs(curto - longo) < 15:
            leitura = (
                f"Movimento **paralelo**: a curva inteira andou cerca de "
                f"{(curto + longo) / 2:+.0f} bps. Costuma refletir revisão do nível "
                "esperado de juros, e não mudança de cenário para um horizonte específico."
            )
        elif curto > longo:
            leitura = (
                f"**Achatamento** (*flattening*): a ponta curta subiu {curto:+.0f} bps "
                f"contra {longo:+.0f} bps da longa. Típico de aperto monetário "
                "precificado ou de piora da expectativa de curto prazo."
            )
        else:
            leitura = (
                f"**Inclinação** (*steepening*): a ponta longa subiu {longo:+.0f} bps "
                f"contra {curto:+.0f} bps da curta. Costuma indicar prêmio de prazo maior "
                "— risco fiscal, oferta de títulos ou expectativa de inflação mais alta."
            )
        st.info(leitura, icon="📐")

    if len(objetos) > 2:
        st.subheader("Mapa de variações")
        base = objetos[0]
        linhas = {}
        for curva in objetos[1:]:
            comparacao = curva_mod.variacao_bps(base, curva, metodo=metodo)
            linhas[str(curva.data_ref)] = comparacao.set_index("prazo_anos")["variacao_bps"]
        matriz = pd.DataFrame(linhas).T
        st.plotly_chart(
            charts.heatmap_variacao(matriz, titulo=f"Variação contra {base.data_ref} (bps)"),
            use_container_width=True,
        )

common.rodape()
