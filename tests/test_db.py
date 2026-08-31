"""Testes da camada de persistência.

A propriedade que importa é a idempotência: reingerir o mesmo período tem de
substituir as linhas, nunca duplicá-las. Sem ela, uma segunda execução do
comando de ingestão contaminaria silenciosamente todo o histórico.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tesouraria import db


@pytest.fixture
def con(banco_temporario):
    conexao = db.connect()
    yield conexao
    conexao.close()


def lote(taxa: float = 13.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "data_ref": [dt.date(2026, 8, 28)] * 2,
            "fonte": ["tesouro"] * 2,
            "tipo": ["pre"] * 2,
            "instrumento": ["LTN 2029", "LTN 2031"],
            "vencimento": [dt.date(2029, 1, 1), dt.date(2031, 1, 1)],
            "prazo_du": [600, 1100],
            "prazo_anos": [2.4, 4.4],
            "taxa": [taxa, taxa + 0.2],
            "preco": [800.0, 700.0],
        }
    )


def test_schema_criado(con):
    tabelas = {linha[0] for linha in con.execute("SHOW TABLES").fetchall()}
    assert set(db.SCHEMA) <= tabelas


def test_upsert_grava(con):
    assert db.upsert(con, "curve_br", lote()) == 2
    assert con.execute("SELECT COUNT(*) FROM curve_br").fetchone()[0] == 2


def test_reingestao_nao_duplica(con):
    db.upsert(con, "curve_br", lote())
    db.upsert(con, "curve_br", lote())
    assert con.execute("SELECT COUNT(*) FROM curve_br").fetchone()[0] == 2


def test_reingestao_atualiza_o_valor(con):
    db.upsert(con, "curve_br", lote(taxa=13.5))
    db.upsert(con, "curve_br", lote(taxa=14.0))

    taxas = con.execute("SELECT taxa FROM curve_br ORDER BY prazo_anos").fetchall()
    assert [linha[0] for linha in taxas] == [14.0, 14.2]


def test_upsert_de_quadro_vazio(con):
    assert db.upsert(con, "curve_br", pd.DataFrame()) == 0


def test_upsert_preenche_colunas_ausentes(con):
    """Uma fonte que não conhece `preco` ainda tem de conseguir gravar."""
    parcial = lote().drop(columns=["preco"])
    assert db.upsert(con, "curve_br", parcial) == 2
    assert con.execute("SELECT preco FROM curve_br LIMIT 1").fetchone()[0] is None


def test_chave_com_nulo_e_tratada(con):
    """A ETTJ da ANBIMA não tem vencimento; a chave precisa lidar com NULL."""
    sem_vencimento = lote()
    sem_vencimento["vencimento"] = None

    db.upsert(con, "curve_br", sem_vencimento)
    db.upsert(con, "curve_br", sem_vencimento)
    assert con.execute("SELECT COUNT(*) FROM curve_br").fetchone()[0] == 2


def test_table_columns_le_o_ddl():
    colunas = db.table_columns("curve_us")
    assert colunas == ("data_ref", "tipo", "tenor", "prazo_anos", "taxa")


def serie(serie_id: str, n: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pais": "BR",
            "fonte": "bcb_sgs",
            "serie_id": serie_id,
            "nome": f"série {serie_id}",
            "unidade": "un",
            "data_ref": [dt.date(2026, 1, k + 1) for k in range(n)],
            "valor": [1.0 * k for k in range(n)],
        }
    )


def test_poda_remove_o_que_nao_esta_declarado(con):
    """O invariante: o banco contém apenas o que a configuração declara.

    Sem isto, uma série retirada da configuração voltaria a cada execução do
    workflow — que importa o snapshot antes de coletar. Foi o caso das séries
    que se revelaram trocadas: parariam de ser coletadas e continuariam sendo
    exibidas.
    """
    db.upsert(con, "series_macro", serie("432"))
    db.upsert(con, "series_macro", serie("2255"))  # a que se revelou trocada

    removidas = db.podar(con, {"432", "12"})

    assert removidas == {"series_macro": 3}
    restantes = {linha[0] for linha in con.execute(
        "SELECT DISTINCT serie_id FROM series_macro"
    ).fetchall()}
    assert restantes == {"432"}


def test_poda_sem_nada_a_remover(con):
    db.upsert(con, "series_macro", serie("432"))
    assert db.podar(con, {"432"}) == {}


def test_poda_com_conjunto_vazio_nao_apaga_tudo(con):
    """Salvaguarda: um bug que zerasse a lista não pode zerar o banco."""
    db.upsert(con, "series_macro", serie("432"))
    assert db.podar(con, set()) == {}
    assert con.execute("SELECT COUNT(*) FROM series_macro").fetchone()[0] == 3


def test_limpar_tabela(con):
    db.upsert(con, "curve_br", lote())
    assert db.limpar_tabela(con, "curve_br") == 2
    assert db.limpar_tabela(con, "curve_br") == 0


def test_limpar_tabela_apaga_o_registro_que_afirma_linhas(con):
    """Um `ok, 417 linhas` sobre tabela vazia mente no rodapé por meses.

    `snapshots._importar_log` nunca remove nada, então o registro viaja no
    snapshot: a mesma tela dizia que a fonte estava desativada e que a última
    coleta trouxe 417 linhas.
    """
    db.log_ingest(con, "fx_flow", "ok", 417, "rede")
    db.limpar_tabela(con, "fx_flow", fontes=("fx_flow",))

    restantes = con.execute("SELECT COUNT(*) FROM ingest_log WHERE fonte = 'fx_flow'").fetchone()
    assert restantes[0] == 0


def test_limpar_tabela_preserva_o_registro_de_pulado(con):
    """`pulado` com motivo é o que explica a ausência; some seria pior."""
    db.log_ingest(con, "fx_flow", "pulado", 0, "rede", "códigos não confirmados")
    db.limpar_tabela(con, "fx_flow", fontes=("fx_flow",))

    status = con.execute("SELECT status FROM ingest_log WHERE fonte = 'fx_flow'").fetchall()
    assert [linha[0] for linha in status] == ["pulado"]


def test_log_e_status(con, banco_temporario):
    db.log_ingest(con, "tesouro_direto", "ok", 100, "fixture")
    db.log_ingest(con, "focus", "erro", 0, "rede", "timeout")
    con.close()

    status = db.status_report()
    assert set(status["fonte"]) == {"tesouro_direto", "focus"}
    assert status.set_index("fonte").loc["focus", "erro"] == "timeout"


def test_status_mantem_apenas_a_ultima_execucao(con, banco_temporario):
    db.log_ingest(con, "focus", "erro", 0, "rede", "primeira")
    db.log_ingest(con, "focus", "ok", 50, "rede")
    con.close()

    status = db.status_report()
    assert len(status) == 1
    assert status["status"].iloc[0] == "ok"


def test_cobertura_das_tabelas(con, banco_temporario):
    db.upsert(con, "curve_br", lote())
    con.close()

    cobertura = db.table_coverage().set_index("tabela")
    assert cobertura.loc["curve_br", "linhas"] == 2
    assert cobertura.loc["curve_us", "linhas"] == 0
