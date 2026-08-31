"""Teste de fumaça da interface.

Executa de fato cada uma das dez telas com o runtime do Streamlit, contra o
banco populado pela ingestão offline, e verifica que nenhuma levanta exceção.
É o teste que pega quebra de coluna renomeada, chamada de API mudada e erro de
digitação em nome de série — coisas que nenhum teste unitário alcança.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

RAIZ = Path(__file__).resolve().parents[1]
UI = RAIZ / "src" / "tesouraria" / "ui"
FIXTURES = RAIZ / "data" / "fixtures"
SNAPSHOTS = RAIZ / "data" / "snapshots"

# Um clone sem os Parquet versionados não pode falhar por ausência de dados.
TEM_SNAPSHOTS = any(SNAPSHOTS.glob("*/*.parquet"))

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


@pytest.fixture
def ambiente_producao(tmp_path):
    """O container publicado, com os snapshots **reais** do repositório.

    A diferença para `ambiente_so_com_snapshots` é o que este arnês existe para
    cobrir: aquele exporta Parquet a partir das mesmas amostras sintéticas, em
    que toda série declarada existe e nenhuma tabela está vazia. Os dados reais
    têm buracos — séries que a coleta não trouxe, fontes desativadas — e buraco
    é o que quebra tela. Foi assim que a página de fluxo cambial chegou
    publicada com traceback: nenhum teste jamais executou o caminho em que uma
    série que o código espera simplesmente não está no banco.
    """
    from tesouraria.settings import get_settings

    destino = tmp_path / "dados"
    destino.mkdir()
    (destino / "fixtures").symlink_to(FIXTURES, target_is_directory=True)
    shutil.copytree(SNAPSHOTS, destino / "snapshots")

    anterior = os.environ.get("TESOURARIA_DATA_DIR")
    offline_anterior = os.environ.pop("TESOURARIA_OFFLINE", None)
    os.environ["TESOURARIA_DATA_DIR"] = str(destino)
    get_settings.cache_clear()
    st.cache_resource.clear()
    st.cache_data.clear()

    assert not (destino / "tesouraria.duckdb").exists()
    yield destino

    if anterior is None:
        os.environ.pop("TESOURARIA_DATA_DIR", None)
    else:
        os.environ["TESOURARIA_DATA_DIR"] = anterior
    if offline_anterior is not None:
        os.environ["TESOURARIA_OFFLINE"] = offline_anterior
    get_settings.cache_clear()
    st.cache_resource.clear()
    st.cache_data.clear()


@pytest.mark.skipif(not TEM_SNAPSHOTS, reason="sem snapshots reais no repositório")
@pytest.mark.parametrize("pagina", PAGINAS, ids=rotulo)
def test_pagina_renderiza_com_os_dados_reais(pagina, ambiente_producao):
    """As dez telas contra os dados que estão publicados de verdade.

    O teste irmão, sobre as amostras, garante que a lógica funciona quando tudo
    está presente. Este garante que ela não explode quando não está.
    """
    app = AppTest.from_file(str(pagina), default_timeout=TEMPO_LIMITE)
    app.run()

    assert not app.exception, (
        f"{pagina.name} levantou exceção com os dados reais: "
        + " | ".join(str(e.message) for e in app.exception)
    )


@pytest.mark.skipif(not TEM_SNAPSHOTS, reason="sem snapshots reais no repositório")
def test_fluxo_cambial_entrega_analise_com_os_dados_reais(ambiente_producao):
    """Renderizar sem exceção não é o critério: a tela precisa analisar algo.

    Foi exatamente assim que esta página chegou publicada — sem erro nenhum, e
    sem uma única conta: a fonte de fluxo está desativada e as cestas que o
    prêmio preferia não tinham sido coletadas, então sobravam dois avisos. Um
    teste que só olha `app.exception` chama isso de sucesso.
    """
    app = AppTest.from_file(str(UI / "pages" / "5_Fluxo_Cambial.py"), default_timeout=TEMPO_LIMITE)
    app.run()

    assert not app.exception, " | ".join(str(e.message) for e in app.exception)

    rotulos = [m.label for m in app.metric]
    assert any("Prêmio" in r for r in rotulos), (
        f"a página não calculou nenhum prêmio; métricas presentes: {rotulos}"
    )
    assert any("Dólar" in r for r in rotulos)


@pytest.fixture
def ambiente_so_com_snapshots(ambiente_ingerido, tmp_path):
    """Diretório com snapshots e sem banco — o estado de um container recém-criado.

    É a situação exata do Streamlit Community Cloud a cada redeploy: o disco é
    efêmero, o DuckDB nasce vazio e só os Parquet versionados sobreviveram.
    """
    from tesouraria import db, snapshots
    from tesouraria.settings import get_settings

    # Exporta a partir do banco já ingerido pela fixture de sessão.
    origem = tmp_path / "snapshots"
    with db.connection() as con:
        snapshots.exportar(con, origem)

    destino = tmp_path / "dados"
    destino.mkdir()
    (destino / "fixtures").symlink_to(
        get_settings().fixtures_dir, target_is_directory=True
    )
    shutil.copytree(origem, destino / "snapshots")

    anterior = os.environ.get("TESOURARIA_DATA_DIR")
    offline_anterior = os.environ.pop("TESOURARIA_OFFLINE", None)
    os.environ["TESOURARIA_DATA_DIR"] = str(destino)
    # Sem modo offline: é assim que o aplicativo roda publicado.
    get_settings.cache_clear()
    # As telas anteriores já povoaram os caches do Streamlit.
    st.cache_resource.clear()
    st.cache_data.clear()

    assert not (destino / "tesouraria.duckdb").exists()
    yield destino

    if anterior is None:
        os.environ.pop("TESOURARIA_DATA_DIR", None)
    else:
        os.environ["TESOURARIA_DATA_DIR"] = anterior
    if offline_anterior is not None:
        os.environ["TESOURARIA_OFFLINE"] = offline_anterior
    get_settings.cache_clear()
    st.cache_resource.clear()
    st.cache_data.clear()


def test_app_hidrata_a_partir_dos_snapshots(ambiente_so_com_snapshots):
    """Sem esta hidratação, o aplicativo publicado subiria sem dado nenhum."""
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TEMPO_LIMITE)
    app.run()

    assert not app.exception, " | ".join(str(e.message) for e in app.exception)

    textos = " ".join(str(i.value) for i in app.info)
    assert "Nenhum dado no banco" not in textos, "a hidratação não ocorreu"
    # O painel só monta os indicadores quando encontra as duas curvas.
    assert len(app.metric) >= 5


def test_snapshot_sintetico_e_denunciado_em_producao(ambiente_so_com_snapshots):
    """Fora do modo offline, nada mais denunciaria a origem dos números.

    O caminho perigoso: alguém gera snapshots a partir das amostras e os
    commita. O aplicativo publicado roda em modo normal e serviria dados
    inventados sem aviso — não fosse `ingest_log.modo` viajar no snapshot.
    """
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TEMPO_LIMITE)
    app.run()

    avisos = " ".join(str(w.value) for w in app.warning)
    assert "Dados sintéticos no banco" in avisos
    assert "Não são dados reais de mercado" in avisos


def test_painel_abre_na_curva_prefixada_do_tesouro(ambiente_so_com_snapshots):
    """O seletor não pode abrir em `anbima/implicita` só por ordem alfabética.

    Se abrisse, o diferencial do painel confrontaria a inflação implícita
    brasileira com o juro nominal americano — um número sem significado, e
    plausível o bastante para passar despercebido.
    """
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TEMPO_LIMITE)
    app.run()

    legendas = " ".join(str(c.value) for c in app.caption)
    assert "Brasil, tesouro" in legendas

    # O diferencial BR-EUA nominal fica na casa de milhares de pontos-base;
    # contra a inflação implícita cairia para poucas centenas.
    diferenciais = [
        int(m.value.replace("+", "").replace(" bps", "").replace(",", ""))
        for m in app.metric
        if m.label.startswith("Diferencial") and m.value != "—"
    ]
    assert diferenciais, "o painel não calculou nenhum diferencial"
    assert min(diferenciais) > 500, f"diferenciais suspeitos: {diferenciais}"


def test_aviso_de_modo_offline_aparece(ambiente_ingerido):
    """O aviso de dados sintéticos não pode sumir: é o que evita confundi-los com dados reais."""
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TEMPO_LIMITE)
    app.run()

    textos = " ".join(str(w.value) for w in app.warning)
    assert "Modo offline" in textos
    assert "Não são dados reais" in textos
