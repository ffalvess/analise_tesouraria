"""Inflação, emprego e atividade — Brasil e Estados Unidos lado a lado.

São os dados que decidem as duas curvas. Colocá-los na mesma tela, com o mesmo
recorte temporal, é o que permite ver quando os dois ciclos se descolam — e o
descolamento entre eles é a origem do diferencial de juros.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesouraria.ui import charts, common

common.configurar("Inflação, emprego e atividade", "📊")

if not common.exigir_dados():
    st.stop()

catalogo = common.cache_catalogo()
if catalogo.empty:
    st.warning("Sem séries macroeconômicas. Rode `tesouraria ingest --source bcb_sgs`.")
    st.stop()


def primeira_disponivel(*ids: str) -> pd.DataFrame:
    """Primeira das séries indicadas que tem dados — permite fontes alternativas."""
    for serie_id in ids:
        dados = common.cache_serie(serie_id)
        if not dados.empty:
            return dados
    return pd.DataFrame()


def painel(titulo: str, br: pd.DataFrame, us: pd.DataFrame, eixo: str, sufixo: str = "") -> None:
    esquerda, direita = st.columns(2)
    with esquerda:
        if br.empty:
            st.caption(f"{titulo} — Brasil: série não ingerida.")
        else:
            st.plotly_chart(
                charts.grafico_series(
                    [(f"Brasil — {titulo}", br)],
                    titulo=f"{titulo} · Brasil",
                    eixo_y=eixo,
                    sufixo=sufixo,
                    cores=[charts.BR],
                ),
                use_container_width=True,
            )
    with direita:
        if us.empty:
            st.caption(
                f"{titulo} — EUA: série não ingerida "
                "(as séries do FRED exigem `FRED_API_KEY`)."
            )
        else:
            st.plotly_chart(
                charts.grafico_series(
                    [(f"EUA — {titulo}", us)],
                    titulo=f"{titulo} · Estados Unidos",
                    eixo_y=eixo,
                    sufixo=sufixo,
                    cores=[charts.US],
                ),
                use_container_width=True,
            )


aba_inflacao, aba_emprego, aba_atividade, aba_catalogo = st.tabs(
    ["Inflação", "Emprego", "Atividade", "Todas as séries"]
)

with aba_inflacao:
    ipca_12m = primeira_disponivel("13522", "1737-2265")
    ipca_mensal = primeira_disponivel("433", "1737-63")
    cpi = common.cache_serie("CPIAUCSL")
    core_cpi = common.cache_serie("CPILFESL")

    # O CPI é publicado em índice; a variação em 12 meses é o que se compara ao IPCA.
    def variacao_12m(dados: pd.DataFrame) -> pd.DataFrame:
        if dados.empty:
            return dados
        out = dados.sort_values("data_ref").copy()
        out["valor"] = out["valor"].pct_change(12) * 100
        return out.dropna(subset=["valor"])

    painel("Inflação em 12 meses", ipca_12m, variacao_12m(cpi), "% em 12 meses", "%")

    if not ipca_12m.empty and not cpi.empty:
        st.plotly_chart(
            charts.grafico_series(
                [
                    ("IPCA 12 meses", ipca_12m),
                    ("CPI 12 meses", variacao_12m(cpi)),
                    ("Core CPI 12 meses", variacao_12m(core_cpi)),
                ],
                titulo="Inflação nos dois países, na mesma escala",
                eixo_y="% em 12 meses",
                sufixo="%",
                cores=[charts.BR, charts.US, charts.ROXO],
            ),
            use_container_width=True,
        )
        st.caption(
            "É o diferencial de inflação, e não o nível de cada uma, que sustenta um "
            "diferencial de juros nominais no longo prazo."
        )

    if not ipca_mensal.empty:
        st.plotly_chart(
            charts.grafico_series(
                [("IPCA mensal", ipca_mensal)],
                titulo="IPCA — variação mensal",
                eixo_y="% a.m.",
                sufixo="%",
                cores=[charts.BR],
            ),
            use_container_width=True,
        )

with aba_emprego:
    desocupacao = primeira_disponivel("24369", "6381-4099")
    unrate = common.cache_serie("UNRATE")
    painel("Taxa de desemprego", desocupacao, unrate, "%", "%")

    payrolls = common.cache_serie("PAYEMS")
    if not payrolls.empty:
        variacao = payrolls.sort_values("data_ref").copy()
        variacao["valor"] = variacao["valor"].diff()
        st.plotly_chart(
            charts.grafico_series(
                [("Criação de vagas (payroll)", variacao.dropna(subset=["valor"]))],
                titulo="Estados Unidos — variação mensal do payroll",
                eixo_y="mil vagas",
                cores=[charts.US],
            ),
            use_container_width=True,
        )
        st.caption(
            "O payroll é o dado que mais move a ponta curta da curva americana no dia "
            "da divulgação."
        )

with aba_atividade:
    ibc = primeira_disponivel("24364", "24363")
    pib = primeira_disponivel("5932-6564")
    pmc = primeira_disponivel("8880-7169")

    esquerda, direita = st.columns(2)
    with esquerda:
        if ibc.empty:
            st.caption("IBC-Br não ingerido.")
        else:
            st.plotly_chart(
                charts.grafico_series(
                    [("IBC-Br", ibc)],
                    titulo="Brasil — IBC-Br (proxy mensal do PIB)",
                    eixo_y="índice",
                    cores=[charts.BR],
                ),
                use_container_width=True,
            )
    with direita:
        if pib.empty:
            st.caption("PIB anual não ingerido.")
        else:
            st.plotly_chart(
                charts.grafico_series(
                    [("PIB — variação anual", pib)],
                    titulo="Brasil — PIB",
                    eixo_y="%",
                    sufixo="%",
                    cores=[charts.VERDE],
                ),
                use_container_width=True,
            )

    if not pmc.empty:
        st.plotly_chart(
            charts.grafico_series(
                [("Comércio varejista (PMC)", pmc)],
                titulo="Brasil — comércio varejista",
                eixo_y="índice",
                cores=[charts.AMBAR],
            ),
            use_container_width=True,
        )

    reservas = common.cache_serie("4192")
    divida = common.cache_serie("4503")
    if not reservas.empty or not divida.empty:
        st.subheader("Contexto externo e fiscal")
        painel("Reservas / dívida", reservas, pd.DataFrame(), "US$ milhões")
        if not divida.empty:
            st.plotly_chart(
                charts.grafico_series(
                    [("Dívida líquida do setor público", divida)],
                    titulo="Brasil — dívida líquida",
                    eixo_y="% do PIB",
                    sufixo="%",
                    cores=[charts.VERMELHO],
                ),
                use_container_width=True,
            )
            st.caption(
                "A trajetória fiscal é o fator que mais frequentemente quebra a relação "
                "entre diferencial de juros e câmbio: com dívida em alta, carrego alto "
                "deixa de atrair capital."
            )

with aba_catalogo:
    st.caption(
        "Todas as séries no banco. Para acrescentar outra, inclua o código em "
        "`config/sources.yaml` e rode a ingestão — nenhuma alteração de código é necessária."
    )
    st.dataframe(catalogo, use_container_width=True, hide_index=True)

    escolhida = st.selectbox(
        "Visualizar uma série",
        catalogo["serie_id"].tolist(),
        format_func=lambda s: (
            f"{s} — {catalogo[catalogo['serie_id'] == s]['nome'].iloc[0]}"
        ),
    )
    dados = common.cache_serie(escolhida)
    if not dados.empty:
        nome = dados["nome"].iloc[0]
        unidade = dados["unidade"].iloc[0] or ""
        st.plotly_chart(
            charts.grafico_series(
                [(nome, dados)], titulo=f"{nome} ({escolhida})", eixo_y=unidade
            ),
            use_container_width=True,
        )

common.rodape()
