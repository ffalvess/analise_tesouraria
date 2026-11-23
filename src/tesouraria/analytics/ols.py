"""Mínimos quadrados ordinários em numpy.

Implementação própria, sem `statsmodels`, de propósito: a atribuição do câmbio
roda centenas de regressões pequenas (uma por sessão, numa janela móvel) e só
precisa de coeficientes, resíduos e R². Uma função de vinte linhas que se lê
inteira é mais fácil de auditar do que a API de uma biblioteca de econometria.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Ajuste:
    """Resultado de uma regressão: `y = constante + X·betas + resíduo`."""

    constante: float
    betas: np.ndarray
    residuos: np.ndarray
    r2: float
    desvio_residuo: float  # desvio-padrão do resíduo, com graus de liberdade corrigidos
    n: int

    def prever(self, x: np.ndarray) -> np.ndarray:
        x = np.atleast_2d(np.asarray(x, dtype=float))
        return self.constante + x @ self.betas


def ols(y: np.ndarray, x: np.ndarray, constante: bool = True) -> Ajuste:
    """Regressão de `y` em `x` (n × k) por mínimos quadrados.

    Usa `lstsq` (decomposição SVD) em vez da equação normal `(X'X)⁻¹X'y`, que
    perde precisão quando os regressores são quase colineares — caso comum entre
    DXY e cesta de emergentes antes da ortogonalização.
    """
    y = np.asarray(y, dtype=float).ravel()
    x = np.asarray(x, dtype=float)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    n, k = x.shape
    if n != len(y):
        raise ValueError(f"y tem {len(y)} observações e x tem {n}")

    matriz = np.column_stack([np.ones(n), x]) if constante else x
    parametros = matriz.shape[1]
    if n <= parametros:
        raise ValueError(f"observações insuficientes: {n} para {parametros} parâmetros")

    coef, *_ = np.linalg.lstsq(matriz, y, rcond=None)
    ajustado = matriz @ coef
    residuos = y - ajustado

    soma_res = float(residuos @ residuos)
    centrado = y - y.mean() if constante else y
    soma_tot = float(centrado @ centrado)
    r2 = 1.0 - soma_res / soma_tot if soma_tot > 0 else float("nan")

    return Ajuste(
        constante=float(coef[0]) if constante else 0.0,
        betas=np.asarray(coef[1:] if constante else coef, dtype=float),
        residuos=residuos,
        r2=r2,
        desvio_residuo=float(np.sqrt(soma_res / (n - parametros))),
        n=n,
    )


def residualizar(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, Ajuste]:
    """Parte de `y` que `x` (com constante) não explica. Devolve resíduo e ajuste."""
    ajuste = ols(y, x, constante=True)
    return ajuste.residuos, ajuste
