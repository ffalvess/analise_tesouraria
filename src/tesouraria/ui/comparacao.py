"""Bloco de comparação da mesma curva em datas diferentes.

Vive num módulo próprio porque o Brasil e os Estados Unidos têm, cada um, a
sua página de comparação, e as duas precisam ler o movimento do mesmo jeito.
O que muda de um país para o outro é só como se chega às curvas — fonte e tipo
no Brasil; nominal, real ou breakeven nos EUA — e a frase que interpreta o
movimento.

Duas linhas sobrepostas dizem pouco sozinhas: o que interessa é se o movimento
foi de nível (a curva inteira subiu) ou de inclinação (a ponta curta subiu e a
longa não). Por isso o bloco traz sempre a variação em pontos-base por vértice
ao lado do gráfico.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import pandas as pd
import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.ui import charts

# Prazos que separam a ponta curta da longa na leitura do movimento.
LIMITE_CURTO = 2.0
LIMITE_LONGO = 7.0
# Diferença entre as pontas abaixo da qual o movimento conta como paralelo.
TOLERANCIA_PARALELO_BPS = 15


def leitura_juros(curto: float, longo: float) -> str:
    """Interpretação usual de mesa para uma curva de juros (nominal ou real)."""
    if abs(curto - longo) < TOLERANCIA_PARALELO_BPS:
        return (
            f"Movimento **paralelo**: a curva inteira andou cerca de "
            f"{(curto + longo) / 2:+.0f} bps. Costuma refletir revisão do nível "
            "esperado de juros, e não mudança de cenário para um horizonte específico."
        )
    if curto > longo:
        return (
            f"**Achatamento** (*flattening*): a ponta curta subiu {curto:+.0f} bps "
            f"contra {longo:+.0f} bps da longa. Típico de aperto monetário "
            "precificado ou de piora da expectativa de curto prazo."
        )
    return (
        f"**Inclinação** (*steepening*): a ponta longa subiu {longo:+.0f} bps "
        f"contra {curto:+.0f} bps da curta. Costuma indicar prêmio de prazo maior "
        "— risco fiscal, oferta de títulos ou expectativa de inflação mais alta."
    )


def data_proxima(datas: list[dt.date], alvo: dt.date) -> dt.date:
    """Data disponível igual ou imediatamente anterior ao alvo."""
    anteriores = [d for d in datas if d <= alvo]
    return anteriores[0] if anteriores else datas[-1]


def presets(datas: list[dt.date]) -> dict[str, list[dt.date]]:
    """Comparações rápidas a partir da data mais recente. `datas` vem decrescente."""
    recente = datas[0]

    def antes(dias: int) -> dt.date:
        return data_proxima(datas, recente - dt.timedelta(days=dias))

    return {
        "Último dia": [recente, datas[1] if len(datas) > 1 else recente],
        "1 semana": [recente, antes(7)],
        "1 mês": [recente, antes(30)],
        "3 meses": [recente, antes(91)],
        "Início do ano": [recente, data_proxima(datas, dt.date(recente.year, 1, 1))],
        "12 meses": [recente, antes(365)],
    }


def escolher_datas(datas: list[dt.date], chave: str) -> list[dt.date]:
    """Rádio de comparação rápida, com a escolha manual como alternativa."""
    opcoes = presets(datas)
    preset = st.radio(
        "Comparação rápida",
        ["Escolher manualmente", *opcoes],
        horizontal=True,
        index=3,
        key=f"{chave}_preset",
    )
    if preset == "Escolher manualmente":
        selecionadas = st.multiselect(
            "Datas a sobrepor",
            datas,
            default=opcoes["1 mês"],
            help="Escolha quantas datas quiser; a mais antiga sai em tom mais claro.",
            key=f"{chave}_datas",
        )
    else:
        selecionadas = opcoes[preset]
    return sorted(set(selecionadas))


def comparar(
    datas: list[dt.date],
    carregar: Callable[[dt.date], curva_mod.Curva | None],
    descricao: str,
    cor_base: str,
    metodo: str,
    chave: str,
    leitura: Callable[[float, float], str] = leitura_juros,
    eixo_y: str = "Taxa (% a.a.)",
    limite_curto: float = LIMITE_CURTO,
    limite_longo: float = LIMITE_LONGO,
) -> list[curva_mod.Curva]:
    """Desenha sobreposição, variação por vértice, leitura e mapa de variações.

    Devolve as curvas efetivamente desenhadas, da mais antiga para a mais
    recente, para a página acrescentar o que for específico do país.

    `limite_curto` e `limite_longo` existem para curvas que não começam no
    curto prazo: a TIPS nasce em 5 anos e, como não extrapolamos, a régua
    padrão deixaria a ponta curta vazia e a leitura nunca apareceria.
    """
    if not datas:
        st.warning("Sem datas disponíveis para esta curva.")
        return []

    st.caption(f"Curva selecionada: **{descricao}** · {len(datas)} datas com dados.")

    selecionadas = escolher_datas(datas, chave)
    if not selecionadas:
        st.info("Escolha ao menos uma data.")
        return []

    objetos: list[curva_mod.Curva] = []
    for data_ref in selecionadas:
        curva = carregar(data_ref)
        if curva is not None and not curva.vazia:
            objetos.append(curva)

    if not objetos:
        st.warning("Nenhuma das datas escolhidas tem observações.")
        return []

    st.plotly_chart(
        charts.grafico_curva(
            [(str(c.data_ref), curva_mod.to_grid(c, metodo=metodo)) for c in objetos],
            titulo=f"{descricao} — {len(objetos)} datas sobrepostas",
            cores=charts.degrade(len(objetos), cor_base),
            eixo_y=eixo_y,
        ),
        width="stretch",
    )

    if len(objetos) < 2:
        return objetos

    st.subheader("Variação por vértice")

    antiga, recente = objetos[0], objetos[-1]
    variacao = curva_mod.variacao_bps(antiga, recente, metodo=metodo)

    esquerda, direita = st.columns([2, 3])

    with esquerda:
        exibir = variacao.copy()
        exibir.columns = ["Prazo (anos)", str(antiga.data_ref), str(recente.data_ref), "Δ (bps)"]
        st.dataframe(charts.arredondar(exibir), width="stretch", hide_index=True, height=420)

    with direita:
        st.plotly_chart(
            charts.grafico_curva(
                [("Variação", variacao.rename(columns={"variacao_bps": "taxa"}))],
                titulo=f"{antiga.data_ref} → {recente.data_ref}",
                cores=[charts.VERDE],
                eixo_y="Variação (bps)",
            ),
            width="stretch",
        )

    # Leitura automática do movimento: nível contra inclinação.
    curto = variacao[variacao["prazo_anos"] <= limite_curto]["variacao_bps"].mean()
    longo = variacao[variacao["prazo_anos"] >= limite_longo]["variacao_bps"].mean()
    if pd.notna(curto) and pd.notna(longo):
        st.info(leitura(curto, longo), icon="📐")

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
            width="stretch",
        )

    return objetos
