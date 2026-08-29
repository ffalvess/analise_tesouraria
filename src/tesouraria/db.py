"""Camada de persistência em DuckDB.

Um único arquivo local guarda o histórico de todas as fontes. A gravação é
idempotente: `upsert` apaga as linhas cuja chave primária aparece no lote que
está entrando e depois insere o lote, de modo que reingerir o mesmo período
não duplica nada nem deixa registros órfãos.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import duckdb
import pandas as pd

from tesouraria.settings import get_settings

logger = logging.getLogger(__name__)

SCHEMA: dict[str, str] = {
    # Curva brasileira. Uma linha por título/vértice por dia e por fonte, o que
    # permite comparar Tesouro, ANBIMA e B3 na mesma data.
    "curve_br": """
        CREATE TABLE IF NOT EXISTS curve_br (
            data_ref      DATE     NOT NULL,
            fonte         VARCHAR  NOT NULL,  -- tesouro | anbima | b3
            tipo          VARCHAR  NOT NULL,  -- pre | ipca | implicita
            instrumento   VARCHAR  NOT NULL,
            vencimento    DATE,
            prazo_du      INTEGER,
            prazo_anos    DOUBLE   NOT NULL,
            taxa          DOUBLE   NOT NULL,  -- % ao ano
            preco         DOUBLE
        )
    """,
    # Curva americana: par yield nominal e real (TIPS).
    "curve_us": """
        CREATE TABLE IF NOT EXISTS curve_us (
            data_ref      DATE     NOT NULL,
            tipo          VARCHAR  NOT NULL,  -- nominal | real
            tenor         VARCHAR  NOT NULL,  -- 1 Mo, 2 Yr, ...
            prazo_anos    DOUBLE   NOT NULL,
            taxa          DOUBLE   NOT NULL   -- % ao ano
        )
    """,
    # Tabela alta e genérica para tudo que é série temporal simples:
    # SGS, SIDRA, FRED, PTAX, balança comercial.
    "series_macro": """
        CREATE TABLE IF NOT EXISTS series_macro (
            pais          VARCHAR  NOT NULL,  -- BR | US
            fonte         VARCHAR  NOT NULL,
            serie_id      VARCHAR  NOT NULL,
            nome          VARCHAR,
            unidade       VARCHAR,
            data_ref      DATE     NOT NULL,
            valor         DOUBLE
        )
    """,
    "focus": """
        CREATE TABLE IF NOT EXISTS focus (
            data_coleta     DATE     NOT NULL,
            tipo            VARCHAR  NOT NULL,  -- geral | top5
            indicador       VARCHAR  NOT NULL,
            data_referencia VARCHAR  NOT NULL,  -- ano ou competência
            mediana         DOUBLE,
            media           DOUBLE,
            desvio          DOUBLE,
            minimo          DOUBLE,
            maximo          DOUBLE,
            n_respondentes  INTEGER
        )
    """,
    "fx_flow": """
        CREATE TABLE IF NOT EXISTS fx_flow (
            data_ref      DATE     NOT NULL,
            periodicidade VARCHAR  NOT NULL,  -- semanal | mensal
            segmento      VARCHAR  NOT NULL,  -- comercial | financeiro | total
            compras       DOUBLE,
            vendas        DOUBLE,
            saldo         DOUBLE
        )
    """,
    # Discursos, atas e relatórios, já com o score de tom calculado.
    "documentos": """
        CREATE TABLE IF NOT EXISTS documentos (
            id            VARCHAR  NOT NULL,  -- hash do URL ou do arquivo
            fonte         VARCHAR  NOT NULL,
            instituicao   VARCHAR,            -- Fed | BCB | research
            autor         VARCHAR,
            titulo        VARCHAR,
            data_pub      DATE,
            url           VARCHAR,
            tipo          VARCHAR,            -- discurso | ata | relatorio | pdf_local
            idioma        VARCHAR,
            texto         VARCHAR,
            score_tom     DOUBLE,
            n_hawk        INTEGER,
            n_dove        INTEGER
        )
    """,
    "ingest_log": """
        CREATE TABLE IF NOT EXISTS ingest_log (
            fonte         VARCHAR   NOT NULL,
            executado_em  TIMESTAMP NOT NULL,
            status        VARCHAR   NOT NULL,  -- ok | vazio | erro | pulado
            linhas        INTEGER,
            modo          VARCHAR,             -- rede | fixture
            erro          VARCHAR
        )
    """,
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "curve_br": ("data_ref", "fonte", "tipo", "instrumento", "vencimento"),
    "curve_us": ("data_ref", "tipo", "tenor"),
    "series_macro": ("fonte", "serie_id", "data_ref"),
    "focus": ("data_coleta", "tipo", "indicador", "data_referencia"),
    "fx_flow": ("data_ref", "periodicidade", "segmento"),
    "documentos": ("id",),
    "ingest_log": ("fonte", "executado_em"),
}


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    settings = get_settings()
    settings.ensure_dirs()
    if read_only and not settings.db_path.exists():
        # Abrir um banco inexistente em modo leitura falha; cria o esqueleto antes.
        init_db(duckdb.connect(str(settings.db_path)))
    con = duckdb.connect(str(settings.db_path), read_only=read_only)
    if not read_only:
        init_db(con)
    return con


@contextmanager
def connection(read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    con = connect(read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in SCHEMA.values():
        con.execute(ddl)


def _coerce(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Alinha o DataFrame às colunas da tabela, preenchendo o que faltar."""
    columns = table_columns(table)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = None
    return out[list(columns)]


def table_columns(table: str) -> tuple[str, ...]:
    ddl = SCHEMA[table]
    body = ddl[ddl.index("(") + 1 : ddl.rindex(")")]
    names: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.split("--")[0].strip().rstrip(",")
        if not line:
            continue
        names.append(line.split()[0])
    return tuple(names)


def upsert(
    con: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    keys: Sequence[str] | None = None,
) -> int:
    """Grava `df` em `table`, substituindo linhas de mesma chave primária.

    Devolve a quantidade de linhas gravadas.
    """
    if df is None or df.empty:
        return 0

    keys = tuple(keys or PRIMARY_KEYS[table])
    payload = _coerce(df, table)

    con.register("_lote", payload)
    try:
        # Chaves anuláveis (por exemplo `vencimento` em séries sem vencimento)
        # exigem comparação segura contra NULL, daí IS NOT DISTINCT FROM.
        condition = " AND ".join(
            f"alvo.{key} IS NOT DISTINCT FROM lote.{key}" for key in keys
        )
        con.execute(
            f"DELETE FROM {table} AS alvo "
            f"WHERE EXISTS (SELECT 1 FROM _lote AS lote WHERE {condition})"
        )
        con.execute(f"INSERT INTO {table} SELECT * FROM _lote")
    finally:
        con.unregister("_lote")

    return len(payload)


def log_ingest(
    con: duckdb.DuckDBPyConnection,
    fonte: str,
    status: str,
    linhas: int = 0,
    modo: str = "rede",
    erro: str | None = None,
) -> None:
    con.execute(
        "INSERT INTO ingest_log (fonte, executado_em, status, linhas, modo, erro) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [fonte, dt.datetime.now(), status, linhas, modo, (erro or "")[:2000] or None],
    )


def status_report() -> pd.DataFrame:
    """Frescor por fonte: última execução, status e cobertura de datas."""
    with connection(read_only=True) as con:
        log = con.execute(
            """
            SELECT fonte, executado_em, status, linhas, modo, erro
            FROM ingest_log
            QUALIFY ROW_NUMBER() OVER (PARTITION BY fonte ORDER BY executado_em DESC) = 1
            ORDER BY fonte
            """
        ).df()
    return log


def table_coverage() -> pd.DataFrame:
    """Contagem e intervalo de datas de cada tabela de dados."""
    date_column = {
        "curve_br": "data_ref",
        "curve_us": "data_ref",
        "series_macro": "data_ref",
        "focus": "data_coleta",
        "fx_flow": "data_ref",
        "documentos": "data_pub",
    }
    rows = []
    with connection(read_only=True) as con:
        for table, column in date_column.items():
            result = con.execute(
                f"SELECT COUNT(*), MIN({column}), MAX({column}) FROM {table}"
            ).fetchone()
            rows.append(
                {
                    "tabela": table,
                    "linhas": result[0],
                    "data_min": result[1],
                    "data_max": result[2],
                }
            )
    return pd.DataFrame(rows)
