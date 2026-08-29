"""Teste de fumaça da interface.

Executa de fato cada uma das dez telas com o runtime do Streamlit, contra o
banco populado pela ingestão offline, e verifica que nenhuma levanta exceção.
É o teste que pega quebra de coluna renomeada, chamada de API mudada e erro de
digitação em nome de série — coisas que nenhum teste unitário alcança.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

UI = Path(__file__).resolve().parents[1] / "src" / "tesouraria" / "ui"

PAGINAS = [
    UI / "app.py",
    *sorted((UI / "pages").glob("*.py")),
]

TEMPO_LIMITE = 120


def rotulo(caminho: Path) -> str:
    return caminho.stem


@pytest.mark.parametrize("pagina", PAGINAS, ids=rotulo)
def test_pagina_renderiza_sem_excecao(pagina, ambiente_ingerido):
    app = AppTest.from_file(str(pagina), default_timeout=TEMPO_LIMITE)
    app.run()

    assert not app.exception, (
        f"{pagina.name} levantou exceção: "
        + " | ".join(str(e.message) for e in app.exception)
    )


def test_todas_as_paginas_estao_cobertas():
    """Uma página nova entra no teste sozinha; esta asserção garante que não passou despercebida."""
    assert len(PAGINAS) == 10


@pytest.mark.parametrize("pagina", PAGINAS, ids=rotulo)
def test_pagina_produz_conteudo(pagina, ambiente_ingerido):
    """Renderizar sem erro não basta: a tela precisa mostrar alguma coisa."""
    app = AppTest.from_file(str(pagina), default_timeout=TEMPO_LIMITE)
    app.run()

    tem_conteudo = bool(
        len(app.markdown) or len(app.dataframe) or len(app.metric) or len(app.caption)
    )
    assert tem_conteudo, f"{pagina.name} não renderizou nenhum elemento"


def test_aviso_de_modo_offline_aparece(ambiente_ingerido):
    """O aviso de dados sintéticos não pode sumir: é o que evita confundi-los com dados reais."""
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TEMPO_LIMITE)
    app.run()

    textos = " ".join(str(w.value) for w in app.warning)
    assert "Modo offline" in textos
    assert "Não são dados reais" in textos
