"""Validação de plausibilidade das séries coletadas.

Existe por causa de um erro real, caro e silencioso. Um código errado do SGS
quase nunca dá erro de HTTP: ele devolve **outra série**, com dados
perfeitamente bem formados e significado nenhum. Na primeira coleta contra os
servidores reais, o código anotado como "exportações" trouxe valores negativos
de seis dígitos, e o de "reservas internacionais" trouxe números sete vezes
maiores que as reservas do país. Nada nos logs acusou: o parser funcionou, as
linhas entraram, os gráficos desenharam.

A faixa plausível declarada em `config/sources.yaml` é o que transforma esse
erro silencioso em falha visível. Não é validação estatística — é uma âncora
grosseira de ordem de grandeza, do tipo "o dólar fica entre 0,50 e 20 reais".
Larga o bastante para nunca barrar um dado legítimo, estreita o bastante para
barrar uma série trocada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

# Fração mínima de observações dentro da faixa para a série ser aceita.
# Não é 100% porque uma revisão pontual ou um outlier legítimo não deveriam
# derrubar onze anos de história.
TOLERANCIA = 0.95


@dataclass(frozen=True)
class Veredito:
    aceita: bool
    motivo: str = ""


def validar_faixa(
    valores: pd.Series, faixa: list[float] | tuple[float, float] | None
) -> Veredito:
    """A série cabe na faixa declarada?

    Sem faixa declarada, aceita — a validação é opcional, para que uma série
    nova possa ser acrescentada sem obrigar quem a acrescenta a saber de cor os
    seus limites.
    """
    if not faixa:
        return Veredito(True)

    limpos = pd.to_numeric(valores, errors="coerce").dropna()
    if limpos.empty:
        return Veredito(False, "nenhum valor numérico")

    minimo, maximo = float(faixa[0]), float(faixa[1])
    dentro = limpos.between(minimo, maximo)
    proporcao = float(dentro.mean())

    if proporcao >= TOLERANCIA:
        return Veredito(True)

    return Veredito(
        False,
        f"{(1 - proporcao) * 100:.0f}% dos valores fora da faixa "
        f"[{minimo:g}, {maximo:g}] — observado [{limpos.min():,.2f}, "
        f"{limpos.max():,.2f}]. Provável código trocado.",
    )


def aceitar_serie(parcial: pd.DataFrame, serie: dict, identificador: str) -> bool:
    """Aplica a validação e registra a rejeição de forma acionável.

    A mensagem diz o que se esperava, o que veio e o que provavelmente
    aconteceu — é ela que aparece no log do workflow e orienta a correção.
    """
    veredito = validar_faixa(parcial.get("valor", pd.Series(dtype=float)), serie.get("faixa"))
    if veredito.aceita:
        return True

    logger.warning(
        "série %s (%s) rejeitada: %s",
        identificador,
        serie.get("nome", "sem nome"),
        veredito.motivo,
    )
    return False
