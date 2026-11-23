"""Montagem dos dados da aba de câmbio: resumos dos cards, fatores e DI.

Funções puras sobre DataFrames — a interface (`ui/cambio.py`) só busca os dados
no banco e desenha. Isso deixa testável tudo o que tem conta.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tesouraria import calendario
from tesouraria.analytics import curve as curva_mod
from tesouraria.analytics.atribuicao import media_ponderada

# --------------------------------------------------------------- cards


@dataclass(frozen=True)
class Resumo:
    nivel: float
    data: dt.date | None
    var_1d: float
    var_5du: float
    var_21du: float
    defasado: bool
    atraso_du: int | None
    unidade_var: str  # "%" ou "bps"


def _variacao(atual: float, anterior: float, tipo: str) -> float:
    if not (np.isfinite(atual) and np.isfinite(anterior)):
        return float("nan")
    if tipo == "bps":
        return (atual - anterior) * 100.0
    return (atual / anterior - 1.0) * 100.0 if anterior else float("nan")


def resumo_serie(
    serie: pd.Series, tipo: str, hoje: dt.date, tolerancia_du: int = 1
) -> Resumo:
    """Nível, variações em 1 sessão, 5 e 21 dias úteis, e selo de defasagem.

    As variações de 5 e 21 dias úteis usam o calendário brasileiro: comparam a
    última observação com a última observação disponível em ou antes de
    `data − 5 du` (e `− 21 du`). A do dia compara com a observação anterior.

    `defasado` quando a última observação está mais de `tolerancia_du` dias
    úteis atrás do último dia útil até `hoje`.
    """
    unidade = "bps" if tipo == "bps" else "%"
    limpa = serie.dropna().sort_index()
    if limpa.empty:
        nan = float("nan")
        return Resumo(nan, None, nan, nan, nan, True, None, unidade)

    limpa.index = pd.to_datetime(limpa.index)
    ultima_data = limpa.index[-1].date()
    atual = float(limpa.iloc[-1])
    anterior = float(limpa.iloc[-2]) if len(limpa) > 1 else float("nan")

    def valor_ate(data: dt.date) -> float:
        recorte = limpa[limpa.index <= pd.Timestamp(data)]
        return float(recorte.iloc[-1]) if not recorte.empty else float("nan")

    v5 = valor_ate(calendario.somar_du(ultima_data, -5))
    v21 = valor_ate(calendario.somar_du(ultima_data, -21))

    referencia = calendario.ultimo_dia_util(hoje)
    atraso = max(0, calendario.dias_uteis(ultima_data, referencia))
    return Resumo(
        nivel=atual,
        data=ultima_data,
        var_1d=_variacao(atual, anterior, tipo),
        var_5du=_variacao(atual, v5, tipo),
        var_21du=_variacao(atual, v21, tipo),
        defasado=atraso > tolerancia_du,
        atraso_du=atraso,
        unidade_var=unidade,
    )


# ------------------------------------------------------------- fatores


def largo(series: pd.DataFrame) -> pd.DataFrame:
    """`serie_id, data_ref, valor` (longo) → uma coluna por série, índice de datas."""
    if series.empty:
        return pd.DataFrame()
    tabela = series.pivot_table(index="data_ref", columns="serie_id", values="valor", aggfunc="last")
    tabela.index = pd.to_datetime(tabela.index)
    return tabela.sort_index()


def retornos_na_grade(niveis: pd.DataFrame, grade: pd.DatetimeIndex) -> pd.DataFrame:
    """Retornos log de cada coluna entre sessões consecutivas da grade.

    Todas as séries medem o mesmo intervalo (da sessão anterior da grade até a
    atual). Um componente sem cotação numa das duas pontas fica NaN naquele dia
    — nunca zero.
    """
    alinhado = niveis.reindex(grade)
    alinhado = alinhado.where(alinhado > 0)
    return np.log(alinhado).diff().iloc[1:]


@dataclass
class Fatores:
    base: pd.DataFrame                         # usdbrl, dxy, em, cmd (retornos log)
    retornos: pd.DataFrame                     # retornos brutos de cada componente na grade
    pesos_usados: pd.Series                    # pesos da cesta de commodities após exclusões
    excluidos: dict[str, str] = field(default_factory=dict)  # componente → motivo
    cobertura_cmd: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


def montar_fatores(
    niveis: pd.DataFrame,
    usdbrl: str,
    dxy: str,
    em: list[str],
    minimo_em: int,
    cmd_pesos: pd.Series,           # índice = ticker
    cobertura_minima: float,
    minimo_sessoes_componente: int,
    janela_recente: int = 60,
) -> Fatores:
    """Grade = sessões com USD/BRL **e** DXY; cestas medidas nessa mesma grade."""
    if usdbrl not in niveis or dxy not in niveis:
        vazio = pd.DataFrame(columns=["usdbrl", "dxy", "em", "cmd"], dtype=float,
                             index=pd.DatetimeIndex([]))
        faltam = dict.fromkeys(cmd_pesos.index, "sem cotação no banco")
        return Fatores(vazio, pd.DataFrame(), pd.Series(dtype=float), faltam)

    grade = niveis[[usdbrl, dxy]].dropna().index
    colunas = [c for c in {usdbrl, dxy, *em, *cmd_pesos.index} if c in niveis.columns]
    ret = retornos_na_grade(niveis[colunas], grade)

    em_presentes = [c for c in em if c in ret]
    if em_presentes:
        cesta_em = media_ponderada(
            ret[em_presentes], cobertura_minima=minimo_em / max(len(em), 1)
        )["retorno"]
    else:
        cesta_em = pd.Series(np.nan, index=ret.index)

    # Componentes com histórico recente ralo saem da cesta inteira (e a tela diz).
    excluidos: dict[str, str] = {}
    pesos = cmd_pesos.copy()
    for ticker in list(pesos.index):
        if ticker not in ret:
            excluidos[ticker] = "sem cotação no banco"
            continue
        recentes = int(ret[ticker].tail(janela_recente).notna().sum())
        if recentes < minimo_sessoes_componente:
            excluidos[ticker] = (
                f"só {recentes} de {janela_recente} sessões recentes com cotação"
            )
    pesos = pesos.drop(list(excluidos), errors="ignore")
    pesos = pesos / pesos.sum() if pesos.sum() > 0 else pesos

    if len(pesos):
        cmd = media_ponderada(ret[list(pesos.index)], pesos, cobertura_minima)
        cesta_cmd, cobertura = cmd["retorno"], cmd["cobertura"]
    else:
        cesta_cmd = pd.Series(np.nan, index=ret.index)
        cobertura = pd.Series(0.0, index=ret.index)

    base = pd.DataFrame(
        {"usdbrl": ret[usdbrl], "dxy": ret[dxy], "em": cesta_em, "cmd": cesta_cmd}
    ).dropna()
    return Fatores(base, ret, pesos, excluidos, cobertura)


# ------------------------------------------------------------------ DI


def escolher_fonte_curva(contagens: dict[str, int], preferencia: list[str], minimo: int) -> str | None:
    for fonte in preferencia:
        if contagens.get(fonte, 0) >= minimo:
            return fonte
    candidatas = [f for f in preferencia if contagens.get(f, 0) > 0]
    return candidatas[0] if candidatas else None


def serie_prazo_constante(
    vertices: pd.DataFrame, prazos: list[float], metodo: str = "pchip"
) -> pd.DataFrame:
    """Para cada data, a taxa interpolada em cada prazo constante (anos).

    Reaproveita `analytics.curve` (mesma montagem e interpolação das páginas de
    curva). Não extrapola: um prazo fora do intervalo de vértices do dia sai NaN.
    """
    if vertices.empty:
        return pd.DataFrame(columns=prazos)
    linhas = {}
    for data, grupo in vertices.groupby("data_ref"):
        curva = curva_mod.build_curve(grupo, data_ref=pd.Timestamp(data).date())
        if len(curva) < 2:
            continue
        linhas[pd.Timestamp(data)] = curva_mod.interpolate(curva, prazos, metodo=metodo)
    if not linhas:
        return pd.DataFrame(columns=prazos)
    return pd.DataFrame.from_dict(linhas, orient="index", columns=prazos).sort_index()
