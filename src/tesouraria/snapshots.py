"""Exportação e importação do banco em Parquet versionado.

Serve à publicação: o disco de um serviço como o Streamlit Community Cloud é
efêmero, então o DuckDB precisa ser reconstruído a cada container novo. Os
Parquet ficam no repositório, o GitHub Actions os atualiza e o aplicativo os
carrega em segundos ao subir.

**Particionamento por mês.** Um arquivo único por tabela seria reescrito
inteiro a cada coleta, e o git guardaria um blob novo por dia — o histórico
cresceria mais de um gigabyte por ano. Com um arquivo por mês, só o do mês
corrente muda; os anteriores ficam byte a byte idênticos e o git guarda um
único blob para cada.

Essa promessa depende de a escrita ser **determinística**: mesmo conteúdo,
mesmos bytes. Daí o `ORDER BY` explícito em cada exportação — sem ele, a ordem
das linhas variaria entre execuções, todos os meses seriam reescritos e o ganho
do particionamento desapareceria. `tests/test_snapshots.py` protege isso
comparando o hash dos arquivos entre duas exportações seguidas.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import duckdb

from tesouraria import db
from tesouraria.settings import get_settings

logger = logging.getLogger(__name__)

SEM_DATA = "sem-data"

# O registro de ingestão viaja junto, mas só a última execução de cada fonte —
# é o que o rodapé "Frescor dos dados" mostra. Sem ele, o aplicativo publicado
# não teria como dizer quando os dados foram coletados. Fica num arquivo único,
# de tamanho constante, em vez de particionado.
ARQUIVO_LOG = "ingest_log/ultimo.parquet"

# Coluna que define a partição mensal de cada tabela de dados.
PARTICAO: dict[str, str] = {
    "curve_br": "data_ref",
    "curve_us": "data_ref",
    "series_macro": "data_ref",
    "focus": "data_coleta",
    "fx_flow": "data_ref",
    "documentos": "data_pub",
}

# Ordenação estável por tabela. Precisa determinar a ordem de forma única —
# por isso termina sempre numa coluna (ou conjunto) que é chave primária.
ORDENACAO: dict[str, tuple[str, ...]] = {
    "curve_br": db.PRIMARY_KEYS["curve_br"],
    "curve_us": db.PRIMARY_KEYS["curve_us"],
    "series_macro": db.PRIMARY_KEYS["series_macro"],
    "focus": db.PRIMARY_KEYS["focus"],
    "fx_flow": db.PRIMARY_KEYS["fx_flow"],
    "documentos": db.PRIMARY_KEYS["documentos"],
}


def diretorio_padrao() -> Path:
    return get_settings().data_dir / "snapshots"


def tem_snapshots(origem: Path | None = None) -> bool:
    """Há ao menos um Parquet para importar?"""
    origem = origem or diretorio_padrao()
    if not origem.exists():
        return False
    # Só as tabelas de dados contam: um `ingest_log` solto não hidrata nada.
    return any(any((origem / tabela).glob("*.parquet")) for tabela in PARTICAO)


# --------------------------------------------------------------- exportação


def exportar(
    con: duckdb.DuckDBPyConnection, destino: Path | None = None
) -> dict[str, int]:
    """Grava cada tabela em Parquet, um arquivo por mês.

    Devolve a contagem de linhas exportadas por tabela. O diretório de cada
    tabela é recriado do zero, de modo que um mês que deixou de existir nos
    dados não fique para trás como arquivo órfão.
    """
    destino = destino or diretorio_padrao()
    destino.mkdir(parents=True, exist_ok=True)

    resumo: dict[str, int] = {}
    for tabela, coluna_data in PARTICAO.items():
        pasta = destino / tabela
        if pasta.exists():
            shutil.rmtree(pasta)
        pasta.mkdir(parents=True)

        resumo[tabela] = _exportar_tabela(con, tabela, coluna_data, pasta)

    resumo["ingest_log"] = _exportar_log(con, destino)
    return resumo


def _exportar_log(con: duckdb.DuckDBPyConnection, destino: Path) -> int:
    """Só a última execução de cada fonte, ordenada para sair determinística."""
    arquivo = destino / ARQUIVO_LOG
    arquivo.parent.mkdir(parents=True, exist_ok=True)

    con.execute(
        f"""
        COPY (
            SELECT * FROM ingest_log
            QUALIFY ROW_NUMBER() OVER (PARTITION BY fonte ORDER BY executado_em DESC) = 1
            ORDER BY fonte
        )
        TO '{arquivo.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    return con.execute(
        "SELECT COUNT(DISTINCT fonte) FROM ingest_log"
    ).fetchone()[0]


def _exportar_tabela(
    con: duckdb.DuckDBPyConnection, tabela: str, coluna_data: str, pasta: Path
) -> int:
    meses = con.execute(
        f"""
        SELECT DISTINCT coalesce(strftime({coluna_data}, '%Y-%m'), '{SEM_DATA}') AS mes
        FROM {tabela}
        ORDER BY mes
        """
    ).fetchall()

    ordem = ", ".join(ORDENACAO[tabela])
    total = 0

    for (mes,) in meses:
        if mes == SEM_DATA:
            filtro = f"{coluna_data} IS NULL"
        else:
            filtro = f"strftime({coluna_data}, '%Y-%m') = '{mes}'"

        arquivo = pasta / f"{mes}.parquet"
        con.execute(
            f"""
            COPY (SELECT * FROM {tabela} WHERE {filtro} ORDER BY {ordem})
            TO '{arquivo.as_posix()}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        total += con.execute(f"SELECT COUNT(*) FROM {tabela} WHERE {filtro}").fetchone()[0]

    logger.info("snapshot de %s: %d linhas em %d arquivos", tabela, total, len(meses))
    return total


# --------------------------------------------------------------- importação


def importar(
    con: duckdb.DuckDBPyConnection, origem: Path | None = None, substituir: bool = True
) -> dict[str, int]:
    """Carrega os Parquet de volta para o banco.

    Com `substituir`, cada tabela é esvaziada antes — é o caso do container
    novo, em que o banco está vazio de qualquer forma. Sem ele, as linhas são
    acrescentadas pelo caminho normal de upsert, respeitando a chave primária.
    """
    origem = origem or diretorio_padrao()
    resumo: dict[str, int] = {}

    if not origem.exists():
        logger.info("sem diretório de snapshots em %s", origem)
        return resumo

    db.init_db(con)

    for tabela in PARTICAO:
        pasta = origem / tabela
        arquivos = sorted(pasta.glob("*.parquet")) if pasta.exists() else []
        if not arquivos:
            continue

        colunas = ", ".join(db.table_columns(tabela))
        padrao = (pasta / "*.parquet").as_posix()

        if substituir:
            con.execute(f"DELETE FROM {tabela}")
            con.execute(
                f"INSERT INTO {tabela} SELECT {colunas} FROM read_parquet('{padrao}')"
            )
        else:
            lote = con.execute(
                f"SELECT {colunas} FROM read_parquet('{padrao}')"
            ).df()
            db.upsert(con, tabela, lote)

        resumo[tabela] = con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
        logger.info("importado %s: %d linhas de %d arquivos", tabela, resumo[tabela], len(arquivos))

    log = _importar_log(con, origem)
    if log:
        resumo["ingest_log"] = log

    return resumo


def _importar_log(con: duckdb.DuckDBPyConnection, origem: Path) -> int:
    """Acrescenta o registro de ingestão, sem nunca apagar o que já existe.

    Diferente das tabelas de dados: aqui o histórico local do usuário convive
    com o registro da coleta feita na integração contínua, e a chave primária
    (fonte, executado_em) evita duplicata.
    """
    arquivo = origem / ARQUIVO_LOG
    if not arquivo.exists():
        return 0

    colunas = ", ".join(db.table_columns("ingest_log"))
    lote = con.execute(
        f"SELECT {colunas} FROM read_parquet('{arquivo.as_posix()}')"
    ).df()
    return db.upsert(con, "ingest_log", lote)


def tamanho(origem: Path | None = None) -> int:
    """Soma dos bytes dos snapshots, para relatar no CLI."""
    origem = origem or diretorio_padrao()
    if not origem.exists():
        return 0
    return sum(arquivo.stat().st_size for arquivo in origem.glob("*/*.parquet"))
