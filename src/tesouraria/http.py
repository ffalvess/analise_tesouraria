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


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=16),
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
        response.raise_for_status()
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
) -> bytes:
    """Busca uma URL e devolve o corpo em bytes.

    Em modo offline levanta `OfflineError` — cabe à fonte capturar e recorrer
    à sua fixture.
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
    content = _request(
        method,
        url,
        params=params,
        data=data,
        headers=request_headers,
        timeout=settings.http_timeout,
    )

    if use_cache:
        path.write_bytes(content)

    return content


def fetch_text(url: str, *, encoding: str = "utf-8", **kwargs: Any) -> str:
    return fetch(url, **kwargs).decode(encoding, errors="replace")


def fetch_json(url: str, **kwargs: Any) -> Any:
    return json.loads(fetch(url, **kwargs).decode("utf-8", errors="replace"))
