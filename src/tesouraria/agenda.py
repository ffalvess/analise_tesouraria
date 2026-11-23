"""Agenda de eventos: expande as regras de `config/calendario_eventos.yaml`.

Cada evento vira uma ocorrência com data e hora em America/Sao_Paulo. Eventos
declarados em outro fuso (os americanos, em America/New_York) são convertidos
para o horário de Brasília na data da ocorrência — o que já leva em conta o
horário de verão americano, que o Brasil não tem mais.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from tesouraria import calendario
from tesouraria.settings import load_config

FUSO_PADRAO = "America/Sao_Paulo"
COLUNAS = ["data", "hora", "evento", "regiao", "importancia", "a_confirmar", "nota", "datahora"]


def _ocorrencias(evento: dict, inicio: dt.date, fim: dt.date) -> list[dt.date]:
    regra = evento.get("regra")
    if regra == "datas":
        datas = [dt.date.fromisoformat(str(d)) for d in evento.get("datas", [])]
        return [d for d in datas if inicio <= d <= fim]

    dias = pd.date_range(inicio, fim, freq="D")
    if regra == "semanal":
        return [d.date() for d in dias if d.weekday() == int(evento["dia_semana"])]
    if regra == "mensal":
        ordem, alvo = int(evento["ordem"]), int(evento["dia_semana"])
        saida = []
        for d in dias:
            if d.weekday() == alvo and (d.day - 1) // 7 + 1 == ordem:
                saida.append(d.date())
        return saida
    raise ValueError(f"regra desconhecida no evento {evento.get('nome')!r}: {regra!r}")


def expandir(eventos: list[dict], inicio: dt.date, fim: dt.date,
             fuso_destino: str = FUSO_PADRAO) -> pd.DataFrame:
    """Ocorrências entre `inicio` e `fim` (inclusive), no fuso de destino."""
    destino = ZoneInfo(fuso_destino)
    linhas = []
    for evento in eventos:
        origem = ZoneInfo(evento.get("fuso", FUSO_PADRAO))
        hora = dt.time.fromisoformat(str(evento.get("hora", "00:00")))
        for data in _ocorrencias(evento, inicio, fim):
            # Evento brasileiro em feriado nacional não acontece naquela data.
            if evento.get("regiao") == "BR" and not calendario.eh_dia_util(data):
                continue
            nota = ""
            feriado_us = calendario.feriados_eua(data.year).get(data)
            if evento.get("regiao") == "US" and feriado_us and evento.get("regra") != "datas":
                nota = f"feriado nos EUA ({feriado_us}): data provavelmente remarcada"
            local = dt.datetime.combine(data, hora, tzinfo=origem).astimezone(destino)
            linhas.append(
                {
                    "data": local.date(),
                    "hora": local.strftime("%H:%M"),
                    "evento": evento["nome"],
                    "regiao": evento.get("regiao", ""),
                    "importancia": int(evento.get("importancia", 1)),
                    "a_confirmar": bool(evento.get("verificar", False)) or bool(nota),
                    "nota": nota,
                    "datahora": local,
                }
            )
    if not linhas:
        return pd.DataFrame(columns=COLUNAS)
    return pd.DataFrame(linhas, columns=COLUNAS).sort_values(["datahora", "evento"]).reset_index(
        drop=True
    )


def proximos_dias_uteis(hoje: dt.date, n: int = 10) -> tuple[list[dt.date], pd.DataFrame]:
    """Os próximos `n` dias úteis brasileiros e os eventos que caem neles.

    Eventos americanos em dias que não são úteis no Brasil (um feriado só
    daqui) também aparecem, porque o mercado lá fora funciona.
    """
    dias = calendario.proximos_dias_uteis(hoje, n)
    eventos = load_config("calendario_eventos").get("eventos", [])
    agenda = expandir(eventos, hoje, dias[-1])
    return dias, agenda
