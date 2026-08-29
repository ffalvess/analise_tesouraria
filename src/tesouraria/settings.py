"""Configuração central do aplicativo.

Resolve caminhos, lê variáveis de ambiente (via `.env`) e carrega os arquivos
YAML de `config/`, que concentram URLs, códigos de série e listas de feeds.
Manter esses valores fora do código é o que permite corrigir um endpoint que
mudou sem tocar em Python.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/tesouraria/settings.py -> raiz do repositório
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Parâmetros de execução, sobrescrevíveis por variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_prefix="TESOURARIA_",
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Modo offline: as fontes leem `data/fixtures/` em vez da rede. Permite
    # rodar a ingestão inteira e navegar pelo app sem conexão.
    offline: bool = False

    root_dir: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    config_dir: Path = ROOT_DIR / "config"

    # Chave opcional: sem ela, as séries do FRED são simplesmente puladas e
    # registradas como tal em `ingest_log`.
    fred_api_key: str | None = None

    http_timeout: float = 30.0
    http_retries: int = 3
    http_cache_ttl_hours: float = 6.0
    user_agent: str = "analise-tesouraria/0.1 (+https://github.com/ffalvess/analise_tesouraria)"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tesouraria.duckdb"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def research_pdfs_dir(self) -> Path:
        return self.data_dir / "research_pdfs"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.research_pdfs_dir):
            path.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@functools.lru_cache(maxsize=8)
def load_config(name: str) -> dict[str, Any]:
    """Carrega um YAML de `config/` pelo nome sem extensão."""
    path = get_settings().config_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"arquivo de configuração ausente: {path}")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def source_config(source_name: str) -> dict[str, Any]:
    """Bloco de `config/sources.yaml` referente a uma fonte."""
    sources = load_config("sources").get("sources", {})
    return sources.get(source_name, {})
