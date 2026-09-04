"""Escolha da cesta que serve de régua ao prêmio do real.

Esta é a lógica que decidiu, na produção, se a página de fluxo cambial mostrava
uma análise ou dois avisos. Enquanto `cestas_disponiveis` só conhecia as cestas
que excluem o real, e nenhuma delas estava no banco, a tela inteira virava um
pedido de coleta — e como as amostras sintéticas trazem todas as séries, nenhum
teste jamais executou esse caminho.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tesouraria.ui import premio


def serie(serie_id: str, n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "serie_id": serie_id,
            "nome": serie_id,
            "data_ref": [dt.date(2026, 1, 1) + dt.timedelta(days=k) for k in range(n)],
            "valor": [100.0 + k for k in range(n)],
        }
    )


@pytest.fixture
def banco_de_series(monkeypatch):
    """Substitui a consulta por um banco declarado no próprio teste."""

    def montar(*presentes: str, n: int = 300) -> None:
        """`n` acima da janela do beta: aqui o assunto é *qual* cesta, não se há
        histórico — isso tem os seus próprios testes logo abaixo."""
        monkeypatch.setattr(
            premio.common,
            "cache_serie",
            lambda sid, desde=None: serie(sid, n) if sid in presentes else pd.DataFrame(),
        )

    return montar


def test_prefere_as_cestas_que_excluem_o_real(banco_de_series):
    banco_de_series("DTWEXAFEGS", "DTWEXEMEGS", "DTWEXBGS")
    cestas, reserva = premio.cestas_disponiveis()

    assert set(cestas) == {"DTWEXAFEGS", "DTWEXEMEGS"}
    assert not reserva, "com as réguas certas no banco, a ampla não deve entrar"


def test_uma_cesta_preferida_basta_para_dispensar_a_reserva(banco_de_series):
    banco_de_series("DTWEXEMEGS", "DTWEXBGS")
    cestas, reserva = premio.cestas_disponiveis()

    assert set(cestas) == {"DTWEXEMEGS"}
    assert not reserva


def test_cai_para_a_cesta_ampla_e_sinaliza(banco_de_series):
    """O caso da produção: só a `DTWEXBGS` foi coletada."""
    banco_de_series("DTWEXBGS")
    cestas, reserva = premio.cestas_disponiveis()

    assert set(cestas) == {"DTWEXBGS"}
    assert reserva, "a flag é o que faz a ressalva aparecer na tela"


def test_sem_nenhuma_cesta_nao_ha_reserva(banco_de_series):
    banco_de_series()
    cestas, reserva = premio.cestas_disponiveis()

    assert cestas == {}
    assert not reserva


def test_premio_do_dia_usa_a_reserva(banco_de_series):
    """Sem a reserva este número seria `nan` e o painel mostraria um traço."""
    import numpy as np

    banco_de_series("DTWEXBGS")
    cambio = serie("1", n=40)

    assert np.isfinite(premio.premio_do_dia(cambio))


def test_premio_do_dia_sem_cesta_e_nan(banco_de_series):
    import numpy as np

    banco_de_series()
    assert np.isnan(premio.premio_do_dia(serie("1", n=40)))


def test_cesta_preferida_curta_nao_desbanca_a_reserva_longa(monkeypatch):
    """A armadilha da coleta parcial.

    Uma coleta de sete dias deixaria a `DTWEXAFEGS` com um punhado de
    observações. Sem esta guarda ela viraria régua na hora, e a tela trocaria um
    prêmio com onze anos de histórico e beta móvel por uma leitura de um dia —
    sem erro nenhum, sem aviso nenhum, parecendo igualmente certa.
    """
    tamanhos = {"DTWEXAFEGS": 7, "DTWEXBGS": 3000}
    monkeypatch.setattr(
        premio.common,
        "cache_serie",
        lambda sid, desde=None: serie(sid, tamanhos[sid]) if sid in tamanhos else pd.DataFrame(),
    )

    cestas, reserva = premio.cestas_disponiveis()
    assert set(cestas) == {"DTWEXBGS"}
    assert reserva


def test_cesta_preferida_com_historico_assume(monkeypatch):
    from tesouraria.analytics import currency

    tamanhos = {"DTWEXAFEGS": currency.JANELA_BETA, "DTWEXBGS": 3000}
    monkeypatch.setattr(
        premio.common,
        "cache_serie",
        lambda sid, desde=None: serie(sid, tamanhos[sid]) if sid in tamanhos else pd.DataFrame(),
    )

    cestas, reserva = premio.cestas_disponiveis()
    assert set(cestas) == {"DTWEXAFEGS"}
    assert not reserva


def test_so_uma_cesta_curta_ainda_e_melhor_que_nada(monkeypatch):
    """Sem nenhuma série longa, meia leitura vale mais que a tela vazia."""
    monkeypatch.setattr(
        premio.common,
        "cache_serie",
        lambda sid, desde=None: serie(sid, 7) if sid == "DTWEXAFEGS" else pd.DataFrame(),
    )

    cestas, reserva = premio.cestas_disponiveis()
    assert set(cestas) == {"DTWEXAFEGS"}
    assert not reserva, "a preferida curta não é a cesta ampla; a ressalva não se aplica"


def test_a_ressalva_diz_por_que_o_numero_e_amortecido():
    """A ressalva precisa explicar o viés, não só existir."""
    assert "inclui o próprio real" in premio.RESSALVA_RESERVA
    assert "amortecido" in premio.RESSALVA_RESERVA
    assert "DTWEXBGS" in premio.RESSALVA_RESERVA
