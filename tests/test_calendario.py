"""Testes do calendário de dias úteis.

Os prazos conferidos aqui são os do exemplo de swap documentado em
`tests/test_swap.py` — dez intervalos entre maio de 2026 e janeiro de 2027,
com os dias úteis calculados de forma independente pelo autor do exemplo.
Um calendário com um feriado a mais ou a menos erraria pelo menos um deles.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tesouraria.analytics import calendario as cal


def test_pascoa_em_anos_conhecidos():
    assert cal.pascoa(2024) == dt.date(2024, 3, 31)
    assert cal.pascoa(2025) == dt.date(2025, 4, 20)
    assert cal.pascoa(2026) == dt.date(2026, 4, 5)


def test_feriados_moveis_derivam_da_pascoa():
    feriados_2026 = cal.feriados(2026)
    assert dt.date(2026, 2, 16) in feriados_2026  # segunda de Carnaval
    assert dt.date(2026, 2, 17) in feriados_2026  # terça de Carnaval
    assert dt.date(2026, 4, 3) in feriados_2026  # Sexta-Feira Santa
    assert dt.date(2026, 6, 4) in feriados_2026  # Corpus Christi


def test_consciencia_negra_so_vale_de_2024_em_diante():
    """Antes da Lei 14.759/2023 o dia 20/11 não era feriado nacional."""
    assert dt.date(2023, 11, 20) not in cal.feriados(2023)
    assert dt.date(2024, 11, 20) in cal.feriados(2024)
    assert dt.date(2026, 11, 20) in cal.feriados(2026)


def test_quarta_de_cinzas_e_dia_util():
    """Expediente reduzido não é feriado: a curva conta o dia."""
    assert cal.eh_dia_util(dt.date(2026, 2, 18))


def test_proximo_dia_util_resolve_vencimento_em_feriado():
    # O DAP de novembro/2026 vence no dia 15, um domingo; liquida em 16.
    assert cal.proximo_dia_util(dt.date(2026, 11, 15)) == dt.date(2026, 11, 16)
    # 20/11/2026 é sexta e feriado: empurra para a segunda seguinte.
    assert cal.proximo_dia_util(dt.date(2026, 11, 20)) == dt.date(2026, 11, 23)
    # Um dia útil devolve a si mesmo.
    assert cal.proximo_dia_util(dt.date(2026, 9, 18)) == dt.date(2026, 9, 18)


@pytest.mark.parametrize(
    ("inicio", "fim", "esperado"),
    [
        (dt.date(2026, 5, 15), dt.date(2026, 11, 23), 131),  # contrato inteiro
        (dt.date(2026, 9, 18), dt.date(2026, 11, 23), 43),  # prazo restante
        (dt.date(2026, 9, 15), dt.date(2026, 9, 18), 3),  # pro rata do mês
        (dt.date(2026, 9, 15), dt.date(2026, 10, 15), 21),  # janela mensal
        (dt.date(2026, 9, 18), dt.date(2026, 11, 3), 30),  # DI1X26
        (dt.date(2026, 9, 18), dt.date(2026, 12, 1), 49),  # DI1Z26
        (dt.date(2026, 9, 18), dt.date(2026, 11, 16), 39),  # DAPX26
        (dt.date(2026, 9, 18), dt.date(2026, 12, 15), 59),  # DAPZ26
        (dt.date(2026, 5, 15), dt.date(2026, 11, 16), 127),  # DAPX26 na contratação
        (dt.date(2026, 5, 15), dt.date(2027, 1, 15), 168),  # DAPF27 na contratação
    ],
)
def test_dias_uteis_dos_vertices_do_exemplo(inicio, fim, esperado):
    assert cal.dias_uteis(inicio, fim) == esperado


def test_dias_uteis_exclui_a_data_final():
    """Um título que vence no próximo pregão tem 1 dia útil de prazo, não 2."""
    assert cal.dias_uteis(dt.date(2026, 9, 17), dt.date(2026, 9, 18)) == 1
    assert cal.dias_uteis(dt.date(2026, 9, 18), dt.date(2026, 9, 18)) == 0


def test_dias_uteis_pula_o_carnaval_inteiro():
    """De sexta a quarta-feira de cinzas há um único dia útil no meio."""
    assert cal.dias_uteis(dt.date(2026, 2, 13), dt.date(2026, 2, 18)) == 1


def test_dias_uteis_aceita_texto_e_datas_invertidas():
    assert cal.dias_uteis("2026-09-18", "2026-11-23") == 43
    assert cal.dias_uteis(dt.date(2026, 11, 23), dt.date(2026, 9, 18)) == -43
