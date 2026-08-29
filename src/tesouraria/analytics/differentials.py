"""Diferenciais entre as curvas brasileira e americana.

O diferencial de juros é o preço do carrego: quanto um investidor ganha por
manter risco Brasil em vez de Treasury. Quando ele se abre e o fluxo cambial
acompanha, o real tende a se apreciar; quando se fecha, o dólar sobe mesmo com
saldo comercial forte. Confrontar as duas coisas é o objetivo central do
aplicativo, e é aqui que a conta é feita.

Toda subtração passa antes por `to_effective_annual` — as duas curvas chegam em
convenções diferentes e compará-las cruas embute um erro que cresce com o nível
da taxa.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from tesouraria.analytics.curve import (
    Curva,
    build_curve,
    grade_padrao,
    interpolate,
    to_effective_annual,
)


def curva_efetiva(df: pd.DataFrame, pais: str, rotulo: str = "",
                  data_ref: dt.date | None = None) -> Curva:
    """Monta a curva já convertida para taxa efetiva anual."""
    if df.empty:
        return Curva(data_ref or dt.date.today(), rotulo, np.array([]), np.array([]))

    capitalizacao = "anual" if pais.upper() == "BR" else "semestral"
    convertido = df.copy()
    convertido["taxa_efetiva"] = to_effective_annual(convertido["taxa"], capitalizacao)
    return build_curve(convertido, data_ref=data_ref, rotulo=rotulo, coluna_taxa="taxa_efetiva")


def diferencial_por_vertice(
    curva_br: Curva,
    curva_us: Curva,
    prazos: list[float] | None = None,
    metodo: str = "pchip",
) -> pd.DataFrame:
    """Diferencial vértice a vértice, em pontos percentuais e em pontos-base.

    Vértices em que uma das curvas não tem observação viram NaN em vez de zero:
    um diferencial ausente é informação diferente de um diferencial nulo.
    """
    prazos = prazos or grade_padrao()
    taxa_br = interpolate(curva_br, prazos, metodo=metodo)
    taxa_us = interpolate(curva_us, prazos, metodo=metodo)
    diferenca = taxa_br - taxa_us

    return pd.DataFrame(
        {
            "prazo_anos": prazos,
            "taxa_br": taxa_br,
            "taxa_us": taxa_us,
            "diferencial_pp": diferenca,
            "diferencial_bps": diferenca * 100,
        }
    )


def inflacao_implicita(curva_nominal: Curva, curva_real: Curva,
                       prazos: list[float] | None = None,
                       metodo: str = "pchip") -> pd.DataFrame:
    """Inflação implícita (breakeven) a partir das curvas nominal e real.

    Usa a relação de Fisher exata — (1+nominal)/(1+real) - 1 — e não a simples
    subtração, que subestima o breakeven em regimes de juro alto como o
    brasileiro.
    """
    prazos = prazos or grade_padrao()
    nominal = interpolate(curva_nominal, prazos, metodo=metodo) / 100
    real = interpolate(curva_real, prazos, metodo=metodo) / 100

    implicita = ((1 + nominal) / (1 + real) - 1) * 100

    return pd.DataFrame(
        {
            "prazo_anos": prazos,
            "nominal": nominal * 100,
            "real": real * 100,
            "implicita": implicita,
        }
    )


def serie_diferencial(
    historico_br: pd.DataFrame,
    historico_us: pd.DataFrame,
) -> pd.DataFrame:
    """Série histórica do diferencial em um vértice.

    Recebe dois quadros com `data_ref` e `taxa` (ver `queries.historico_curva`),
    converte cada um para taxa efetiva e casa as datas. O casamento é por
    interseção: um dia sem pregão em um dos países simplesmente não entra, o
    que é preferível a arrastar a última cotação e criar movimento falso.
    """
    if historico_br.empty or historico_us.empty:
        return pd.DataFrame(columns=["data_ref", "taxa_br", "taxa_us", "diferencial_pp"])

    br = historico_br.copy()
    us = historico_us.copy()
    br["taxa_br"] = to_effective_annual(br["taxa"], "anual")
    us["taxa_us"] = to_effective_annual(us["taxa"], "semestral")

    juntos = pd.merge(
        br[["data_ref", "taxa_br"]], us[["data_ref", "taxa_us"]], on="data_ref", how="inner"
    ).sort_values("data_ref")
    juntos["diferencial_pp"] = juntos["taxa_br"] - juntos["taxa_us"]
    juntos["diferencial_bps"] = juntos["diferencial_pp"] * 100
    return juntos.reset_index(drop=True)


def carry_curto(cdi: pd.DataFrame, fed_funds: pd.DataFrame) -> pd.DataFrame:
    """Carrego de curto prazo: CDI anualizado menos Fed Funds.

    O CDI vem do SGS em taxa diária (série 12); anualizar em base 252 é o que
    o coloca na mesma escala do Fed Funds.
    """
    if cdi.empty or fed_funds.empty:
        return pd.DataFrame(columns=["data_ref", "cdi_anual", "fed_funds", "carry_pp"])

    br = cdi[["data_ref", "valor"]].copy()
    br["cdi_anual"] = ((1 + br["valor"] / 100) ** 252 - 1) * 100

    us = fed_funds[["data_ref", "valor"]].rename(columns={"valor": "fed_funds"}).copy()

    # O Fed Funds mensal é propagado para os dias do mês; é uma taxa de política
    # que só muda em reunião, então repetir o último valor não distorce.
    juntos = pd.merge_asof(
        br.sort_values("data_ref").assign(data_ref=lambda d: pd.to_datetime(d["data_ref"])),
        us.sort_values("data_ref").assign(data_ref=lambda d: pd.to_datetime(d["data_ref"])),
        on="data_ref",
        direction="backward",
    )
    juntos["carry_pp"] = juntos["cdi_anual"] - juntos["fed_funds"]
    return juntos[["data_ref", "cdi_anual", "fed_funds", "carry_pp"]].dropna()


def correlacao_com_cambio(
    diferencial: pd.DataFrame, cambio: pd.DataFrame, janela: int = 63
) -> pd.DataFrame:
    """Correlação móvel entre o diferencial de juros e o dólar.

    Janela padrão de 63 pregões (cerca de um trimestre). O sinal esperado é
    negativo — diferencial maior atrai capital e derruba o dólar; períodos em
    que a correlação inverte costumam ser justamente aqueles em que risco
    fiscal ou externo domina o carrego.
    """
    if diferencial.empty or cambio.empty:
        return pd.DataFrame(columns=["data_ref", "diferencial_pp", "cambio", "correlacao"])

    esq = diferencial[["data_ref", "diferencial_pp"]].copy()
    dir_ = cambio[["data_ref", "valor"]].rename(columns={"valor": "cambio"}).copy()
    for quadro in (esq, dir_):
        quadro["data_ref"] = pd.to_datetime(quadro["data_ref"])

    juntos = pd.merge(esq, dir_, on="data_ref", how="inner").sort_values("data_ref")
    if len(juntos) < janela:
        juntos["correlacao"] = np.nan
        return juntos.reset_index(drop=True)

    juntos["correlacao"] = (
        juntos["diferencial_pp"].rolling(janela).corr(juntos["cambio"]).round(4)
    )
    return juntos.reset_index(drop=True)
