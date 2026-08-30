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
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Arquivo que identifica a raiz do projeto de forma inequívoca.
MARCADOR_RAIZ = Path("config") / "sources.yaml"


def descobrir_raiz() -> Path:
    """Localiza a raiz do projeto, onde vivem `config/` e `data/`.

    Não basta subir dois níveis a partir deste arquivo. Isso só vale na
    instalação editável (`pip install -e .`), em que o pacote fica em
    `<repo>/src/tesouraria`. Na instalação normal — que é o que a integração
    contínua e o Streamlit Community Cloud fazem, via `requirements.txt` — o
    pacote é copiado para `site-packages`, e subir dois níveis leva a
    `.../lib/python3.11/`, onde não existe nem `config/` nem `data/`.

    A busca começa pelo diretório de trabalho e sobe por seus ancestrais — é
    onde o repositório está tanto no runner da CI quanto no servidor do
    Streamlit, e dar precedência a ele torna previsível qual projeto será usado
    quando houver mais de um clone. O caminho relativo ao pacote entra por
    último, para o caso de a CLI ser chamada de fora de qualquer projeto.
    """
    cwd = Path.cwd().resolve()
    candidatos = [cwd, *cwd.parents, Path(__file__).resolve().parents[2]]

    for candidato in candidatos:
        if (candidato / MARCADOR_RAIZ).is_file():
            return candidato

    # Sem marcador em lugar nenhum, o diretório de trabalho é o melhor palpite;
    # `load_config` dirá exatamente qual arquivo faltou.
    return cwd


ROOT_DIR = descobrir_raiz()


class Settings(BaseSettings):
    """Parâmetros de execução, sobrescrevíveis por variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_prefix="TESOURARIA_",
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `fred_api_key` tem um alias explícito, que sobrepõe o prefixo. Sem
        # isto, o pydantic deixaria de aceitar o nome do campo na construção
        # direta — `Settings(fred_api_key=...)` quebraria.
        populate_by_name=True,
    )

    # Modo offline: as fontes leem `data/fixtures/` em vez da rede. Permite
    # rodar a ingestão inteira e navegar pelo app sem conexão.
    offline: bool = False

    root_dir: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    config_dir: Path = ROOT_DIR / "config"

    # Chave opcional: sem ela, as séries do FRED são simplesmente puladas e
    # registradas como tal em `ingest_log`.
    #
    # É a única configuração que precisa ser cadastrada em três lugares — no
    # `.env` local, nos secrets do GitHub Actions e nos do Streamlit —, e por
    # isso usa o mesmo nome `FRED_API_KEY` nos três, sem o prefixo do projeto.
    # Cadastrar com o nome errado não daria erro: a fonte apenas sairia como
    # `pulado` sem ninguém notar. `TESOURARIA_FRED_API_KEY` segue aceito, para
    # não quebrar um `.env` que já exista.
    fred_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FRED_API_KEY", "TESOURARIA_FRED_API_KEY"),
    )

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
