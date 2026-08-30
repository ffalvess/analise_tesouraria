"""Testes da descoberta da raiz do projeto.

Guardam um invariante que quebrou em produção antes de ser pego: `config/` e
`data/` precisam ser encontrados tanto na instalação editável (`pip install -e .`,
o modo de desenvolvimento) quanto na normal (`pip install -r requirements.txt`,
o que a integração contínua e o Streamlit Community Cloud fazem).

No segundo caso o pacote é copiado para `site-packages`, e derivar a raiz de
`Path(__file__).parents[2]` passa a apontar para dentro da instalação do
Python, onde não existe nenhum dos dois diretórios. O aplicativo publicado
quebraria no primeiro acesso.
"""

from __future__ import annotations

import os
from pathlib import Path

from tesouraria import settings


def test_raiz_contem_a_configuracao():
    """O invariante que quebrou: a raiz descoberta precisa ter os arquivos."""
    assert (settings.ROOT_DIR / settings.MARCADOR_RAIZ).is_file()
    assert (settings.ROOT_DIR / "data" / "fixtures").is_dir()


def test_configuracao_carrega():
    """Prova de que a raiz serve para o que existe: ler os YAML de config/."""
    assert settings.load_config("sources")["sources"]
    assert settings.load_config("feeds")["discursos"]


def projeto_falso(base: Path) -> Path:
    (base / "config").mkdir(parents=True)
    (base / "config" / "sources.yaml").write_text("sources: {}\n", encoding="utf-8")
    return base


def test_descobre_a_raiz_a_partir_de_um_subdiretorio(tmp_path, monkeypatch):
    """Rodar de dentro de qualquer subpasta do projeto tem de funcionar."""
    raiz = projeto_falso(tmp_path / "projeto")
    fundo = raiz / "src" / "pacote" / "ui"
    fundo.mkdir(parents=True)

    monkeypatch.chdir(fundo)
    assert settings.descobrir_raiz() == raiz.resolve()


def test_diretorio_de_trabalho_tem_precedencia(tmp_path, monkeypatch):
    """Com dois projetos em jogo, vale aquele de onde o comando foi chamado."""
    raiz = projeto_falso(tmp_path / "outro-clone")

    monkeypatch.chdir(raiz)
    assert settings.descobrir_raiz() == raiz.resolve()
    # E não o repositório onde o pacote foi instalado em modo editável.
    assert settings.descobrir_raiz() != Path(__file__).resolve().parents[1]


def test_sem_projeto_algum_devolve_um_caminho_utilizavel(tmp_path, monkeypatch):
    """Sem marcador em lugar nenhum, não pode explodir na importação.

    O erro tem de vir depois, de `load_config`, dizendo qual arquivo faltou.
    """
    vazio = tmp_path / "sem-projeto"
    vazio.mkdir()
    monkeypatch.chdir(vazio)

    resultado = settings.descobrir_raiz()
    assert isinstance(resultado, Path)
    assert resultado.is_absolute()


def test_caminhos_derivam_da_raiz(tmp_path, monkeypatch):
    """`data_dir` e `config_dir` acompanham a raiz, e o ambiente os sobrepõe."""
    monkeypatch.delenv("TESOURARIA_DATA_DIR", raising=False)
    padrao = settings.Settings(_env_file=tmp_path / "vazio.env")
    assert padrao.data_dir == padrao.root_dir / "data"
    assert padrao.config_dir == padrao.root_dir / "config"

    os.environ["TESOURARIA_DATA_DIR"] = str(tmp_path / "alhures")
    try:
        sobreposto = settings.Settings(_env_file=tmp_path / "vazio.env")
        assert sobreposto.data_dir == tmp_path / "alhures"
        assert sobreposto.db_path == tmp_path / "alhures" / "tesouraria.duckdb"
    finally:
        os.environ.pop("TESOURARIA_DATA_DIR", None)
