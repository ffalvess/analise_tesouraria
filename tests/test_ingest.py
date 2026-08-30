"""Testes de ponta a ponta da ingestão em modo offline.

Exercitam o caminho completo — `collect` → `upsert` → `ingest_log` — para as
doze fontes, o que é o mais próximo de uma execução real que se pode fazer sem
acesso à rede.
"""

from __future__ import annotations

import pytest

from tesouraria import db, queries, sources


def test_registro_tem_as_doze_fontes():
    assert len(sources.REGISTRO) == 12
    assert set(sources.REGISTRO) == {
        "tesouro_direto", "us_treasury", "anbima_ettj", "b3_di", "bcb_sgs",
        "fx_flow", "focus", "comex", "ibge_sidra", "us_macro", "speeches", "research",
    }


def test_criar_fonte_desconhecida():
    with pytest.raises(KeyError, match="desconhecida"):
        sources.criar("inexistente")


def test_todas_as_fontes_ingerem_sem_erro(ambiente_ingerido):
    status = db.status_report().set_index("fonte")

    assert len(status) == 12
    com_erro = status[status["status"] == "erro"]
    assert com_erro.empty, f"fontes com erro: {list(com_erro.index)}"
    assert (status["modo"] == "fixture").all()


def test_tabelas_ficam_populadas(ambiente_ingerido):
    cobertura = db.table_coverage().set_index("tabela")
    for tabela in ("curve_br", "curve_us", "series_macro", "focus", "fx_flow", "documentos"):
        assert cobertura.loc[tabela, "linhas"] > 0, f"{tabela} ficou vazia"


def test_ingestao_repetida_e_idempotente(ambiente_ingerido):
    """Rodar a ingestão de novo não pode inflar as tabelas."""
    from tesouraria.cli import main

    antes = db.table_coverage().set_index("tabela")["linhas"]
    assert main(["ingest", "--source", "tesouro_direto", "--source", "us_treasury"]) == 0
    depois = db.table_coverage().set_index("tabela")["linhas"]

    assert depois["curve_br"] == antes["curve_br"]
    assert depois["curve_us"] == antes["curve_us"]


def test_tres_fontes_alimentam_a_curva_brasileira(ambiente_ingerido):
    fontes = queries.fontes_curva_br()
    assert set(fontes["fonte"]) == {"tesouro", "anbima", "b3"}


def test_curva_do_dia_esta_ordenada(ambiente_ingerido):
    data_ref = queries.datas_disponiveis("curve_br", "tesouro", "pre")[0]
    curva = queries.curva_br(data_ref, "tesouro", "pre")

    assert not curva.empty
    assert list(curva["prazo_anos"]) == sorted(curva["prazo_anos"])


def test_curva_americana_tem_nominal_e_real(ambiente_ingerido):
    data_ref = queries.datas_disponiveis("curve_us", tipo="nominal")[0]
    assert not queries.curva_us(data_ref, "nominal").empty
    assert not queries.curva_us(data_ref, "real").empty


def test_documentos_recebem_score_de_tom(ambiente_ingerido):
    documentos = queries.documentos(limite=50)
    assert not documentos.empty
    assert documentos["score_tom"].notna().all()
    # As amostras têm textos hawkish e dovish; os dois lados devem aparecer.
    assert documentos["score_tom"].max() > 0
    assert documentos["score_tom"].min() < 0


@pytest.mark.parametrize(
    "variavel", ["FRED_API_KEY", "TESOURARIA_FRED_API_KEY"]
)
def test_chave_do_fred_aceita_os_dois_nomes(variavel, monkeypatch, tmp_path):
    """`FRED_API_KEY` é o nome documentado; o antigo segue valendo.

    Cadastrar a chave com o nome errado não daria erro — a fonte apenas sairia
    como `pulado` sem ninguém notar. Aceitar os dois nomes remove a armadilha,
    e este teste impede que a compatibilidade se perca numa refatoração.
    """
    from tesouraria.settings import Settings

    for nome in ("FRED_API_KEY", "TESOURARIA_FRED_API_KEY"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv(variavel, "chave-de-teste")

    # Um .env do repositório sobreporia o ambiente do teste.
    assert Settings(_env_file=tmp_path / "vazio.env").fred_api_key == "chave-de-teste"


def test_chave_do_fred_ausente(monkeypatch, tmp_path):
    from tesouraria.settings import Settings

    for nome in ("FRED_API_KEY", "TESOURARIA_FRED_API_KEY"):
        monkeypatch.delenv(nome, raising=False)

    assert Settings(_env_file=tmp_path / "vazio.env").fred_api_key is None


def test_fonte_sem_chave_de_api_e_pulada(ambiente_ingerido, monkeypatch):
    """Sem FRED_API_KEY e fora do modo offline, a fonte é `pulado`, não `erro`."""
    from tesouraria.settings import Settings
    from tesouraria.sources.us_macro import UsMacroSource

    fonte = UsMacroSource()
    sem_chave = lambda: Settings(offline=False, fred_api_key=None)  # noqa: E731
    # `skip_reason` consulta o modo offline pela base e a chave pelo módulo.
    monkeypatch.setattr("tesouraria.sources.base.get_settings", sem_chave)
    monkeypatch.setattr("tesouraria.sources.us_macro.get_settings", sem_chave)

    motivo = fonte.skip_reason()
    assert motivo is not None and "FRED_API_KEY" in motivo

    with db.connection() as con:
        resultado = fonte.run(con)
    assert resultado.status == "pulado"


def test_fonte_com_falha_nao_derruba_as_outras(ambiente_ingerido):
    """Um erro em `collect` vira registro em ingest_log, não exceção."""
    fonte = sources.criar("focus")
    fonte.collect = lambda since=None: (_ for _ in ()).throw(RuntimeError("servidor fora do ar"))

    with db.connection() as con:
        resultado = fonte.run(con)

    assert resultado.status == "erro"
    assert "servidor fora do ar" in resultado.erro


def test_cli_recusa_fonte_desconhecida(ambiente_ingerido):
    from tesouraria.cli import main

    assert main(["ingest", "--source", "nao_existe"]) == 2


def test_cli_status(ambiente_ingerido, capsys):
    from tesouraria.cli import main

    assert main(["status"]) == 0
    saida = capsys.readouterr().out
    assert "Última execução por fonte" in saida
    assert "Cobertura das tabelas" in saida
