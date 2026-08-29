"""Discursos, atas e comunicados do Fed e do Banco Central.

Comunicação é o instrumento que move a curva antes de qualquer decisão sair —
por isso ela entra aqui com o mesmo peso que os dados. Cada documento recebe o
score de tom do léxico, o que transforma uma lista de discursos numa série
temporal comparável à curva de juros.

Dois formatos de feed convivem: RSS (Fed) e o feed JSON do site do Banco
Central. Os textos completos são baixados apenas para documentos ainda não
indexados, e no máximo `MAX_TEXTOS` por execução.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from typing import Any

import duckdb
import feedparser
import pandas as pd
from bs4 import BeautifulSoup

from tesouraria.analytics.tone import pontuar
from tesouraria.http import fetch
from tesouraria.settings import load_config
from tesouraria.sources.base import Source

logger = logging.getLogger(__name__)

MAX_TEXTOS = 40
SECAO = "discursos"


def doc_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def limpar_html(bruto: str) -> str:
    if not bruto:
        return ""
    return BeautifulSoup(bruto, "lxml").get_text(" ", strip=True)


def parse_rss(conteudo: bytes, feed: dict[str, Any]) -> list[dict[str, Any]]:
    analisado = feedparser.parse(conteudo)
    itens: list[dict[str, Any]] = []
    for entrada in analisado.entries:
        url = entrada.get("link") or ""
        if not url:
            continue
        itens.append(
            {
                "id": doc_id(url),
                "fonte": feed["nome"],
                "instituicao": feed.get("instituicao"),
                "autor": entrada.get("author") or None,
                "titulo": limpar_html(entrada.get("title") or ""),
                "data_pub": _data_da_entrada(entrada),
                "url": url,
                "tipo": feed.get("tipo", "discurso"),
                "idioma": feed.get("idioma", "en"),
                "texto": limpar_html(
                    entrada.get("summary") or entrada.get("description") or ""
                ),
            }
        )
    return itens


def parse_bcb_json(conteudo: bytes, feed: dict[str, Any]) -> list[dict[str, Any]]:
    """Feed JSON do site do BCB: uma lista sob a chave `conteudo`."""
    payload = json.loads(conteudo.decode("utf-8", errors="replace"))
    registros = payload.get("conteudo", payload if isinstance(payload, list) else [])

    itens: list[dict[str, Any]] = []
    for registro in registros:
        caminho = (
            registro.get("Url")
            or registro.get("url")
            or registro.get("caminho")
            or registro.get("Caminho")
            or ""
        )
        if caminho and caminho.startswith("/"):
            caminho = "https://www.bcb.gov.br" + caminho
        if not caminho:
            continue

        titulo = registro.get("titulo") or registro.get("Titulo") or ""
        itens.append(
            {
                "id": doc_id(caminho),
                "fonte": feed["nome"],
                "instituicao": feed.get("instituicao", "BCB"),
                "autor": registro.get("autoridade") or registro.get("Autoridade") or None,
                "titulo": limpar_html(titulo),
                "data_pub": _para_data(
                    registro.get("DataPublicacao")
                    or registro.get("dataPublicacao")
                    or registro.get("data")
                ),
                "url": caminho,
                "tipo": feed.get("tipo", "discurso"),
                "idioma": feed.get("idioma", "pt"),
                "texto": limpar_html(
                    registro.get("textoInformacao")
                    or registro.get("TextoInformacao")
                    or registro.get("resumo")
                    or ""
                ),
            }
        )
    return itens


def _data_da_entrada(entrada: Any) -> dt.date | None:
    for campo in ("published_parsed", "updated_parsed"):
        valor = entrada.get(campo)
        if valor:
            return dt.date(valor[0], valor[1], valor[2])
    return _para_data(entrada.get("published") or entrada.get("updated"))


def _para_data(valor: Any) -> dt.date | None:
    if not valor:
        return None
    convertido = pd.to_datetime(valor, errors="coerce", utc=True)
    return None if pd.isna(convertido) else convertido.date()


class SpeechesSource(Source):
    name = "speeches"
    table = "documentos"
    secao = SECAO
    fixture_name = "feeds_discursos.json"

    def __init__(self) -> None:
        self._ja_indexados: set[str] = set()

    def prepare(self, con: duckdb.DuckDBPyConnection) -> None:
        linhas = con.execute(
            "SELECT id FROM documentos WHERE texto IS NOT NULL AND length(texto) > 400"
        ).fetchall()
        self._ja_indexados = {linha[0] for linha in linhas}

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        feeds = load_config("feeds").get(self.secao, [])

        if self.offline:
            itens = json.loads(self.fixture(self.fixture_name).decode("utf-8"))
        else:
            itens = self._coletar_feeds(feeds)

        if not itens:
            return pd.DataFrame()

        df = pd.DataFrame(itens).drop_duplicates(subset=["id"], keep="first")
        df = self._aplicar_filtros(df, feeds)

        if since is not None:
            df = df[df["data_pub"].isna() | (df["data_pub"] >= since)]
        if df.empty:
            return pd.DataFrame()

        df = self._completar_textos(df)
        return self._pontuar(df)

    # ---------------------------------------------------------------- coleta

    def _coletar_feeds(self, feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        itens: list[dict[str, Any]] = []
        for feed in feeds:
            try:
                conteudo = fetch(feed["url"])
                if feed.get("formato") == "json_bcb":
                    itens.extend(parse_bcb_json(conteudo, feed))
                else:
                    itens.extend(parse_rss(conteudo, feed))
            except Exception as exc:  # noqa: BLE001 — um feed fora do ar não trava o resto
                logger.warning("feed '%s' falhou: %s", feed.get("nome"), exc)
        return itens

    @staticmethod
    def _aplicar_filtros(df: pd.DataFrame, feeds: list[dict[str, Any]]) -> pd.DataFrame:
        """Alguns feeds são gerais; `filtro_titulo` restringe ao que interessa."""
        filtros = {
            feed["nome"]: feed["filtro_titulo"] for feed in feeds if feed.get("filtro_titulo")
        }
        if not filtros:
            return df

        def manter(linha: pd.Series) -> bool:
            termos = filtros.get(linha["fonte"])
            if not termos:
                return True
            alvo = f"{linha.get('titulo') or ''} {linha.get('texto') or ''}".lower()
            return any(termo.lower() in alvo for termo in termos)

        return df[df.apply(manter, axis=1)]

    def _completar_textos(self, df: pd.DataFrame) -> pd.DataFrame:
        """Baixa o texto integral dos documentos novos, com limite por execução."""
        if self.offline:
            return df

        curtos = df[
            (~df["id"].isin(self._ja_indexados))
            & (df["texto"].fillna("").str.len() < 400)
        ].head(MAX_TEXTOS)

        for indice, linha in curtos.iterrows():
            try:
                pagina = fetch(linha["url"])
                texto = limpar_html(pagina.decode("utf-8", errors="replace"))
                if len(texto) > len(str(linha["texto"] or "")):
                    df.at[indice, "texto"] = texto[:200_000]
            except Exception as exc:  # noqa: BLE001 — sem o texto integral, fica o resumo
                logger.info("sem texto integral para %s: %s", linha["url"], exc)

        return df

    @staticmethod
    def _pontuar(df: pd.DataFrame) -> pd.DataFrame:
        resultados = [
            pontuar(f"{linha.titulo or ''} {linha.texto or ''}", str(linha.idioma or "pt"))
            for linha in df.itertuples()
        ]
        df = df.copy()
        df["score_tom"] = [r.score for r in resultados]
        df["n_hawk"] = [r.n_hawk for r in resultados]
        df["n_dove"] = [r.n_dove for r in resultados]
        return df.sort_values("data_pub", ascending=False).reset_index(drop=True)
