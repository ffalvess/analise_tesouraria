"""Descreve o que uma URL devolveu, sem tentar interpretar.

Existe por causa da restrição que domina este projeto: os hosts financeiros só
respondem de dentro do workflow do GitHub. Sem enxergar a resposta, corrigir uma
fonte vira tentativa e erro caro — foi assim que uma varredura do SGS gastou uma
hora para não achar nada, enquanto a B3 se explicou na primeira execução, porque
ali o erro tinha sido mandado carregar um trecho do que veio.

Este módulo generaliza aquele trecho. Não parseia, não valida, não decide: só
conta o que chegou, com detalhe suficiente para escrever o parser depois.

A saída é texto para o log do workflow, que é onde ela vai ser lida.
"""

from __future__ import annotations

import html
import io
import json
import re

import pandas as pd

LIMITE_TEXTO = 400
LINHAS_DE_AMOSTRA = 4


def _decodificar(conteudo: bytes, content_type: str = "") -> str:
    """Texto legível, respeitando o charset declarado e caindo para latin-1.

    Um inspetor que devolve `N�o foi poss�vel` ajuda menos do que parece: a
    mensagem do servidor é o achado, e ela costuma vir acentuada. Boa parte dos
    sistemas legados brasileiros — o boletim da B3 entre eles — ainda serve
    latin-1, com ou sem declarar.
    """
    achado = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    candidatos = [achado.group(1)] if achado else []
    candidatos += ["utf-8", "latin-1"]

    for codec in candidatos:
        try:
            texto = conteudo.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        if "�" not in texto:
            return texto
    return conteudo.decode("latin-1", errors="replace")


def _visivel(texto: str, limite: int = LIMITE_TEXTO) -> str:
    """Texto sem marcação, sem entidades e sem espaço repetido."""
    sem_marcacao = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", texto, flags=re.I | re.S)
    sem_marcacao = html.unescape(re.sub(r"<[^>]+>", " ", sem_marcacao))
    return " ".join(sem_marcacao.split())[:limite]


def descrever(
    conteudo: bytes,
    *,
    url: str = "",
    content_type: str = "",
    filtro: str | None = None,
) -> list[str]:
    """Linhas descrevendo o conteúdo, do cabeçalho ao resumo estrutural.

    `filtro` restringe os links listados numa página HTML — é o que permite
    achar o arquivo a partir da página que o lista, sem despejar centenas de
    links no log.
    """
    linhas = [
        f"url .......... {url}",
        f"content-type . {content_type or '(não informado)'}",
        f"tamanho ...... {len(conteudo):,} bytes".replace(",", "."),
    ]

    for tentativa in (_como_planilha, _como_json, _como_html, _como_csv):
        resumo = tentativa(conteudo, content_type, filtro)
        if resumo:
            return linhas + [""] + resumo

    return linhas + ["", f"primeiros bytes: {conteudo[:120]!r}"]


def _como_planilha(conteudo: bytes, content_type: str, _filtro: str | None) -> list[str] | None:
    """Abas e primeiras linhas — o que se precisa para escrever o parser.

    A detecção é pela assinatura do arquivo, não pelo `Content-Type`: servidor
    de órgão público costuma devolver `application/octet-stream` para planilha.
    """
    xlsx = conteudo[:4] == b"PK\x03\x04"
    xls = conteudo[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    if not (xlsx or xls):
        return None

    formato = "xlsx" if xlsx else "xls"
    try:
        livro = pd.ExcelFile(io.BytesIO(conteudo))
    except Exception as exc:  # noqa: BLE001 — planilha corrompida ainda é resposta
        return [f"planilha {formato}, ilegível: {type(exc).__name__}: {exc}"]

    linhas = [f"planilha {formato} com {len(livro.sheet_names)} aba(s):"]
    for nome in livro.sheet_names:
        try:
            amostra = livro.parse(nome, header=None, nrows=LINHAS_DE_AMOSTRA)
        except Exception as exc:  # noqa: BLE001
            linhas.append(f"  · {nome!r}: ilegível ({type(exc).__name__})")
            continue

        linhas.append(f"  · {nome!r} — {amostra.shape[1]} colunas")
        for _, linha in amostra.iterrows():
            celulas = [str(c) for c in linha.tolist() if str(c) not in ("nan", "NaT", "")]
            if celulas:
                linhas.append(f"      {' | '.join(celulas)[:160]}")
    return linhas


def _como_json(conteudo: bytes, content_type: str, _filtro: str | None) -> list[str] | None:
    if "json" not in content_type.lower() and conteudo.lstrip()[:1] not in (b"{", b"["):
        return None
    try:
        dados = json.loads(_decodificar(conteudo, content_type))
    except ValueError:
        return None

    if isinstance(dados, list):
        primeiro = dados[0] if dados else None
        return [
            f"json: lista com {len(dados)} itens",
            f"  primeiro item: {str(primeiro)[:LIMITE_TEXTO]}",
        ]
    return [
        f"json: objeto com as chaves {list(dados)[:20]}",
        f"  amostra: {str(dados)[:LIMITE_TEXTO]}",
    ]


def _como_html(conteudo: bytes, content_type: str, filtro: str | None) -> list[str] | None:
    texto = _decodificar(conteudo, content_type)
    if "<html" not in texto[:2000].lower() and "html" not in content_type.lower():
        return None

    titulo = re.search(r"<title[^>]*>(.*?)</title>", texto, re.IGNORECASE | re.DOTALL)
    linhas = [
        f"html · título: {titulo.group(1).strip() if titulo else '(sem título)'}",
        f"      tabelas: {len(re.findall(r'<table', texto, re.IGNORECASE))}",
        f"      texto: {_visivel(texto)}",
    ]

    # Os links são o motivo de espiar uma página: é neles que está o arquivo.
    links = re.findall(r'href=["\']([^"\']+)["\']', texto, re.IGNORECASE)
    if filtro:
        agulha = filtro.lower()
        links = [link for link in links if agulha in link.lower()]
    vistos = list(dict.fromkeys(links))

    rotulo = f"links com {filtro!r}" if filtro else "links"
    linhas.append(f"      {rotulo}: {len(vistos)}")
    linhas += [f"        {link[:150]}" for link in vistos[:40]]
    if len(vistos) > 40:
        linhas.append(f"        ... e mais {len(vistos) - 40}")
    return linhas


def _como_csv(conteudo: bytes, content_type: str, _filtro: str | None) -> list[str] | None:
    texto = _decodificar(conteudo, content_type)
    primeiras = [linha for linha in texto.splitlines()[:LINHAS_DE_AMOSTRA] if linha.strip()]
    if not primeiras:
        return None

    separador = max(";,\t", key=primeiras[0].count)
    if primeiras[0].count(separador) < 1:
        return None

    return [
        f"texto delimitado por {separador!r}, {len(texto.splitlines()):,} linhas".replace(",", "."),
        *[f"  {linha[:180]}" for linha in primeiras],
    ]
