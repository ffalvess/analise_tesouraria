"""Política de rede: quando insistir e quando desistir.

O ponto delicado não é o caminho feliz, e sim o custo do fracasso. A política
que serve a uma coleta — 30 segundos, quatro tentativas — inviabiliza uma
varredura: o SGS pendura a conexão em código inexistente, e cada código passa a
custar dois minutos e meio. Uma faixa de 201 códigos levaria quase oito horas, e
foi o que aconteceu na primeira sondagem: 25 códigos em 56 minutos.
"""

from __future__ import annotations

import httpx
import pytest

from tesouraria import http


@pytest.fixture
def rede(monkeypatch, tmp_path):
    """Registra cada tentativa e deixa o teste decidir o que a rede responde."""
    from tesouraria.settings import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        http,
        "get_settings",
        lambda: Settings(offline=False, data_dir=tmp_path),
    )

    chamadas: list[dict] = []

    class ClienteFalso:
        def __init__(self, *, timeout, follow_redirects):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def request(self, method, url, **kwargs):
            chamadas.append({"timeout": self.timeout, "url": url})
            raise httpx.ReadTimeout("pendurou", request=httpx.Request(method, url))

    monkeypatch.setattr(http.httpx, "Client", ClienteFalso)
    return chamadas


def test_coleta_insiste_quatro_vezes(rede, monkeypatch):
    """O padrão continua sendo o da coleta: uma série no banco merece insistência."""
    monkeypatch.setattr(http, "_espera", lambda tentativa: 0)

    with pytest.raises(httpx.TransportError):
        http.fetch("https://exemplo/serie", use_cache=False)

    assert len(rede) == 4
    assert rede[0]["timeout"] == 30.0


def test_varredura_desiste_na_primeira(rede):
    """Numa sondagem, silêncio é resposta: o código não existe, siga em frente."""
    with pytest.raises(httpx.TransportError):
        http.fetch("https://exemplo/serie", use_cache=False, timeout=8.0, tentativas=1)

    assert len(rede) == 1, "insistir aqui é o que fez 201 códigos virarem oito horas"
    assert rede[0]["timeout"] == 8.0


def test_o_limite_da_varredura_nao_vaza_para_a_coleta(rede, monkeypatch):
    """Uma chamada com limites não pode reconfigurar a política das seguintes.

    `_request` é decorado no módulo; se o override mutasse o decorador em vez de
    derivar um novo, a primeira sondagem deixaria toda a coleta seguinte sem
    retry — e isso não apareceria em teste nenhum até uma fonte falhar em
    produção por um soluço de rede.
    """
    monkeypatch.setattr(http, "_espera", lambda tentativa: 0)

    with pytest.raises(httpx.TransportError):
        http.fetch("https://exemplo/sonda", use_cache=False, timeout=8.0, tentativas=1)
    rede.clear()

    with pytest.raises(httpx.TransportError):
        http.fetch("https://exemplo/coleta", use_cache=False)

    assert len(rede) == 4
    assert rede[0]["timeout"] == 30.0
