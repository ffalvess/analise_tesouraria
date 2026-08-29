"""Configuração comum dos testes.

Todos os testes rodam em modo offline e contra um banco temporário, para que
nenhum deles toque a rede nem o `data/tesouraria.duckdb` do usuário.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("TESOURARIA_OFFLINE", "1")

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "data" / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def ambiente_ingerido(tmp_path_factory):
    """Banco temporário já populado com uma ingestão offline completa.

    Serve aos testes de ponta a ponta e ao teste de fumaça da interface, que
    precisam de dados reais nas tabelas — sem tocar o banco do usuário.
    """
    from tesouraria.cli import main
    from tesouraria.settings import get_settings

    diretorio = tmp_path_factory.mktemp("dados")
    # As fontes leem `data_dir/fixtures`; aponta para as amostras do repositório.
    (diretorio / "fixtures").symlink_to(FIXTURES, target_is_directory=True)

    os.environ["TESOURARIA_DATA_DIR"] = str(diretorio)
    os.environ["TESOURARIA_OFFLINE"] = "1"
    get_settings.cache_clear()

    assert main(["ingest", "--all"]) == 0
    yield diretorio

    os.environ.pop("TESOURARIA_DATA_DIR", None)
    get_settings.cache_clear()


@pytest.fixture
def banco_temporario(tmp_path, monkeypatch):
    """Redireciona o banco para um diretório temporário do teste."""
    from tesouraria.settings import Settings, get_settings

    get_settings.cache_clear()

    def fabricar() -> Settings:
        return Settings(offline=True, data_dir=tmp_path, root_dir=RAIZ, config_dir=RAIZ / "config")

    monkeypatch.setattr("tesouraria.settings.get_settings", fabricar)
    monkeypatch.setattr("tesouraria.db.get_settings", fabricar)
    yield tmp_path
    get_settings.cache_clear()
