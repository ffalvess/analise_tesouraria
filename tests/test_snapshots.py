"""Testes da exportação e importação de snapshots Parquet.

Dois deles guardam promessas que sustentam a publicação:

* **ida e volta** — o banco reconstruído a partir dos Parquet tem de ser
  idêntico ao original, senão o aplicativo publicado mostraria outra coisa;
* **determinismo** — exportar duas vezes o mesmo conteúdo tem de produzir os
  mesmos bytes. É o que faz o git guardar um único blob por mês; sem isso, o
  histórico do repositório cresceria mais de um gigabyte por ano.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from tesouraria import db, snapshots


def hash_de(diretorio: Path) -> dict[str, str]:
    """Mapa arquivo -> sha256, para comparar exportações."""
    return {
        str(caminho.relative_to(diretorio)): hashlib.sha256(caminho.read_bytes()).hexdigest()
        for caminho in sorted(diretorio.rglob("*.parquet"))
    }


def conteudo(con: duckdb.DuckDBPyConnection, tabela: str) -> tuple[int, int | None]:
    """Contagem e soma de hashes das linhas — detecta diferença de valor, não só de volume."""
    return con.execute(
        f"SELECT COUNT(*), sum(hash(t::VARCHAR)) FROM {tabela} t"
    ).fetchone()


@pytest.fixture
def banco_com_dados(banco_temporario):
    """Banco temporário populado com algumas linhas de cada tabela."""
    con = db.connect()

    db.upsert(
        con,
        "curve_br",
        pd.DataFrame(
            {
                # Duas datas em meses diferentes, para exercitar a partição.
                "data_ref": [dt.date(2026, 7, 31), dt.date(2026, 8, 28)],
                "fonte": ["tesouro"] * 2,
                "tipo": ["pre"] * 2,
                "instrumento": ["LTN 2029", "LTN 2029"],
                "vencimento": [dt.date(2029, 1, 1)] * 2,
                "prazo_du": [600, 590],
                "prazo_anos": [2.4, 2.35],
                "taxa": [13.5, 13.7],
                "preco": [800.0, 798.0],
            }
        ),
    )
    db.upsert(
        con,
        "curve_us",
        pd.DataFrame(
            {
                "data_ref": [dt.date(2026, 8, 28)],
                "tipo": ["nominal"],
                "tenor": ["10 YR"],
                "prazo_anos": [10.0],
                "taxa": [4.2],
            }
        ),
    )
    db.upsert(
        con,
        "documentos",
        pd.DataFrame(
            {
                "id": ["a" * 32, "b" * 32],
                "fonte": ["Fed"] * 2,
                "instituicao": ["Fed"] * 2,
                "autor": [None, None],
                "titulo": ["com data", "sem data"],
                # O segundo não tem data: precisa cair na partição `sem-data`.
                "data_pub": [dt.date(2026, 8, 20), None],
                "url": ["https://x.invalido/1", "https://x.invalido/2"],
                "tipo": ["discurso"] * 2,
                "idioma": ["en"] * 2,
                "texto": ["restrictive stance", "easing cycle"],
                "score_tom": [0.8, -0.8],
                "n_hawk": [1, 0],
                "n_dove": [0, 1],
            }
        ),
    )
    db.log_ingest(con, "tesouro_direto", "ok", 2, "fixture")

    yield con
    con.close()


# ------------------------------------------------------------------ formato


def test_exportar_particiona_por_mes(banco_com_dados, banco_temporario):
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    arquivos = {p.name for p in (destino / "curve_br").glob("*.parquet")}
    assert arquivos == {"2026-07.parquet", "2026-08.parquet"}


def test_documento_sem_data_vai_para_particao_propria(banco_com_dados, banco_temporario):
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    arquivos = {p.name for p in (destino / "documentos").glob("*.parquet")}
    assert arquivos == {"2026-08.parquet", "sem-data.parquet"}


def test_ingest_log_sai_em_arquivo_unico(banco_com_dados, banco_temporario):
    """O registro de ingestão alimenta o rodapé de frescor no app publicado."""
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    assert (destino / snapshots.ARQUIVO_LOG).exists()


def test_ingest_log_guarda_so_a_ultima_execucao(banco_com_dados, banco_temporario):
    db.log_ingest(banco_com_dados, "tesouro_direto", "ok", 99, "rede")
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    linhas = duckdb.sql(
        f"SELECT linhas FROM read_parquet('{(destino / snapshots.ARQUIVO_LOG).as_posix()}')"
    ).fetchall()
    assert linhas == [(99,)]


# ------------------------------------------------------------- determinismo


def test_exportar_duas_vezes_produz_os_mesmos_bytes(banco_com_dados, banco_temporario):
    """A propriedade da qual depende o tamanho do repositório."""
    destino = banco_temporario / "snapshots"

    snapshots.exportar(banco_com_dados, destino)
    primeiro = hash_de(destino)

    snapshots.exportar(banco_com_dados, destino)
    segundo = hash_de(destino)

    assert primeiro == segundo


def test_mes_antigo_nao_muda_quando_chega_dado_novo(banco_com_dados, banco_temporario):
    """Só o arquivo do mês afetado pode mudar — é isso que evita o inchaço."""
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)
    antes = hash_de(destino)

    db.upsert(
        banco_com_dados,
        "curve_br",
        pd.DataFrame(
            {
                "data_ref": [dt.date(2026, 9, 1)],
                "fonte": ["tesouro"],
                "tipo": ["pre"],
                "instrumento": ["LTN 2029"],
                "vencimento": [dt.date(2029, 1, 1)],
                "prazo_du": [585],
                "prazo_anos": [2.32],
                "taxa": [13.9],
                "preco": [797.0],
            }
        ),
    )
    snapshots.exportar(banco_com_dados, destino)
    depois = hash_de(destino)

    assert depois["curve_br/2026-07.parquet"] == antes["curve_br/2026-07.parquet"]
    assert depois["curve_br/2026-08.parquet"] == antes["curve_br/2026-08.parquet"]
    assert "curve_br/2026-09.parquet" in depois


def test_exportar_remove_mes_que_deixou_de_existir(banco_com_dados, banco_temporario):
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)
    assert (destino / "curve_br" / "2026-07.parquet").exists()

    banco_com_dados.execute("DELETE FROM curve_br WHERE data_ref < '2026-08-01'")
    snapshots.exportar(banco_com_dados, destino)

    assert not (destino / "curve_br" / "2026-07.parquet").exists()


# --------------------------------------------------------------- ida e volta


def test_ida_e_volta_preserva_o_conteudo(banco_com_dados, banco_temporario, tmp_path):
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    original = {t: conteudo(banco_com_dados, t) for t in snapshots.PARTICAO}

    # Banco novo, como num container recém-criado.
    novo = duckdb.connect(str(tmp_path / "vazio.duckdb"))
    db.init_db(novo)
    snapshots.importar(novo, destino)

    for tabela, esperado in original.items():
        assert conteudo(novo, tabela) == esperado, f"{tabela} divergiu"
    novo.close()


def test_importar_e_idempotente(banco_com_dados, banco_temporario, tmp_path):
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    novo = duckdb.connect(str(tmp_path / "vazio.duckdb"))
    db.init_db(novo)
    snapshots.importar(novo, destino)
    primeiro = conteudo(novo, "curve_br")
    snapshots.importar(novo, destino)

    assert conteudo(novo, "curve_br") == primeiro
    novo.close()


def test_importar_log_nao_apaga_o_historico_local(banco_com_dados, banco_temporario, tmp_path):
    """O registro local do usuário convive com o da integração contínua."""
    destino = banco_temporario / "snapshots"
    snapshots.exportar(banco_com_dados, destino)

    novo = duckdb.connect(str(tmp_path / "vazio.duckdb"))
    db.init_db(novo)
    db.log_ingest(novo, "focus", "ok", 10, "rede")
    snapshots.importar(novo, destino)

    fontes = {linha[0] for linha in novo.execute("SELECT fonte FROM ingest_log").fetchall()}
    assert fontes == {"focus", "tesouro_direto"}
    novo.close()


# ------------------------------------------------------------------ ausência


def test_importar_de_diretorio_inexistente(banco_com_dados, tmp_path):
    assert snapshots.importar(banco_com_dados, tmp_path / "nao-existe") == {}


def test_tem_snapshots(banco_com_dados, banco_temporario, tmp_path):
    destino = banco_temporario / "snapshots"

    assert not snapshots.tem_snapshots(tmp_path / "nao-existe")
    assert not snapshots.tem_snapshots(destino)

    snapshots.exportar(banco_com_dados, destino)
    assert snapshots.tem_snapshots(destino)


def test_apenas_ingest_log_nao_conta_como_snapshot(banco_temporario, tmp_path):
    """Um registro de ingestão solto não hidrata nada; a interface não deve tentar."""
    (tmp_path / "ingest_log").mkdir(parents=True)
    (tmp_path / "ingest_log" / "ultimo.parquet").write_bytes(b"")

    assert not snapshots.tem_snapshots(tmp_path)


# ---------------------------------------------------------------------- CLI


def test_cli_export_e_import(banco_com_dados, banco_temporario, capsys):
    from tesouraria.cli import main

    banco_com_dados.close()  # o CLI abre a sua própria conexão de escrita
    destino = banco_temporario / "snapshots"

    assert main(["snapshot", "export", "--dir", str(destino)]) == 0
    assert "linhas exportadas" in capsys.readouterr().out
    assert snapshots.tem_snapshots(destino)

    assert main(["snapshot", "import", "--dir", str(destino)]) == 0
    assert "linhas importadas" in capsys.readouterr().out


def test_cli_import_sem_snapshots(banco_temporario, capsys, tmp_path):
    from tesouraria.cli import main

    assert main(["snapshot", "import", "--dir", str(tmp_path / "vazio")]) == 0
    assert "Nada a fazer" in capsys.readouterr().out


def test_tamanho_em_disco(banco_com_dados, banco_temporario):
    destino = banco_temporario / "snapshots"
    assert snapshots.tamanho(destino) == 0

    snapshots.exportar(banco_com_dados, destino)
    assert snapshots.tamanho(destino) > 0
