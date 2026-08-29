"""Elementos compartilhados pelas páginas: cabeçalho, barra lateral e cache.

As consultas passam por `st.cache_data` porque o DuckDB é aberto em modo
somente leitura a cada chamada; sem cache, cada rerender do Streamlit reabriria
o arquivo várias vezes.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from tesouraria import db, queries
from tesouraria.settings import get_settings

TTL = 300  # segundos


def configurar(titulo: str, icone: str = "📈") -> None:
    """Configuração de página e avisos que valem para todas as telas."""
    st.set_page_config(page_title=f"{titulo} · Tesouraria", page_icon=icone, layout="wide")
    st.title(titulo)
    aviso_offline()


def aviso_offline() -> None:
    if get_settings().offline:
        st.warning(
            "**Modo offline** — os números nesta tela vêm das amostras sintéticas de "
            "`data/fixtures/`, geradas para desenvolvimento. **Não são dados reais de "
            "mercado.** Para dados reais, rode `tesouraria ingest --all` com rede aberta "
            "e reabra o aplicativo sem `TESOURARIA_OFFLINE`.",
            icon="⚠️",
        )


def banco_vazio() -> bool:
    try:
        cobertura = cache_cobertura()
    except Exception:  # noqa: BLE001 — banco inexistente ou corrompido
        return True
    return bool(cobertura.empty or cobertura["linhas"].sum() == 0)


def exigir_dados() -> bool:
    """Mostra instruções e interrompe a página quando não há nada ingerido."""
    if not banco_vazio():
        return True
    st.info(
        "Nenhum dado no banco ainda. Rode a ingestão antes de usar o aplicativo:\n\n"
        "```bash\n"
        "tesouraria ingest --all --since 2015-01-01   # dados reais, exige rede\n"
        "TESOURARIA_OFFLINE=1 tesouraria ingest --all # amostras sintéticas\n"
        "```"
    )
    return False


# ------------------------------------------------------------------ cache


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_datas(tabela: str, fonte: str | None = None, tipo: str | None = None) -> list[dt.date]:
    return queries.datas_disponiveis(tabela, fonte, tipo)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_curva_br(data_ref: dt.date, fonte: str, tipo: str) -> pd.DataFrame:
    return queries.curva_br(data_ref, fonte, tipo)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_curva_us(data_ref: dt.date, tipo: str) -> pd.DataFrame:
    return queries.curva_us(data_ref, tipo)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_serie(serie_id: str | list[str], desde: dt.date | None = None) -> pd.DataFrame:
    return queries.serie(serie_id, desde)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_catalogo() -> pd.DataFrame:
    return queries.catalogo_series()


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_fluxo(desde: dt.date | None = None) -> pd.DataFrame:
    return queries.fluxo_cambial(desde)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_focus(indicador: str | None, tipo: str, desde: dt.date | None = None) -> pd.DataFrame:
    return queries.focus(indicador, tipo, desde)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_documentos(
    instituicao: str | None = None,
    desde: dt.date | None = None,
    busca: str | None = None,
    limite: int = 500,
) -> pd.DataFrame:
    return queries.documentos(instituicao, desde, busca, limite)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_historico(tabela: str, prazo: float, tolerancia: float, filtros: dict) -> pd.DataFrame:
    return queries.historico_curva(tabela, prazo, tolerancia, **filtros)


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_fontes_br() -> pd.DataFrame:
    return queries.fontes_curva_br()


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_status() -> pd.DataFrame:
    return db.status_report()


@st.cache_data(ttl=TTL, show_spinner=False)
def cache_cobertura() -> pd.DataFrame:
    return db.table_coverage()


# ------------------------------------------------------------ barra lateral


def seletor_metodo() -> str:
    """Método de interpolação, compartilhado por todas as páginas de curva."""
    return st.sidebar.selectbox(
        "Interpolação",
        ["pchip", "cubic", "nss", "linear"],
        index=0,
        help=(
            "pchip é monotônica por trechos e não inventa oscilação entre vértices. "
            "nss (Nelson-Siegel-Svensson) suaviza a curva inteira com quatro fatores. "
            "cubic é a spline cúbica clássica."
        ),
    )


def seletor_fonte_br(chave: str = "fonte_br") -> tuple[str, str]:
    """Escolha de fonte e tipo da curva brasileira, limitada ao que existe."""
    disponivel = cache_fontes_br()
    if disponivel.empty:
        st.sidebar.warning("Nenhuma curva brasileira ingerida.")
        return "tesouro", "pre"

    rotulos_fonte = {"tesouro": "Tesouro Direto", "anbima": "ANBIMA ETTJ", "b3": "Futuros DI (B3)"}
    fontes = sorted(disponivel["fonte"].unique())
    fonte = st.sidebar.selectbox(
        "Fonte da curva BR",
        fontes,
        format_func=lambda f: rotulos_fonte.get(f, f),
        key=f"{chave}_fonte",
    )

    rotulos_tipo = {"pre": "Prefixada", "ipca": "IPCA+ (real)", "implicita": "Inflação implícita"}
    tipos = sorted(disponivel[disponivel["fonte"] == fonte]["tipo"].unique())
    tipo = st.sidebar.selectbox(
        "Tipo",
        tipos,
        format_func=lambda t: rotulos_tipo.get(t, t),
        key=f"{chave}_tipo",
    )
    return fonte, tipo


def seletor_data(
    tabela: str, rotulo: str, chave: str, fonte: str | None = None, tipo: str | None = None,
    posicao: int = 0,
) -> dt.date | None:
    datas = cache_datas(tabela, fonte, tipo)
    if not datas:
        st.sidebar.warning(f"Sem datas disponíveis em `{tabela}`.")
        return None
    indice = min(posicao, len(datas) - 1)
    return st.sidebar.selectbox(rotulo, datas, index=indice, key=chave)


def rodape() -> None:
    """Frescor dos dados — evita analisar uma curva de três semanas atrás sem notar."""
    st.divider()
    with st.expander("Frescor dos dados", expanded=False):
        status = cache_status()
        cobertura = cache_cobertura()

        coluna_esq, coluna_dir = st.columns(2)
        with coluna_esq:
            st.caption("Última ingestão por fonte")
            if status.empty:
                st.write("nenhuma ingestão registrada")
            else:
                st.dataframe(status, use_container_width=True, hide_index=True)
        with coluna_dir:
            st.caption("Cobertura das tabelas")
            st.dataframe(cobertura, use_container_width=True, hide_index=True)

        falhas = status[status["status"].isin(["erro", "pulado"])] if not status.empty else status
        if not falhas.empty:
            st.caption(
                "Fontes com erro ou puladas continuam com os dados da última coleta bem-sucedida. "
                "Rode `tesouraria status` no terminal para o detalhe."
            )
