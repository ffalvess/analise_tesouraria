"""Cliente HTTP compartilhado pelas fontes.

Três responsabilidades: aplicar timeout e retry com backoff exponencial,
guardar as respostas em disco com TTL (evita rebaixar servidores públicos
durante o desenvolvimento) e falhar de forma explícita quando a rede está
indisponível.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from tesouraria.settings import get_settings

logger = logging.getLogger(__name__)


class OfflineError(RuntimeError):
    """Levantada quando o modo offline está ativo e não há fixture disponível."""


def _cache_key(method: str, url: str, params: dict[str, Any] | None, data: Any) -> str:
    payload = json.dumps(
        {"m": method, "u": url, "p": params, "d": data}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _cache_path(key: str) -> Path:
    settings = get_settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings.cache_dir / f"{key}.bin"


def _read_cache(path: Path, ttl_hours: float) -> bytes | None:
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > ttl_hours:
        return None
    return path.read_bytes()


class RespostaDeErro(RuntimeError):
    """Erro HTTP que carrega o que o servidor respondeu.

    `raise_for_status()` do httpx descarta o corpo, e é justamente ali que as
    APIs explicam o problema: o Olinda diz qual cláusula do filtro OData está
    malformada, o Comex Stat diz quanto esperar antes de tentar de novo. Sem
    esse texto, corrigir um endpoint vira adivinhação.
    """


class ErroTemporario(RespostaDeErro):
    """Erro que vale a pena repetir: 429 e 5xx."""


def _classificar(exc: httpx.HTTPStatusError) -> RespostaDeErro:
    resposta = exc.response
    corpo = resposta.text.strip().replace("\n", " ")[:500]
    mensagem = f"HTTP {resposta.status_code} em {resposta.request.url}"
    if corpo:
        mensagem += f" — resposta: {corpo}"

    # 429 e 5xx podem passar; 4xx no geral, não: um 400 malformado ou um 404
    # inexistente continuarão iguais na terceira tentativa.
    if resposta.status_code == 429 or resposta.status_code >= 500:
        return ErroTemporario(mensagem)
    return RespostaDeErro(mensagem)


def _espera(tentativa) -> float:
    """Backoff exponencial, mas respeitando `Retry-After` quando o servidor manda."""
    exc = tentativa.outcome.exception() if tentativa.outcome else None
    if isinstance(exc, ErroTemporario):
        achado = re.search(r"[Rr]etry-[Aa]fter[\"']?\s*[:=]\s*[\"']?(\d+)", str(exc))
        if achado:
            return min(float(achado.group(1)), 120.0)
    return wait_exponential(multiplier=2, min=5, max=120)(tentativa)


@retry(
    # Só erro de transporte e erro temporário. Repetir um 400 três vezes só
    # gastava o tempo do workflow — foram minutos na primeira coleta real.
    retry=retry_if_exception_type((httpx.TransportError, ErroTemporario)),
    stop=stop_after_attempt(4),
    wait=_espera,
    reraise=True,
)
def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None,
    data: Any,
    headers: dict[str, str],
    timeout: float,
) -> bytes:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.request(method, url, params=params, data=data, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _classificar(exc) from exc
        return response.content


def fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: Any = None,
    headers: dict[str, str] | None = None,
    use_cache: bool = True,
    ttl_hours: float | None = None,
    timeout: float | None = None,
    tentativas: int | None = None,
) -> bytes:
    """Busca uma URL e devolve o corpo em bytes.

    Em modo offline levanta `OfflineError` — cabe à fonte capturar e recorrer
    à sua fixture.

    `timeout` e `tentativas` existem para quem **varre** em vez de coletar. A
    política padrão — 30s, quatro tentativas — é a certa para uma série que
    precisa entrar no banco, e desastrosa para uma sondagem: o SGS pendura a
    conexão em código inexistente, e cada código passa a custar dois minutos e
    meio. A varredura de 201 códigos levaria quase oito horas.
    """
    settings = get_settings()
    if settings.offline:
        raise OfflineError(f"modo offline ativo; requisição a {url} não executada")

    request_headers = {"User-Agent": settings.user_agent}
    if headers:
        request_headers.update(headers)

    ttl = settings.http_cache_ttl_hours if ttl_hours is None else ttl_hours
    key = _cache_key(method, url, params, data)
    path = _cache_path(key)

    if use_cache:
        cached = _read_cache(path, ttl)
        if cached is not None:
            logger.debug("cache hit para %s", url)
            return cached

    logger.info("GET %s", url)
    executor = (
        _request
        if tentativas is None
        else _request.retry_with(stop=stop_after_attempt(tentativas))
    )
    content = executor(
        method,
        url,
        params=params,
        data=data,
        headers=request_headers,
        timeout=settings.http_timeout if timeout is None else timeout,
    )

    if use_cache:
        path.write_bytes(content)

    return content


def fetch_text(url: str, *, encoding: str = "utf-8", **kwargs: Any) -> str:
    return fetch(url, **kwargs).decode(encoding, errors="replace")


def fetch_json(url: str, **kwargs: Any) -> Any:
    return json.loads(fetch(url, **kwargs).decode("utf-8", errors="replace"))
