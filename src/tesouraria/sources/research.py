"""Relatórios de risco e análise sobre o Brasil.

Duas origens, por uma razão prática e uma legal:

* **Feeds públicos** (`config/feeds.yaml`, seção `research`) — FMI, BIS, Banco
  Mundial, agências de rating, IPEA, FGV. Conteúdo aberto, coletado como
  qualquer outro feed.
* **PDFs locais** (`data/research_pdfs/`) — a pasta onde você deposita os
  relatórios que já recebe por direito. Pesquisa sell-side de Itaú, BTG, XP,
  Goldman e afins é licenciada e não é raspada por este aplicativo; passando
  pelo disco, o material é seu e o app apenas lê o que você colocou lá.

Ambas as origens recebem o mesmo score de tom, então um relatório de banco e
uma ata do Copom aparecem na mesma escala.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from tesouraria.analytics.tone import pontuar
from tesouraria.settings import get_settings
from tesouraria.sources.speeches import SpeechesSource

logger = logging.getLogger(__name__)

MAX_PAGINAS = 60


def _hash_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as handle:
        for bloco in iter(lambda: handle.read(65536), b""):
            digest.update(bloco)
    return digest.hexdigest()[:32]


def extrair_pdf(caminho: Path, max_paginas: int = MAX_PAGINAS) -> str:
    """Texto das primeiras páginas de um PDF.

    O limite existe porque relatórios longos costumam trazer a tese nas
    primeiras páginas e dezenas de páginas de tabelas e disclaimers depois —
    que só diluiriam o score de tom.
    """
    leitor = PdfReader(str(caminho))
    partes = []
    for pagina in leitor.pages[:max_paginas]:
        try:
            partes.append(pagina.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 — página corrompida não invalida o arquivo
            logger.debug("página ilegível em %s: %s", caminho.name, exc)
    return "\n".join(partes).strip()


def _idioma_provavel(texto: str) -> str:
    """Heurística barata: palavras funcionais decidem entre português e inglês."""
    amostra = texto[:4000].lower()
    pt = sum(amostra.count(p) for p in (" de ", " que ", " para ", " não ", " com "))
    en = sum(amostra.count(p) for p in (" the ", " of ", " and ", " that ", " with "))
    return "pt" if pt >= en else "en"


class ResearchSource(SpeechesSource):
    """Feeds públicos de análise, mais os PDFs depositados localmente."""

    name = "research"
    table = "documentos"
    secao = "research"
    fixture_name = "feeds_research.json"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        dos_feeds = super().collect(since=since)
        dos_pdfs = self.coletar_pdfs()

        quadros = [q for q in (dos_feeds, dos_pdfs) if q is not None and not q.empty]
        if not quadros:
            return pd.DataFrame()
        return pd.concat(quadros, ignore_index=True).drop_duplicates(subset=["id"])

    def coletar_pdfs(self) -> pd.DataFrame:
        pasta = get_settings().research_pdfs_dir
        if not pasta.exists():
            return pd.DataFrame()

        registros = []
        for caminho in sorted(pasta.rglob("*.pdf")):
            try:
                texto = extrair_pdf(caminho)
            except Exception as exc:  # noqa: BLE001
                logger.warning("não foi possível ler %s: %s", caminho.name, exc)
                continue

            if not texto:
                logger.info("%s não tem texto extraível (provável PDF digitalizado)", caminho.name)
                continue

            idioma = _idioma_provavel(texto)
            tom = pontuar(texto, idioma)
            # Subpasta como instituição: data/research_pdfs/BTG/nota.pdf -> "BTG".
            relativo = caminho.relative_to(pasta)
            instituicao = relativo.parts[0] if len(relativo.parts) > 1 else "PDF local"

            registros.append(
                {
                    "id": _hash_arquivo(caminho),
                    "fonte": "pdf_local",
                    "instituicao": instituicao,
                    "autor": None,
                    "titulo": caminho.stem,
                    "data_pub": dt.date.fromtimestamp(caminho.stat().st_mtime),
                    "url": str(caminho),
                    "tipo": "pdf_local",
                    "idioma": idioma,
                    "texto": texto[:200_000],
                    "score_tom": tom.score,
                    "n_hawk": tom.n_hawk,
                    "n_dove": tom.n_dove,
                }
            )

        return pd.DataFrame(registros)
