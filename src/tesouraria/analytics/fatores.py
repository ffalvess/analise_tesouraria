"""Fatores acumulados e interpolação *flat-forward* em dias úteis.

O resto do pacote trabalha com **taxas** em prazos medidos em anos corridos, e
interpola com spline — é o que serve para desenhar a curva e comparar países.
Para precificar um contrato, a régua é outra: o mercado brasileiro acumula em
dias úteis, e o objeto que se interpola é o **fator**, não a taxa.

    Q(du) = (1 + i)^(du/252)

A interpolação *flat-forward* entre dois vértices supõe taxa a termo constante
no intervalo, o que equivale a interpolar linearmente o logaritmo do fator:

    α = (du − du₁) / (du₂ − du₁),    Q(du) = Q₁^(1−α) · Q₂^α

A diferença para uma spline sobre a taxa não é cosmética: flat-forward é a
convenção com que a B3 e as mesas marcam DI1 e DAP, então é ela que reproduz o
preço que a contraparte vai apresentar.

Aqui também não se extrapola. Pedir um prazo fora dos vértices negociados
levanta erro em vez de devolver um número — a mesma decisão que `analytics/
curve.py` toma ao deixar um vértice vazio em vez de estimá-lo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DIAS_UTEIS_ANO = 252


@dataclass(frozen=True)
class Vertice:
    """Um ponto negociado da curva: prazo em dias úteis e taxa efetiva anual em %."""

    du: int
    taxa: float
    rotulo: str = ""

    @property
    def fator(self) -> float:
        return fator(self.taxa, self.du)


def fator(taxa: float, du: int | float) -> float:
    """Fator acumulado de uma taxa efetiva anual (em %) por `du` dias úteis."""
    if du < 0:
        raise ValueError(f"prazo em dias úteis não pode ser negativo: {du}")
    return (1 + taxa / 100.0) ** (du / DIAS_UTEIS_ANO)


def taxa_de_fator(fator_acumulado: float, du: int | float) -> float:
    """Caminho inverso de `fator`: a taxa efetiva anual, em %, embutida no fator."""
    if du <= 0:
        raise ValueError(f"prazo em dias úteis precisa ser positivo: {du}")
    if fator_acumulado <= 0:
        raise ValueError(f"fator acumulado precisa ser positivo: {fator_acumulado}")
    return (fator_acumulado ** (DIAS_UTEIS_ANO / du) - 1) * 100.0


def flat_forward(fator_1: float, du_1: int, fator_2: float, du_2: int, du: int | float) -> float:
    """Fator interpolado entre dois vértices, com taxa a termo constante entre eles."""
    if du_2 == du_1:
        raise ValueError("os dois vértices têm o mesmo prazo; não há intervalo para interpolar")
    alfa = (du - du_1) / (du_2 - du_1)
    return fator_1 ** (1 - alfa) * fator_2**alfa


def interpolar(vertices: Sequence[Vertice], du: int | float) -> float:
    """Fator acumulado da curva no prazo `du`, por flat-forward entre os vizinhos.

    Um `du` que coincide com um vértice devolve o fator dele, sem interpolar.
    Um `du` fora do intervalo negociado levanta `ValueError`: extrapolar a ponta
    de uma curva é inventar preço, e um número inventado não se distingue de um
    observado depois que entra na conta.
    """
    if not vertices:
        raise ValueError("curva sem vértices")

    ordenados = sorted(vertices, key=lambda v: v.du)
    if du < ordenados[0].du or du > ordenados[-1].du:
        raise ValueError(
            f"prazo de {du} d.u. fora dos vértices negociados "
            f"({ordenados[0].du} a {ordenados[-1].du} d.u.); não extrapolamos"
        )

    for vertice in ordenados:
        if vertice.du == du:
            return vertice.fator

    anterior = max((v for v in ordenados if v.du < du), key=lambda v: v.du)
    seguinte = min((v for v in ordenados if v.du > du), key=lambda v: v.du)
    return flat_forward(anterior.fator, anterior.du, seguinte.fator, seguinte.du, du)


def taxa_interpolada(vertices: Sequence[Vertice], du: int | float) -> float:
    """A taxa efetiva anual, em %, correspondente ao fator interpolado em `du`."""
    return taxa_de_fator(interpolar(vertices, du), du)
