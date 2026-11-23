"""Atribuição diária do USD/BRL a fatores globais e a um componente Brasil.

A pergunta é retrospectiva: **por que o dólar se mexeu hoje?** Não há previsão
aqui, nem sinal. O retorno do dia é decomposto em

    r_USDBRL = constante + β1·f1 + β2·f2 + β3·f3 + resíduo

com três fatores **ortogonalizados em ordem fixa**:

    f1 = DXY                                   (dólar global)
    f2 = cesta EM  residualizada contra f1     (o que os pares emergentes fizeram
                                                além do dólar global)
    f3 = commodities Brasil residualizada contra f1 e f2

e o resíduo é o "componente Brasil" — o que nem o dólar global, nem os pares,
nem as commodities que o Brasil exporta explicam.

**A ordem é uma escolha de modelagem, não um fato.** Ortogonalizar dá ao fator
que vem primeiro todo o movimento que ele compartilha com os seguintes: um dia
em que DXY e emergentes sobem juntos é creditado ao DXY. Inverter a ordem
mudaria a divisão entre fatores (nunca o resíduo, que só depende do espaço
gerado pelos três). A interface repete isso em texto.

**Estimação fora da amostra.** Os coeficientes usados no dia t vêm da janela
[t − janela, t − 1]; o próprio dia t não entra. Assim um choque grande hoje não
"puxa" os betas para se explicar sozinho, e o resíduo de hoje é de fato o que o
comportamento recente não previa. A ortogonalização também usa os coeficientes
da janela anterior, aplicados aos fatores de hoje. A identidade

    constante + Σ contribuições + resíduo = retorno observado

vale exatamente por construção, porque o resíduo é definido como o que sobra.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tesouraria.analytics.ols import Ajuste, ols

ALVO = "usdbrl"
FATORES = ("dxy", "em", "cmd")
ROTULOS = {
    "constante": "Constante",
    "dxy": "Dólar global (DXY)",
    "em": "Pares emergentes ⊥ DXY",
    "cmd": "Commodities Brasil ⊥ DXY, EM",
    "residuo": "Componente Brasil (resíduo)",
}

TEXTO_ORDEM = (
    "**Ordem de ortogonalização (escolha de modelagem):** 1º DXY; 2º cesta EM "
    "(MXN, CLP, COP, ZAR) depois de retirado o que o DXY explica; 3º cesta de "
    "commodities Brasil depois de retirado o que DXY e EM explicam. O fator que "
    "vem antes fica com todo o movimento que compartilha com os seguintes — num "
    "dia em que DXY e emergentes andam juntos, o crédito vai para o DXY. Trocar a "
    "ordem muda a divisão entre os fatores, mas não o resíduo (componente Brasil). "
    "Coeficientes estimados na janela que termina na sessão anterior: o dia "
    "analisado não entra na própria estimação."
)


@dataclass(frozen=True)
class Ortogonalizacao:
    """Coeficientes que transformam fatores brutos em fatores ortogonais."""

    em_em_dxy: Ajuste      # em ~ f1
    cmd_em_f1f2: Ajuste    # cmd ~ f1 + f2

    def aplicar(self, bruto: np.ndarray) -> np.ndarray:
        """`bruto` (n × 3: dxy, em, cmd) → fatores ortogonais (n × 3)."""
        bruto = np.atleast_2d(bruto)
        f1 = bruto[:, 0]
        f2 = bruto[:, 1] - self.em_em_dxy.prever(f1.reshape(-1, 1))
        f3 = bruto[:, 2] - self.cmd_em_f1f2.prever(np.column_stack([f1, f2]))
        return np.column_stack([f1, f2, f3])


def ortogonalizar(bruto: np.ndarray) -> tuple[np.ndarray, Ortogonalizacao]:
    """Gram-Schmidt por regressão, na ordem DXY → EM → commodities."""
    bruto = np.asarray(bruto, dtype=float)
    f1 = bruto[:, 0]
    ajuste_em = ols(bruto[:, 1], f1)
    f2 = ajuste_em.residuos
    ajuste_cmd = ols(bruto[:, 2], np.column_stack([f1, f2]))
    f3 = ajuste_cmd.residuos
    return np.column_stack([f1, f2, f3]), Ortogonalizacao(ajuste_em, ajuste_cmd)


def retornos_log(niveis: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Retorno log entre observações consecutivas de cada coluna.

    Calculado coluna a coluna sobre as observações existentes: um feriado só
    numa praça não vira retorno zero, vira a variação entre os dois pregões.
    """
    if isinstance(niveis, pd.Series):
        limpa = niveis.dropna()
        limpa = limpa[limpa > 0]
        return np.log(limpa).diff().dropna()
    return pd.DataFrame({c: retornos_log(niveis[c]) for c in niveis.columns})


def media_ponderada(
    retornos: pd.DataFrame, pesos: pd.Series | None = None, cobertura_minima: float = 0.5
) -> pd.DataFrame:
    """Média ponderada linha a linha, renormalizando sobre o que existe no dia.

    Devolve `retorno` e `cobertura` (fração do peso total presente). Dias com
    cobertura abaixo do mínimo ficam NaN — melhor uma lacuna do que um índice
    de "commodities" que naquele dia é só café.
    """
    if retornos.empty:
        return pd.DataFrame(columns=["retorno", "cobertura"])
    if pesos is None:
        pesos = pd.Series(1.0, index=retornos.columns)
    pesos = pesos.reindex(retornos.columns).fillna(0.0)
    pesos = pesos / pesos.sum() if pesos.sum() > 0 else pesos

    presente = retornos.notna()
    peso_presente = presente.mul(pesos, axis=1).sum(axis=1)
    soma = retornos.fillna(0.0).mul(pesos, axis=1).sum(axis=1)
    retorno = soma / peso_presente.replace(0.0, np.nan)
    retorno[peso_presente < cobertura_minima] = np.nan
    return pd.DataFrame({"retorno": retorno, "cobertura": peso_presente})


def montar_base(
    usdbrl: pd.Series, dxy: pd.Series, em: pd.Series, cmd: pd.Series
) -> pd.DataFrame:
    """Retornos log diários alinhados por interseção de datas.

    Interseção, nunca preenchimento: preencher um dia sem cotação com zero
    inventaria um retorno nulo seguido de um retorno de dois dias.
    """
    base = pd.concat({ALVO: usdbrl, "dxy": dxy, "em": em, "cmd": cmd}, axis=1)
    return base.dropna().sort_index()


def atribuir(base: pd.DataFrame, janela: int = 60) -> pd.DataFrame:
    """Decomposição sessão a sessão, com coeficientes da janela anterior.

    `base` tem as colunas `usdbrl, dxy, em, cmd` (retornos log). A saída tem uma
    linha por sessão a partir da `janela`-ésima, com contribuições, resíduo,
    z-score do resíduo, betas, R² e os fatores ortogonais do dia.
    """
    colunas = [ALVO, *FATORES]
    faltando = set(colunas) - set(base.columns)
    if faltando:
        raise ValueError(f"base sem as colunas {sorted(faltando)}")
    if janela < 10:
        raise ValueError("janela curta demais para estimar quatro parâmetros")

    dados = base[colunas].dropna().to_numpy(dtype=float)
    datas = base[colunas].dropna().index
    linhas: list[dict] = []

    for t in range(janela, len(dados)):
        amostra = dados[t - janela : t]
        orto_amostra, orto = ortogonalizar(amostra[:, 1:])
        reg = ols(amostra[:, 0], orto_amostra)

        fatores_hoje = orto.aplicar(dados[t, 1:])[0]
        contribuicoes = reg.betas * fatores_hoje
        observado = dados[t, 0]
        residuo = observado - reg.constante - contribuicoes.sum()

        desvio_f3 = float(np.std(orto_amostra[:, 2], ddof=1))
        linhas.append(
            {
                "data": datas[t],
                "observado": observado,
                "constante": reg.constante,
                **{f"contrib_{f}": c for f, c in zip(FATORES, contribuicoes, strict=True)},
                "residuo": residuo,
                "z_residuo": residuo / reg.desvio_residuo if reg.desvio_residuo > 0 else np.nan,
                **{f"beta_{f}": b for f, b in zip(FATORES, reg.betas, strict=True)},
                "r2": reg.r2,
                **{f"f_{f}": v for f, v in zip(FATORES, fatores_hoje, strict=True)},
                "z_cmd": fatores_hoje[2] / desvio_f3 if desvio_f3 > 0 else np.nan,
                "desvio_residuo": reg.desvio_residuo,
            }
        )

    if not linhas:
        return pd.DataFrame()
    return pd.DataFrame(linhas).set_index("data")


def conferir_identidade(atribuicao: pd.DataFrame) -> pd.Series:
    """Diferença entre o observado e a soma das partes (deve ser ~0)."""
    partes = atribuicao["constante"] + atribuicao["residuo"]
    for f in FATORES:
        partes = partes + atribuicao[f"contrib_{f}"]
    return atribuicao["observado"] - partes


# ----------------------------------------------------------------- flags

LIMIARES_PADRAO = {
    "estresse_z_residuo": 2.0,
    "estresse_di_bps": 10.0,
    "riskoff_vix_pct": 10.0,
    "choque_cmd_desvios": 2.0,
}


def flags(
    atribuicao: pd.DataFrame,
    contexto: pd.DataFrame,
    limiares: dict | None = None,
) -> pd.DataFrame:
    """Marca as sessões que se encaixam em cada regime.

    `contexto` é indexado por data com:
      - `di_longo_bps`: variação do DI longo no dia, em pontos-base;
      - `vix_pct`: variação do VIX no dia, em %;
      - `dxy` e `em`: retornos log brutos do DXY e da cesta EM (EM cotado em
        moeda local por dólar, então positivo = emergentes se depreciando).

    Ausência de dado de contexto nunca dispara flag: NaN compara como falso.
    """
    lim = {**LIMIARES_PADRAO, **(limiares or {})}
    ctx = contexto.reindex(atribuicao.index)

    estresse = (atribuicao["z_residuo"] >= lim["estresse_z_residuo"]) & (
        ctx["di_longo_bps"] >= lim["estresse_di_bps"]
    )
    risk_off = (ctx["vix_pct"] >= lim["riskoff_vix_pct"]) & (ctx["dxy"] > 0) & (ctx["em"] > 0)
    choque = atribuicao["z_cmd"].abs() >= lim["choque_cmd_desvios"]

    return pd.DataFrame(
        {
            "estresse_brasil": estresse.fillna(False).astype(bool),
            "risk_off_global": risk_off.fillna(False).astype(bool),
            "choque_commodities": choque.fillna(False).astype(bool),
        },
        index=atribuicao.index,
    )
