"""Comparação da curva americana em datas diferentes.

Além da nominal e da real (TIPS), compara a inflação implícita (breakeven)
entre datas — é a forma mais direta de ver se o mercado revisou a inflação
esperada ou só o juro real. Na nominal, fecha com a inclinação 10a−2a de cada
data, o indicador de inversão que a mesa americana acompanha.

A TIPS só existe a partir de 5 anos, então real e breakeven leem a ponta curta
no vértice de 5 anos e a longa a partir de 20.

A lógica comum da comparação está em `ui/comparacao.py`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from tesouraria.analytics import curve as curva_mod
from tesouraria.analytics import differentials as dif
from tesouraria.ui import charts, common, comparacao

common.configurar("Comparação entre datas — Estados Unidos", "🗓️")

if not common.exigir_dados():
    st.stop()

metodo = common.seletor_metodo()

TIPOS = {
    "nominal": "Nominal",
    "real": "Real (TIPS)",
    "breakeven": "Inflação implícita (breakeven)",
}
tipo = st.sidebar.selectbox("Tipo", list(TIPOS), format_func=TIPOS.get, key="cmp_us_tipo")


def leitura_breakeven(curto: float, longo: float) -> str:
    """A mesma régua de nível contra inclinação, lida como expectativa de inflação."""
    if abs(curto - longo) < comparacao.TOLERANCIA_PARALELO_BPS:
        if abs(curto + longo) / 2 < 5:
            return (
                "Inflação implícita **praticamente estável** em todos os prazos: o que "
                "mexeu na curva nominal no período foi o juro real, não a inflação esperada."
            )
        return (
            f"Revisão **paralela** da inflação esperada: cerca de {(curto + longo) / 2:+.0f} bps "
            "em todos os prazos. O mercado mudou o nível de inflação que espera, não o "
            "horizonte em que ela acontece."
        )
    if curto > longo:
        return (
            f"A inflação implícita **curta** andou {curto:+.0f} bps contra {longo:+.0f} bps "
            "da longa. Leitura de choque transitório — energia, tarifas, um CPI fora da "
            "curva — com a expectativa de longo prazo preservada."
        )
    return (
        f"A inflação implícita **longa** andou {longo:+.0f} bps contra {curto:+.0f} bps "
        "da curta. É o movimento que o Fed mais vigia: sinal de que a âncora de longo "
        "prazo está cedendo, e não só de um repique pontual."
    )


def curva_us(data_ref: dt.date, tipo_us: str) -> curva_mod.Curva:
    return curva_mod.build_curve(common.cache_curva_us(data_ref, tipo_us), data_ref, str(data_ref))


if tipo == "breakeven":
    # Só há breakeven nos dias com as duas curvas publicadas.
    reais = set(common.cache_datas("curve_us", tipo="real"))
    datas = [d for d in common.cache_datas("curve_us", tipo="nominal") if d in reais]

    def carregar(data_ref: dt.date) -> curva_mod.Curva | None:
        nominal, real = curva_us(data_ref, "nominal"), curva_us(data_ref, "real")
        if nominal.vazia or real.vazia:
            return None
        grade = dif.inflacao_implicita(nominal, real, metodo=metodo)
        return curva_mod.build_curve(grade, data_ref, str(data_ref), coluna_taxa="implicita")

    leitura, eixo_y, cor = leitura_breakeven, "Inflação implícita (% a.a.)", charts.VERDE
    limites = (5.0, 20.0)
else:
    datas = common.cache_datas("curve_us", tipo=tipo)

    def carregar(data_ref: dt.date) -> curva_mod.Curva | None:
        return curva_us(data_ref, tipo)

    leitura, eixo_y = comparacao.leitura_juros, "Taxa (% a.a.)"
    cor = charts.US if tipo == "nominal" else charts.ROXO
    limites = (
        (comparacao.LIMITE_CURTO, comparacao.LIMITE_LONGO) if tipo == "nominal" else (5.0, 20.0)
    )

objetos = comparacao.comparar(
    datas,
    carregar,
    descricao=f"Estados Unidos · {TIPOS[tipo]}",
    cor_base=cor,
    metodo=metodo,
    chave="cmp_us",
    leitura=leitura,
    eixo_y=eixo_y,
    limite_curto=limites[0],
    limite_longo=limites[1],
)

# ------------------------------------------------------- inclinação 10a−2a
if objetos and tipo == "nominal":
    st.subheader("Inclinação 10a−2a por data")
    linhas = []
    for curva in objetos:
        dois, dez = curva_mod.interpolate(curva, [2.0, 10.0], metodo=metodo)
        linhas.append(
            {
                "Data": str(curva.data_ref),
                "2 anos (%)": dois,
                "10 anos (%)": dez,
                "10a−2a (bps)": (dez - dois) * 100,
            }
        )
    tabela = pd.DataFrame(linhas)
    st.dataframe(charts.arredondar(tabela), width="stretch", hide_index=True)

    if len(tabela) >= 2:
        antes, depois = tabela["10a−2a (bps)"].iloc[0], tabela["10a−2a (bps)"].iloc[-1]
        if pd.notna(antes) and pd.notna(depois):
            if (antes < 0) != (depois < 0):
                virada = "**desinverteu**" if depois >= 0 else "**inverteu**"
                st.info(
                    f"Entre {tabela['Data'].iloc[0]} e {tabela['Data'].iloc[-1]} a curva "
                    f"{virada}: a inclinação foi de {antes:+.0f} para {depois:+.0f} bps.",
                    icon="🔀",
                )
            else:
                st.caption(
                    f"A inclinação 10a−2a foi de {antes:+.0f} para {depois:+.0f} bps "
                    f"({depois - antes:+.0f} bps) no período, "
                    f"{'invertida' if depois < 0 else 'positiva'} nas duas pontas."
                )

st.caption(
    "As taxas são *par yields* em convenção semestral, como o Tesouro americano "
    "publica; a variação entre datas não depende da convenção, desde que as duas "
    "estejam na mesma. O breakeven usa a relação de Fisher exata."
)

common.rodape()
