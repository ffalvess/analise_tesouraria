"""Construção, interpolação e leitura de curvas de juros.

O ponto mais delicado deste módulo não é a interpolação — é a **convenção de
taxa**. A taxa brasileira é efetiva anual em base 252 dias úteis; o *par yield*
americano é *bond-equivalent*, com capitalização semestral. Subtrair uma da
outra sem converter produz um diferencial sistematicamente errado: para um
juro americano de 4,5%, a diferença entre as convenções passa de 5 pontos-base,
e cresce com o nível da taxa.

Por isso `to_effective_annual` roda antes de qualquer comparação entre curvas,
e é ela que os testes protegem com valores calculados à mão.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline, PchipInterpolator
from scipy.optimize import least_squares

from tesouraria.settings import load_config

METODOS = ("pchip", "cubic", "nss", "linear")


# --------------------------------------------------------------- convenções


def to_effective_annual(taxa: pd.Series | np.ndarray | float, capitalizacao: str) -> np.ndarray:
    """Converte uma taxa nominal em taxa efetiva anual, em pontos percentuais.

    - `anual`: a taxa já é efetiva ao ano (caso do Brasil, base 252).
    - `semestral`: par yield americano, com dois cupons por ano.
      i_efetiva = (1 + i/2)^2 - 1
    """
    valores = np.asarray(taxa, dtype=float) / 100.0

    if capitalizacao == "anual":
        efetiva = valores
    elif capitalizacao == "semestral":
        efetiva = (1 + valores / 2) ** 2 - 1
    else:
        raise ValueError(f"capitalização desconhecida: {capitalizacao}")

    return efetiva * 100.0


def convencao(pais: str) -> dict:
    return load_config("sources")["convencoes"][pais.lower()]


def normalizar_curva(df: pd.DataFrame, pais: str) -> pd.DataFrame:
    """Acrescenta `taxa_efetiva`, na mesma base para BR e EUA."""
    out = df.copy()
    out["taxa_efetiva"] = to_effective_annual(out["taxa"], convencao(pais)["capitalizacao"])
    return out


def grade_padrao() -> list[float]:
    return [float(v) for v in load_config("sources")["grade_padrao"]]


# ------------------------------------------------------------------- curva


@dataclass(frozen=True)
class Curva:
    """Uma curva num instante: prazos em anos e taxas em % ao ano."""

    data_ref: dt.date
    rotulo: str
    prazos: np.ndarray
    taxas: np.ndarray

    def __len__(self) -> int:
        return len(self.prazos)

    @property
    def vazia(self) -> bool:
        return len(self.prazos) == 0


def build_curve(
    df: pd.DataFrame,
    data_ref: dt.date | None = None,
    rotulo: str = "",
    coluna_taxa: str = "taxa",
) -> Curva:
    """Monta uma `Curva` a partir de um quadro com `prazo_anos` e uma taxa.

    Vértices repetidos (títulos distintos no mesmo prazo) são consolidados pela
    mediana, que resiste melhor a um preço isolado fora de linha do que a média.
    """
    if df.empty:
        return Curva(data_ref or dt.date.today(), rotulo, np.array([]), np.array([]))

    limpo = df.dropna(subset=["prazo_anos", coluna_taxa])
    limpo = limpo[limpo["prazo_anos"] > 0]
    if limpo.empty:
        return Curva(data_ref or dt.date.today(), rotulo, np.array([]), np.array([]))

    agrupado = (
        limpo.groupby("prazo_anos", as_index=False)[coluna_taxa]
        .median()
        .sort_values("prazo_anos")
    )

    if data_ref is None and "data_ref" in limpo.columns:
        data_ref = pd.to_datetime(limpo["data_ref"]).max().date()

    return Curva(
        data_ref=data_ref or dt.date.today(),
        rotulo=rotulo,
        prazos=agrupado["prazo_anos"].to_numpy(dtype=float),
        taxas=agrupado[coluna_taxa].to_numpy(dtype=float),
    )


# ------------------------------------------------------------- interpolação


def interpolate(curva: Curva, prazos: np.ndarray | list[float], metodo: str = "pchip") -> np.ndarray:
    """Avalia a curva nos prazos pedidos.

    `pchip` é o padrão por ser monotônica por trechos: ela não inventa
    oscilações entre vértices, defeito clássico da spline cúbica em curvas com
    poucos pontos longos. `nss` (Nelson-Siegel-Svensson) suaviza a curva
    inteira com quatro fatores, útil quando os vértices são ruidosos.
    """
    alvo = np.asarray(prazos, dtype=float)
    if curva.vazia:
        return np.full_like(alvo, np.nan, dtype=float)
    if len(curva) == 1:
        return np.full_like(alvo, curva.taxas[0], dtype=float)

    if metodo == "nss":
        parametros = ajustar_nss(curva)
        return nss(alvo, *parametros)

    if metodo == "cubic" and len(curva) >= 4:
        funcao = CubicSpline(curva.prazos, curva.taxas, extrapolate=False)
    elif metodo == "linear":
        return np.interp(alvo, curva.prazos, curva.taxas, left=np.nan, right=np.nan)
    else:
        funcao = PchipInterpolator(curva.prazos, curva.taxas, extrapolate=False)

    return np.asarray(funcao(alvo), dtype=float)


def to_grid(
    curva: Curva, prazos: list[float] | None = None, metodo: str = "pchip"
) -> pd.DataFrame:
    """Projeta a curva na grade padrão de vértices.

    É esta projeção que torna Brasil e EUA comparáveis: as duas curvas passam a
    ter exatamente os mesmos prazos, e a subtração vértice a vértice faz sentido.
    Prazos fora do intervalo observado ficam como NaN — extrapolar a ponta longa
    de uma curva que não tem vértice lá seria inventar informação.
    """
    prazos = prazos or grade_padrao()
    taxas = interpolate(curva, prazos, metodo=metodo)
    return pd.DataFrame(
        {
            "prazo_anos": prazos,
            "taxa": taxas,
            "data_ref": curva.data_ref,
            "rotulo": curva.rotulo,
        }
    )


# ----------------------------------------------- Nelson-Siegel-Svensson


def nss(
    prazo: np.ndarray, beta0: float, beta1: float, beta2: float, beta3: float,
    tau1: float, tau2: float,
) -> np.ndarray:
    """Forma funcional de Nelson-Siegel-Svensson.

    beta0 é o nível de longo prazo, beta1 a inclinação e beta2/beta3 as duas
    corcovas — que é como o mercado costuma descrever uma curva.
    """
    t = np.maximum(np.asarray(prazo, dtype=float), 1e-6)
    tau1 = max(tau1, 1e-6)
    tau2 = max(tau2, 1e-6)

    termo1 = (1 - np.exp(-t / tau1)) / (t / tau1)
    termo2 = termo1 - np.exp(-t / tau1)
    termo3 = (1 - np.exp(-t / tau2)) / (t / tau2) - np.exp(-t / tau2)

    return beta0 + beta1 * termo1 + beta2 * termo2 + beta3 * termo3


def ajustar_nss(curva: Curva) -> tuple[float, ...]:
    """Ajusta os seis parâmetros por mínimos quadrados."""
    if len(curva) < 4:
        raise ValueError("NSS exige ao menos quatro vértices")

    chute = [curva.taxas[-1], curva.taxas[0] - curva.taxas[-1], 0.0, 0.0, 1.5, 5.0]
    limites = (
        [-50, -50, -50, -50, 0.05, 0.05],
        [50, 50, 50, 50, 30, 30],
    )

    resultado = least_squares(
        lambda p: nss(curva.prazos, *p) - curva.taxas,
        x0=chute,
        bounds=limites,
        max_nfev=5000,
    )
    return tuple(resultado.x)


# ---------------------------------------------------------------- métricas


def metricas(curva: Curva, metodo: str = "pchip") -> dict[str, float]:
    """Nível, inclinação e curvatura — o vocabulário usual de mesa."""
    pontos = dict(zip([1.0, 2.0, 5.0, 10.0], interpolate(curva, [1.0, 2.0, 5.0, 10.0], metodo), strict=True))

    def valor(prazo: float) -> float:
        v = pontos.get(prazo)
        return float(v) if v is not None and not np.isnan(v) else float("nan")

    return {
        "nivel": float(np.nanmean(curva.taxas)) if not curva.vazia else float("nan"),
        "curto_1a": valor(1.0),
        "medio_5a": valor(5.0),
        "longo_10a": valor(10.0),
        "inclinacao_10a_2a": valor(10.0) - valor(2.0),
        "inclinacao_5a_1a": valor(5.0) - valor(1.0),
        "curvatura": 2 * valor(5.0) - valor(2.0) - valor(10.0),
    }


def forward(curva: Curva, inicio: float, fim: float, metodo: str = "pchip") -> float:
    """Taxa a termo implícita entre dois prazos, em % ao ano.

    Responde a "que juro o mercado embute para o período entre 2 e 5 anos?" —
    a leitura que separa expectativa de política monetária de prêmio de prazo.
    """
    if fim <= inicio:
        raise ValueError("o prazo final deve ser maior que o inicial")

    taxas = interpolate(curva, [inicio, fim], metodo=metodo)
    if np.isnan(taxas).any():
        return float("nan")

    fator_inicio = (1 + taxas[0] / 100) ** inicio
    fator_fim = (1 + taxas[1] / 100) ** fim
    return float(((fator_fim / fator_inicio) ** (1 / (fim - inicio)) - 1) * 100)


def variacao_bps(curva_a: Curva, curva_b: Curva, prazos: list[float] | None = None,
                 metodo: str = "pchip") -> pd.DataFrame:
    """Variação, em pontos-base, da curva B em relação à curva A por vértice.

    É a tabela que acompanha a sobreposição de datas: não basta ver duas linhas,
    é preciso saber se o movimento foi de nível (paralelo) ou de inclinação.
    """
    prazos = prazos or grade_padrao()
    taxa_a = interpolate(curva_a, prazos, metodo=metodo)
    taxa_b = interpolate(curva_b, prazos, metodo=metodo)

    return pd.DataFrame(
        {
            "prazo_anos": prazos,
            f"{curva_a.rotulo or curva_a.data_ref}": taxa_a,
            f"{curva_b.rotulo or curva_b.data_ref}": taxa_b,
            "variacao_bps": (taxa_b - taxa_a) * 100,
        }
    )
