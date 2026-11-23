"""Calendário de dias úteis brasileiro (feriados nacionais, padrão ANBIMA).

Até aqui o projeto só tinha `sources.base.business_days`, uma aproximação por
anos corridos × 252 que o próprio comentário dizia não servir para precificar.
Este módulo é o calendário de verdade, e a partir dele passam a sair as
contagens de dias úteis do aplicativo: a variação "em 5 e em 21 dias úteis" da
aba de câmbio e, na Fase 2, o `du` do forward por paridade coberta.

Os feriados são os nacionais que a ANBIMA usa para contar dias úteis:

- fixos: 1/1, 21/4, 1/5, 7/9, 12/10, 2/11, 15/11, 25/12 e, desde 2024, 20/11
  (Dia Nacional de Zumbi e da Consciência Negra, Lei 14.759/2023);
- móveis, a partir da Páscoa: segunda e terça de Carnaval (−48 e −47 dias),
  Sexta-feira Santa (−2) e Corpus Christi (+60).

Feriados estaduais e municipais (25/1 em São Paulo, por exemplo) não entram:
a B3 abre neles e a ANBIMA não os desconta.

Convenção de contagem: `dias_uteis(inicio, fim)` conta os dias úteis no
intervalo **(inicio, fim]** — exclui o dia inicial e inclui o final, que é como
o mercado brasileiro conta o `du` entre a data de operação e o vencimento.
"""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Iterable

import numpy as np

ANO_CONSCIENCIA_NEGRA = 2024

FIXOS: dict[tuple[int, int], str] = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independência",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (12, 25): "Natal",
}


def pascoa(ano: int) -> dt.date:
    """Domingo de Páscoa pelo algoritmo de Meeus/Jones/Butcher (calendário gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes, dia = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(ano, mes, dia + 1)


@functools.lru_cache(maxsize=256)
def feriados(ano: int) -> dict[dt.date, str]:
    """Feriados nacionais do ano, com o nome de cada um."""
    datas = {dt.date(ano, mes, dia): nome for (mes, dia), nome in FIXOS.items()}
    if ano >= ANO_CONSCIENCIA_NEGRA:
        datas[dt.date(ano, 11, 20)] = "Consciência Negra"

    p = pascoa(ano)
    datas[p - dt.timedelta(days=48)] = "Carnaval (segunda)"
    datas[p - dt.timedelta(days=47)] = "Carnaval (terça)"
    datas[p - dt.timedelta(days=2)] = "Sexta-feira Santa"
    datas[p + dt.timedelta(days=60)] = "Corpus Christi"
    return dict(sorted(datas.items()))


@functools.lru_cache(maxsize=64)
def _feriados_entre(ano_ini: int, ano_fim: int) -> tuple[np.datetime64, ...]:
    return tuple(
        np.datetime64(d) for ano in range(ano_ini, ano_fim + 1) for d in feriados(ano)
    )


def _holidays(*datas: dt.date) -> list[np.datetime64]:
    anos = [d.year for d in datas]
    # Uma folga de um ano para cada lado cobre deslocamentos que cruzam a virada.
    return list(_feriados_entre(min(anos) - 1, max(anos) + 1))


def _como_data(valor) -> dt.date:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    return np.datetime64(valor, "D").astype(dt.date)


def eh_dia_util(data: dt.date) -> bool:
    data = _como_data(data)
    return bool(np.is_busday(np.datetime64(data), holidays=_holidays(data)))


def dias_uteis(inicio: dt.date, fim: dt.date) -> int:
    """Dias úteis em (inicio, fim]. Negativo se `fim` vier antes de `inicio`."""
    inicio, fim = _como_data(inicio), _como_data(fim)
    if fim < inicio:
        return -dias_uteis(fim, inicio)
    um_dia = np.timedelta64(1, "D")
    return int(
        np.busday_count(
            np.datetime64(inicio) + um_dia,
            np.datetime64(fim) + um_dia,
            holidays=_holidays(inicio, fim),
        )
    )


def somar_du(data: dt.date, n: int) -> dt.date:
    """Data `n` dias úteis depois (ou antes, se negativo) de `data`.

    Se `data` não for dia útil, a contagem parte do dia útil anterior quando se
    anda para trás e do seguinte quando se anda para frente — a convenção que
    faz `somar_du(sabado, 1)` cair na segunda.
    """
    data = _como_data(data)
    folga = dt.timedelta(days=max(400, abs(n) * 2))
    feriados_ = _holidays(data - folga, data + folga)
    inicio = np.datetime64(data)
    if not np.is_busday(inicio, holidays=feriados_) and n != 0:
        # Rolar já conta como o primeiro passo: sábado + 1 du = segunda.
        inicio = np.busday_offset(inicio, 0, roll="forward" if n > 0 else "backward",
                                  holidays=feriados_)
        n = n - 1 if n > 0 else n + 1
    resultado = np.busday_offset(inicio, n, roll="backward", holidays=feriados_)
    return resultado.astype(dt.date)


def ultimo_dia_util(ate: dt.date) -> dt.date:
    """O próprio `ate`, se for dia útil; senão, o dia útil anterior."""
    return somar_du(ate, 0)


def proximos_dias_uteis(desde: dt.date, n: int, incluir_hoje: bool = True) -> list[dt.date]:
    """Os próximos `n` dias úteis a partir de `desde`."""
    desde = _como_data(desde)
    primeiro = desde if incluir_hoje and eh_dia_util(desde) else somar_du(desde, 1)
    return [primeiro] + [somar_du(primeiro, i) for i in range(1, n)]


def feriados_entre(inicio: dt.date, fim: dt.date) -> dict[dt.date, str]:
    """Feriados nacionais no intervalo fechado [inicio, fim]."""
    saida: dict[dt.date, str] = {}
    for ano in range(inicio.year, fim.year + 1):
        saida.update({d: nome for d, nome in feriados(ano).items() if inicio <= d <= fim})
    return saida


def serie_dias_uteis(datas: Iterable[dt.date]) -> list[bool]:
    """Máscara de dia útil para uma coleção de datas."""
    datas = [_como_data(d) for d in datas]
    if not datas:
        return []
    arr = np.array([np.datetime64(d) for d in datas])
    return list(np.is_busday(arr, holidays=_holidays(min(datas), max(datas))))


# ------------------------------------------------------------- EUA
#
# Só para a agenda: um evento americano marcado por regra (o auxílio-desemprego
# das quintas, o CFTC das sextas) que cai num feriado federal é remarcado por
# quem publica. A agenda não adivinha a nova data, mas marca a ocorrência.


def _n_esimo(ano: int, mes: int, dia_semana: int, n: int) -> dt.date:
    """n-ésimo dia da semana do mês (n = -1 para o último)."""
    if n > 0:
        primeiro = dt.date(ano, mes, 1)
        deslocamento = (dia_semana - primeiro.weekday()) % 7
        return primeiro + dt.timedelta(days=deslocamento + 7 * (n - 1))
    proximo_mes = dt.date(ano + (mes == 12), mes % 12 + 1, 1)
    ultimo = proximo_mes - dt.timedelta(days=1)
    return ultimo - dt.timedelta(days=(ultimo.weekday() - dia_semana) % 7)


def _observado(data: dt.date) -> dt.date:
    """Regra federal: sábado → sexta anterior; domingo → segunda seguinte."""
    if data.weekday() == 5:
        return data - dt.timedelta(days=1)
    if data.weekday() == 6:
        return data + dt.timedelta(days=1)
    return data


@functools.lru_cache(maxsize=64)
def feriados_eua(ano: int) -> dict[dt.date, str]:
    """Feriados federais americanos (data observada)."""
    datas = {
        _observado(dt.date(ano, 1, 1)): "New Year's Day",
        _n_esimo(ano, 1, 0, 3): "Martin Luther King Jr. Day",
        _n_esimo(ano, 2, 0, 3): "Presidents' Day",
        _n_esimo(ano, 5, 0, -1): "Memorial Day",
        _observado(dt.date(ano, 6, 19)): "Juneteenth",
        _observado(dt.date(ano, 7, 4)): "Independence Day",
        _n_esimo(ano, 9, 0, 1): "Labor Day",
        _n_esimo(ano, 10, 0, 2): "Columbus Day",
        _observado(dt.date(ano, 11, 11)): "Veterans Day",
        _n_esimo(ano, 11, 3, 4): "Thanksgiving",
        _observado(dt.date(ano, 12, 25)): "Christmas Day",
    }
    return dict(sorted(datas.items()))
