"""Contrato comum das fontes de dados.

Cada fonte separa o acesso à rede (`fetch_*`) da interpretação do conteúdo
(`parse`). Essa divisão é o que torna os parsers testáveis sem conexão: os
testes chamam `parse` diretamente com as amostras de `data/fixtures/`.

`Source.run()` cuida do resto — gravar com upsert idempotente e registrar o
resultado em `ingest_log` — de modo que uma fonte fora do ar nunca derruba as
demais nem o aplicativo.
"""

from __future__ import annotations

import datetime as dt
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd

from tesouraria import db
from tesouraria.http import OfflineError, fetch
from tesouraria.settings import get_settings, source_config

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    fonte: str
    status: str  # ok | vazio | erro | pulado
    linhas: int = 0
    modo: str = "rede"
    erro: str | None = None


class Source(ABC):
    """Uma fonte de dados que sabe se coletar e onde se gravar."""

    name: str = ""
    table: str = ""
    keys: tuple[str, ...] | None = None

    @property
    def config(self) -> dict[str, Any]:
        return source_config(self.name)

    @property
    def offline(self) -> bool:
        return get_settings().offline

    # ------------------------------------------------------------------ IO

    def fixture(self, filename: str) -> bytes:
        """Lê uma amostra versionada de `data/fixtures/`."""
        path = get_settings().fixtures_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"fixture ausente para {self.name}: {path}")
        return path.read_bytes()

    def get(self, url: str, *, fixture: str | None = None, **kwargs: Any) -> bytes:
        """Busca `url` na rede, ou devolve a fixture quando em modo offline.

        A fixture é usada apenas no modo offline explícito. Falha de rede em
        modo normal propaga — mascarar erro com dado antigo daria uma falsa
        sensação de atualidade.
        """
        if self.offline:
            if fixture is None:
                raise OfflineError(f"{self.name}: sem fixture configurada para {url}")
            logger.info("%s: lendo fixture %s", self.name, fixture)
            return self.fixture(fixture)
        return fetch(url, **kwargs)

    # -------------------------------------------------------------- coleta

    @abstractmethod
    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        """Devolve o quadro já no formato da tabela de destino."""

    def prepare(self, con: duckdb.DuckDBPyConnection) -> None:  # noqa: B027 — gancho opcional
        """Gancho opcional para consultar o que já foi gravado antes de coletar.

        Usado pelas fontes de documentos, que evitam rebaixar o servidor
        buscando de novo o texto de discursos já indexados.
        """

    def skip_reason(self) -> str | None:
        """Motivo para não executar (por exemplo, chave de API ausente).

        Devolver um texto faz a fonte ser registrada como `pulado` em vez de
        `erro`, distinguindo uma configuração faltante de uma falha real.
        """
        return None

    # ------------------------------------------------------------- execução

    def run(self, con: duckdb.DuckDBPyConnection, since: dt.date | None = None) -> IngestResult:
        modo = "fixture" if self.offline else "rede"

        motivo = self.skip_reason()
        if motivo:
            result = IngestResult(self.name, "pulado", 0, modo, motivo)
            db.log_ingest(con, result.fonte, result.status, 0, modo, motivo)
            return result

        try:
            self.prepare(con)
            frame = self.collect(since=since)
        except Exception as exc:  # noqa: BLE001 — uma fonte não pode derrubar as outras
            logger.warning("%s falhou: %s", self.name, exc)
            result = IngestResult(self.name, "erro", 0, modo, f"{type(exc).__name__}: {exc}")
        else:
            if frame is None or frame.empty:
                result = IngestResult(self.name, "vazio", 0, modo)
            else:
                linhas = db.upsert(con, self.table, frame, self.keys)
                result = IngestResult(self.name, "ok", linhas, modo)

        db.log_ingest(con, result.fonte, result.status, result.linhas, result.modo, result.erro)
        return result


def to_date(value: Any, dayfirst: bool = True) -> Any:
    """Converte para `datetime.date`, devolvendo NaT quando não der."""
    parsed = pd.to_datetime(value, dayfirst=dayfirst, errors="coerce")
    if isinstance(parsed, pd.Series):
        return parsed.dt.date
    return parsed.date() if pd.notna(parsed) else None


def year_fraction(start: pd.Series, end: pd.Series) -> pd.Series:
    """Prazo em anos corridos entre duas séries de datas."""
    delta = pd.to_datetime(end) - pd.to_datetime(start)
    return delta.dt.days / 365.25


def business_days(start: pd.Series, end: pd.Series) -> pd.Series:
    """Aproximação de dias úteis (252/ano), suficiente para exibição.

    Não substitui o calendário ANBIMA de feriados: para precificação use as
    taxas como vêm da fonte, que já embutem o calendário correto.
    """
    return (year_fraction(start, end) * 252).round().astype("Int64")
