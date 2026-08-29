"""Comunicação do BCB e do Fed, e relatórios de análise sobre o Brasil.

Cada documento recebe um score de tom entre −1 (dovish) e +1 (hawkish),
calculado por léxico ponderado. O método é determinístico e a régua não muda
com o tempo, o que permite comparar o tom de hoje com o de dois anos atrás.

Como ler com cuidado: um léxico não entende ironia, condicional nem citação de
terceiros. O score serve para detectar **inflexões numa série** de
pronunciamentos da mesma autoridade, não para julgar um texto isolado.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from tesouraria.analytics import tone
from tesouraria.settings import get_settings
from tesouraria.ui import charts, common

common.configurar("Comunicação e research", "🗣️")

if not common.exigir_dados():
    st.stop()

meses = st.sidebar.slider("Período (meses)", 3, 60, 24, 3)
desde = dt.date.today() - dt.timedelta(days=meses * 30)

documentos = common.cache_documentos(desde=desde, limite=2000)
if documentos.empty:
    st.warning(
        "Nenhum documento ingerido no período. Rode "
        "`tesouraria ingest --source speeches --source research`."
    )
    st.stop()

instituicoes = sorted(documentos["instituicao"].dropna().unique())
escolhidas = st.sidebar.multiselect("Instituições", instituicoes, default=instituicoes)
busca = st.sidebar.text_input("Buscar no texto", placeholder="ex.: desancoragem, tightening")

filtrados = documentos[documentos["instituicao"].isin(escolhidas)]
if busca:
    alvo = busca.lower()
    filtrados = filtrados[
        filtrados["titulo"].fillna("").str.lower().str.contains(alvo)
        | filtrados["trecho"].fillna("").str.lower().str.contains(alvo)
    ]

if filtrados.empty:
    st.info("Nenhum documento atende aos filtros escolhidos.")
    st.stop()

filtrados = filtrados.copy()
filtrados["data_pub"] = pd.to_datetime(filtrados["data_pub"])

colunas = st.columns(4)
colunas[0].metric("Documentos", len(filtrados))
colunas[1].metric("Tom médio", f"{filtrados['score_tom'].mean():+.3f}")
recentes = filtrados.nlargest(min(10, len(filtrados)), "data_pub")
colunas[2].metric(
    "Tom dos 10 mais recentes",
    f"{recentes['score_tom'].mean():+.3f}",
    help="Comparado ao tom médio do período, indica para onde a comunicação está indo.",
)
colunas[3].metric("Instituições", filtrados["instituicao"].nunique())

aba_tom, aba_lista, aba_texto = st.tabs(["Evolução do tom", "Documentos", "Analisar um texto"])


with aba_tom:
    st.plotly_chart(
        charts.grafico_dispersao_tom(filtrados, titulo="Tom de cada documento"),
        use_container_width=True,
    )

    serie = tone.serie_de_tom(filtrados, janela=5)
    if not serie.empty:
        linhas = [
            (str(instituicao), grupo.rename(columns={"data_pub": "data_ref", "media_movel": "valor"}))
            for instituicao, grupo in serie.groupby("instituicao")
        ]
        st.plotly_chart(
            charts.grafico_series(
                linhas,
                titulo="Tom médio móvel (5 documentos) por instituição",
                eixo_y="← dovish   ·   hawkish →",
                cores=[charts.BR, charts.US, charts.VERDE, charts.ROXO, charts.AMBAR],
            ),
            use_container_width=True,
        )

    # Sobrepor o tom à curva mostra se a comunicação antecedeu o movimento.
    fonte_br, _ = common.seletor_fonte_br("tom")
    curva_1a = common.cache_historico("curve_br", 1.0, 0.35, {"fonte": fonte_br, "tipo": "pre"})
    bcb = serie[serie["instituicao"] == "BCB"] if not serie.empty else pd.DataFrame()

    if not curva_1a.empty and not bcb.empty:
        st.subheader("Tom do BCB contra a curva")
        esquerda, direita = st.columns(2)
        with esquerda:
            st.plotly_chart(
                charts.grafico_series(
                    [("Tom do BCB (média móvel)", bcb.rename(columns={"data_pub": "data_ref", "media_movel": "valor"}))],
                    titulo="Tom da comunicação",
                    eixo_y="score",
                    cores=[charts.ROXO],
                ),
                use_container_width=True,
            )
        with direita:
            st.plotly_chart(
                charts.grafico_series(
                    [("Vértice de 1 ano", curva_1a.rename(columns={"taxa": "valor"}))],
                    titulo="Curva pré — 1 ano",
                    eixo_y="% a.a.",
                    sufixo="%",
                    cores=[charts.BR],
                ),
                use_container_width=True,
            )
        st.caption(
            "Se o tom vira antes da curva, a comunicação está guiando o mercado. Se vira "
            "depois, o Banco Central está validando um movimento que o mercado já fez."
        )


with aba_lista:
    ordenacao = st.radio(
        "Ordenar por", ["Mais recentes", "Mais hawkish", "Mais dovish"], horizontal=True
    )
    if ordenacao == "Mais hawkish":
        ordenados = filtrados.sort_values("score_tom", ascending=False)
    elif ordenacao == "Mais dovish":
        ordenados = filtrados.sort_values("score_tom")
    else:
        ordenados = filtrados.sort_values("data_pub", ascending=False)

    for linha in ordenados.head(60).itertuples():
        tom = linha.score_tom if linha.score_tom is not None else 0.0
        marcador = "🔴" if tom > 0.25 else ("🟢" if tom < -0.25 else "⚪")
        rotulo = (
            f"{marcador} {linha.data_pub.date()} · {linha.instituicao} · "
            f"tom {tom:+.2f} — {(linha.titulo or '')[:110]}"
        )
        with st.expander(rotulo):
            if linha.autor:
                st.caption(f"Autor: {linha.autor}")
            st.write((linha.trecho or "")[:1200] + ("…" if (linha.tamanho or 0) > 1200 else ""))
            st.caption(f"{linha.n_hawk} termos hawkish · {linha.n_dove} termos dovish")
            if linha.tipo == "pdf_local":
                st.caption(f"Arquivo local: `{linha.url}`")
            else:
                st.markdown(f"[Abrir documento original]({linha.url})")

    st.download_button(
        "Baixar a seleção em CSV",
        ordenados.drop(columns=["trecho"]).to_csv(index=False).encode("utf-8"),
        file_name="documentos.csv",
        mime="text/csv",
    )


with aba_texto:
    st.markdown(
        "Cole um trecho de discurso, ata ou relatório para ver como o léxico o pontua. "
        "Útil para calibrar a leitura antes de confiar nos gráficos acima."
    )
    idioma = st.radio("Idioma", ["pt", "en"], horizontal=True, format_func=str.upper)
    texto = st.text_area("Texto", height=200, placeholder="Cole aqui o trecho a analisar…")

    if texto.strip():
        resultado = tone.pontuar(texto, idioma)
        colunas = st.columns(4)
        colunas[0].metric("Score", f"{resultado.score:+.3f}")
        colunas[1].metric("Classificação", resultado.rotulo)
        colunas[2].metric("Termos hawkish", resultado.n_hawk)
        colunas[3].metric("Termos dovish", resultado.n_dove)
        st.caption(
            f"Peso hawkish {resultado.peso_hawk:.2f} contra dovish {resultado.peso_dove:.2f}. "
            "Os termos e seus pesos ficam em `config/lexicon_pt.yaml` e `lexicon_en.yaml` — "
            "ajuste-os ao seu vocabulário de mesa."
        )

st.divider()
st.markdown(
    f"""
**Sobre os relatórios de casas de análise.** Pesquisa sell-side (Itaú, BTG, XP,
Goldman e afins) é conteúdo licenciado e **não é coletada** por este aplicativo.
A página agrega apenas fontes abertas — FMI, BIS, Banco Mundial, agências de
rating, IPEA, FGV — configuradas em `config/feeds.yaml`.

Para incluir os relatórios que você já recebe por direito, coloque os PDFs em
`{get_settings().research_pdfs_dir}` e rode `tesouraria ingest --source research`.
O texto é extraído localmente e recebe o mesmo tratamento de tom. Subpastas viram
o nome da instituição.
"""
)

common.rodape()
