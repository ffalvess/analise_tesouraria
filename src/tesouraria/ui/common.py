"""Elementos compartilhados pelas páginas: cabeçalho, barra lateral e cache.

As consultas passam por `st.cache_data` porque o DuckDB é aberto em modo
somente leitura a cada chamada; sem cache, cada rerender do Streamlit reabriria
o arquivo várias vezes.
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import streamlit as st

from tesouraria import db, queries, snapshots
from tesouraria.settings import get_settings

TTL = 300  # segundos


@st.cache_resource(show_spinner="Preparando os dados…")
def preparar_ambiente() -> dict[str, int]:
    """Prepara o container antes da primeira consulta. Roda uma vez por processo.

    Faz duas coisas necessárias para publicar o aplicativo:

    1. **Segredos viram variáveis de ambiente.** As `Settings` leem o ambiente
       via pydantic-settings; copiar `st.secrets` para `os.environ` antes do
       primeiro `get_settings()` faz a chave do FRED chegar até elas sem
       depender de o serviço de hospedagem expor secrets como variáveis.
    2. **Hidratação a partir dos snapshots.** O disco do Streamlit Community
       Cloud é efêmero: a cada container novo o DuckDB nasce vazio. Se houver
       Parquet versionados em `data/snapshots/`, o banco é reconstruído a
       partir deles em segundos, em vez de exigir uma coleta completa.
    """
    try:
        segredos = dict(st.secrets)
    except Exception:  # noqa: BLE001 — sem arquivo de secrets, o normal em uso local
        segredos = {}

    novos = False
    for chave, valor in segredos.items():
        if isinstance(valor, str) and chave not in os.environ:
            os.environ[chave] = valor
            novos = True
    if novos:
        get_settings.cache_clear()

    if not snapshots.tem_snapshots() or not _banco_vazio_sem_cache():
        return {}

    with db.connection() as con:
        resumo = snapshots.importar(con)

    # As consultas passam por `st.cache_data`; sem limpar, as chamadas feitas
    # antes da hidratação continuariam devolvendo o banco vazio.
    st.cache_data.clear()
    return resumo


def _banco_vazio_sem_cache() -> bool:
    """Igual a `banco_vazio`, mas sem passar pelo cache do Streamlit."""
    try:
        cobertura = db.table_coverage()
    except Exception:  # noqa: BLE001 — banco inexistente ou corrompido
        return True
    return bool(cobertura.empty or cobertura["linhas"].sum() == 0)


def configurar(titulo: str, icone: str = "📈") -> None:
    """Configuração de página e avisos que valem para todas as telas."""
    st.set_page_config(page_title=f"{titulo} · Tesouraria", page_icon=icone, layout="wide")
    preparar_ambiente()
    st.title(titulo)
    aviso_procedencia()


def aviso_procedencia() -> None:
    """Avisa quando os números na tela são sintéticos.

    Duas situações levam a isso, e a segunda é a traiçoeira: além do modo
    offline explícito, o banco pode ter sido hidratado por snapshots que alguém
    gerou a partir das amostras. Nesse caso o aplicativo roda em modo normal e
    nada denunciaria a origem — não fosse `ingest_log.modo`, que viaja junto no
    snapshot e registra se cada fonte veio da rede ou de fixture.
    """
    if get_settings().offline:
        st.warning(
            "**Modo offline** — os números nesta tela vêm das amostras sintéticas de "
            "`data/fixtures/`, geradas para desenvolvimento. **Não são dados reais de "
            "mercado.** Para dados reais, rode `tesouraria ingest --all` com rede aberta "
            "e reabra o aplicativo sem `TESOURARIA_OFFLINE`.",
            icon="⚠️",
        )
        return

    fontes = fontes_sinteticas()
    if fontes:
        st.warning(
            f"**Dados sintéticos no banco** — {len(fontes)} fonte(s) foram carregadas a "
            f"partir das amostras de desenvolvimento, não da rede: "
            f"`{'`, `'.join(fontes)}`. **Não são dados reais de mercado.** "
            "Rode `tesouraria ingest --all` com rede aberta para substituí-las.",
            icon="⚠️",
        )


def fontes_sinteticas() -> list[str]:
    """Fontes cuja última coleta veio de fixture, e não da rede."""
    try:
        status = cache_status()
    except Exception:  # noqa: BLE001 — banco ausente ou sem registro
        return []
    if status.empty or "modo" not in status.columns:
        return []
    return sorted(status.loc[status["modo"] == "fixture", "fonte"].tolist())


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
        "Nenhum dado no banco ainda, e não há snapshots em `data/snapshots/` para "
        "hidratá-lo. Escolha um caminho:\n\n"
        "```bash\n"
        "tesouraria snapshot import                   # reconstrói de Parquet versionado\n"
        "tesouraria ingest --all --since 2015-01-01   # coleta real, exige rede\n"
        "TESOURARIA_OFFLINE=1 tesouraria ingest --all # amostras sintéticas\n"
        "```\n\n"
        "No aplicativo publicado, os snapshots são atualizados pelo GitHub Actions a "
        "cada dia útil; se esta mensagem aparecer lá, verifique se o workflow "
        "*Coleta de dados* rodou e commitou."
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


# Preferência de abertura, e não ordem alfabética: `tesouro` é a fonte primária
# e a única com histórico longo, e `pre` é a curva comparável à Treasury
# nominal. Sem isso, a tela abriria em `anbima/implicita` e o diferencial do
# painel confrontaria inflação implícita com juro nominal americano.
PREFERENCIA_FONTE = ["tesouro", "b3", "anbima"]
PREFERENCIA_TIPO = ["pre", "ipca", "implicita"]


def _ordenar(valores: list[str], preferencia: list[str]) -> list[str]:
    """Ordena pela preferência; o que não estiver na lista vai para o fim."""
    return sorted(
        valores,
        key=lambda v: (preferencia.index(v) if v in preferencia else len(preferencia), v),
    )


def seletor_fonte_br(chave: str = "fonte_br") -> tuple[str, str]:
    """Escolha de fonte e tipo da curva brasileira, limitada ao que existe."""
    disponivel = cache_fontes_br()
    if disponivel.empty:
        st.sidebar.warning("Nenhuma curva brasileira ingerida.")
        return "tesouro", "pre"

    rotulos_fonte = {"tesouro": "Tesouro Direto", "anbima": "ANBIMA ETTJ", "b3": "Futuros DI (B3)"}
    fontes = _ordenar(list(disponivel["fonte"].unique()), PREFERENCIA_FONTE)
    fonte = st.sidebar.selectbox(
        "Fonte da curva BR",
        fontes,
        format_func=lambda f: rotulos_fonte.get(f, f),
        key=f"{chave}_fonte",
    )

    rotulos_tipo = {"pre": "Prefixada", "ipca": "IPCA+ (real)", "implicita": "Inflação implícita"}
    tipos = _ordenar(
        list(disponivel[disponivel["fonte"] == fonte]["tipo"].unique()), PREFERENCIA_TIPO
    )
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
                st.dataframe(status, width="stretch", hide_index=True)
        with coluna_dir:
            st.caption("Cobertura das tabelas")
            st.dataframe(cobertura, width="stretch", hide_index=True)

        falhas = status[status["status"].isin(["erro", "pulado"])] if not status.empty else status
        if not falhas.empty:
            st.caption(
                "Fontes com erro ou puladas continuam com os dados da última coleta bem-sucedida. "
                "Rode `tesouraria status` no terminal para o detalhe."
            )
