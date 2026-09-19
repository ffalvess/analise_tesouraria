"""Calendário de dias úteis brasileiro (feriados nacionais ANBIMA).

`sources/base.py::business_days` aproxima o prazo por `dias_corridos/365,25 × 252`
e arredonda. Isso basta para exibir "cerca de 5 anos" numa tabela, mas não para
precificar: um contrato de DI ou de swap acumula exatamente `(1+i)^(du/252)`, e
errar um único dia útil em 43 desloca o fator na quarta casa decimal — dinheiro
de verdade num notional de milhões.

Este módulo existe para essa segunda necessidade. Os feriados nacionais são
derivados, não tabelados: os fixos vêm do calendário civil e os móveis saem da
data da Páscoa, o que faz o calendário valer para qualquer ano sem depender de
um arquivo que alguém precisaria atualizar todo dezembro.

Cobre apenas feriados **nacionais**, que são os que a ANBIMA usa para a curva.
Feriados municipais e estaduais (aniversário de São Paulo, por exemplo) não
suspendem o pregão da B3 para efeito de contagem de dias úteis da curva e
ficam de fora de propósito.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd

# A Consciência Negra virou feriado nacional pela Lei 14.759/2023, com efeito a
# partir de 2024. Antes disso era feriado apenas em parte dos municípios, e não
# entrava no calendário ANBIMA — contar 20/11/2022 como não útil erraria o
# prazo de qualquer contrato daquele ano.
ANO_CONSCIENCIA_NEGRA = 2024


def pascoa(ano: int) -> dt.date:
    """Domingo de Páscoa pelo algoritmo de Gauss/Butcher (calendário gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    mes, dia = divmod(h + ell - 7 * m + 114, 31)
    return dt.date(ano, mes, dia + 1)


@lru_cache(maxsize=256)
def feriados(ano: int) -> frozenset[dt.date]:
    """Feriados nacionais do ano, incluindo os móveis ligados à Páscoa."""
    domingo = pascoa(ano)
    datas = {
        dt.date(ano, 1, 1),  # Confraternização Universal
        dt.date(ano, 4, 21),  # Tiradentes
        dt.date(ano, 5, 1),  # Dia do Trabalho
        dt.date(ano, 9, 7),  # Independência
        dt.date(ano, 10, 12),  # Nossa Senhora Aparecida
        dt.date(ano, 11, 2),  # Finados
        dt.date(ano, 11, 15),  # Proclamação da República
        dt.date(ano, 12, 25),  # Natal
        domingo - dt.timedelta(days=48),  # segunda de Carnaval
        domingo - dt.timedelta(days=47),  # terça de Carnaval
        domingo - dt.timedelta(days=2),  # Sexta-Feira Santa
        domingo + dt.timedelta(days=60),  # Corpus Christi
    }
    if ano >= ANO_CONSCIENCIA_NEGRA:
        datas.add(dt.date(ano, 11, 20))  # Consciência Negra
    return frozenset(datas)


@lru_cache(maxsize=64)
def _feriados_np(ano_inicial: int, ano_final: int) -> np.ndarray:
    """Feriados do intervalo no formato que o numpy espera para dias úteis."""
    todos: set[dt.date] = set()
    for ano in range(ano_inicial, ano_final + 1):
        todos |= feriados(ano)
    return np.array(sorted(todos), dtype="datetime64[D]")


def _como_data(valor: dt.date | dt.datetime | str | pd.Timestamp) -> dt.date:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    convertido = pd.to_datetime(valor)
    return convertido.date()


def eh_dia_util(data: dt.date | str) -> bool:
    """Dia de pregão: nem fim de semana, nem feriado nacional."""
    alvo = _como_data(data)
    return alvo.weekday() < 5 and alvo not in feriados(alvo.year)


def proximo_dia_util(data: dt.date | str) -> dt.date:
    """O próprio dia, se for útil; senão o primeiro dia útil seguinte.

    É a regra que resolve o vencimento de um contrato que cai em feriado — o
    DAP de novembro de 2026 vence no dia 15, que é domingo, e liquida em 16.
    """
    alvo = _como_data(data)
    while not eh_dia_util(alvo):
        alvo += dt.timedelta(days=1)
    return alvo


def dias_uteis(inicio: dt.date | str, fim: dt.date | str) -> int:
    """Dias úteis no intervalo `[inicio, fim)` — início inclusive, fim exclusive.

    É a contagem do mercado: um título que vence amanhã tem 1 dia útil de
    prazo, e não 2. Devolve valor negativo quando `fim` é anterior a `inicio`.
    """
    comeco, termino = _como_data(inicio), _como_data(fim)
    anos = sorted((comeco.year, termino.year))
    feriados_np = _feriados_np(anos[0] - 1, anos[1] + 1)
    return int(
        np.busday_count(
            np.datetime64(comeco, "D"), np.datetime64(termino, "D"), holidays=feriados_np
        )
    )
