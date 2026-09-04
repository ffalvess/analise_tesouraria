"""Testes do inspetor de resposta.

A promessa aqui é estreita e importante: **descrever sem interpretar**. Um
inspetor que engasga com o que recebe não serve para nada, porque o caso em que
ele é chamado é justamente aquele em que o conteúdo não é o esperado — página
de erro no lugar do boletim, HTML no lugar de planilha, resposta vazia.
"""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from tesouraria import inspecao


def texto_de(linhas: list[str]) -> str:
    return "\n".join(linhas)


def test_descreve_planilha_com_abas_e_primeiras_linhas():
    """É o que se precisa saber antes de escrever o parser da planilha."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        pd.DataFrame({"Data": ["2026-08-28"], "Comercial": [1234.5]}).to_excel(
            escritor, sheet_name="Câmbio contratado", index=False
        )
        pd.DataFrame({"x": [1]}).to_excel(escritor, sheet_name="Notas", index=False)

    linhas = texto_de(inspecao.descrever(buffer.getvalue(), content_type="application/xlsx"))

    assert "planilha xlsx com 2 aba(s)" in linhas
    assert "Câmbio contratado" in linhas
    assert "Comercial" in linhas
    assert "Notas" in linhas


def test_planilha_e_detectada_pela_assinatura_e_nao_pelo_content_type():
    """Servidor de órgão público manda `octet-stream` para planilha."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        pd.DataFrame({"a": [1]}).to_excel(escritor, index=False)

    linhas = texto_de(
        inspecao.descrever(buffer.getvalue(), content_type="application/octet-stream")
    )
    assert "planilha xlsx" in linhas


def test_descreve_pagina_de_erro_com_titulo_e_texto():
    """O caso da B3: status 200 e uma página dizendo que não deu."""
    pagina = (
        "<html><head><title>Erro</title></head>"
        "<body><p>Não foi possível realizar a operação.</p></body></html>"
    ).encode("latin-1")

    linhas = texto_de(inspecao.descrever(pagina, content_type="text/html"))

    assert "título: Erro" in linhas
    assert "possível realizar a operação" in linhas


def test_filtro_reduz_os_links_ao_que_interessa():
    """Achar o arquivo a partir da página que o lista é o uso principal."""
    pagina = """
    <html><head><title>Estatísticas</title></head><body>
      <a href="/content/estatisticas/cambio_contratado_2026.xls">planilha</a>
      <a href="/content/institucional/quemsomos.html">quem somos</a>
      <a href="/content/estatisticas/cambio_contratado_2025.xls">anterior</a>
    </body></html>
    """.encode()

    com_filtro = texto_de(inspecao.descrever(pagina, filtro="cambio"))
    assert "links com 'cambio': 2" in com_filtro
    assert "quemsomos" not in com_filtro

    sem_filtro = texto_de(inspecao.descrever(pagina))
    assert "quemsomos" in sem_filtro


def test_descreve_json():
    corpo = json.dumps({"result": {"count": 3, "results": []}}).encode()
    linhas = texto_de(inspecao.descrever(corpo, content_type="application/json"))
    assert "json: objeto" in linhas
    assert "result" in linhas


def test_descreve_csv_com_cabecalho():
    corpo = b"data;comercial;financeiro\n2026-08-28;100;-50\n2026-08-21;90;-40\n"
    linhas = texto_de(inspecao.descrever(corpo, content_type="text/csv"))
    assert "delimitado por ';'" in linhas
    assert "data;comercial;financeiro" in linhas


@pytest.mark.parametrize(
    "conteudo",
    [b"", b"\x00\x01\x02\x03 lixo binario", b"%PDF-1.7 conteudo"],
)
def test_nunca_levanta_com_conteudo_inesperado(conteudo):
    """O inspetor é chamado quando algo deu errado; engasgar seria inútil."""
    linhas = inspecao.descrever(conteudo, url="https://exemplo/x")
    assert any("tamanho" in linha for linha in linhas)


def test_planilha_corrompida_vira_descricao_e_nao_excecao():
    """Assinatura de xlsx com corpo quebrado: relatar, não estourar."""
    linhas = texto_de(inspecao.descrever(b"PK\x03\x04" + b"lixo" * 20))
    assert "ilegível" in linhas


def test_le_latin1_sem_charset_declarado():
    """O boletim da B3 é latin-1 e não declara; `N�o foi poss�vel` não serve."""
    pagina = "<html><title>Erro</title><p>Não foi possível</p></html>".encode("latin-1")
    linhas = texto_de(inspecao.descrever(pagina, content_type="text/html"))

    assert "Não foi possível" in linhas
    assert "�" not in linhas


def test_respeita_o_charset_declarado():
    pagina = "<html><title>Ação</title></html>".encode("iso-8859-1")
    linhas = texto_de(
        inspecao.descrever(pagina, content_type="text/html; charset=iso-8859-1")
    )
    assert "Ação" in linhas


def test_desfaz_entidades_html():
    """O log da B3 saiu com `N&atilde;o foi poss&iacute;vel` — ilegível."""
    pagina = b"<html><title>Erro</title><body>N&atilde;o foi poss&iacute;vel</body></html>"
    assert "Não foi possível" in texto_de(inspecao.descrever(pagina, content_type="text/html"))
