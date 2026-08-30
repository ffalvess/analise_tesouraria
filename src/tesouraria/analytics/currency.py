"""Prêmio do real: quanto o real se descolou da cesta de moedas.

A pergunta que este módulo responde é a que separa duas leituras que o gráfico
do dólar sozinho confunde: **o dólar subiu porque subiu contra todo mundo, ou
porque o real ficou pior?** Sem essa separação, um dia de alta global do dólar
é lido como deterioração do Brasil, e o inverso também.

Duas medidas, de propósito:

    prêmio_simples = Δ% USD/BRL − Δ% cesta
    prêmio_beta    = resíduo acumulado de  r_brl = α + β·r_cesta + ε

A simples é conferível na mão e não depende de nenhum parâmetro estimado. A
ajustada existe porque **o real tem beta historicamente acima de 1** contra o
dólar global: num dia em que a cesta sobe 1%, o real cai mais que 1% só pela
sua sensibilidade estrutural, sem que nada tenha acontecido no Brasil. Sem o
ajuste, esse excesso mecânico apareceria como prêmio de risco todo dia.

**Convenção de sinal**, que vale para as duas medidas e é repetida na
interface: prêmio **positivo** = o real se desvalorizou **além** do que o dólar
global explica — algo específico do Brasil. Negativo = o real andou melhor que
a cesta.

A cesta nunca deve ser a `DTWEXBGS` (ampla), porque ela **inclui o próprio
real**: parte do movimento entraria dos dois lados da conta e amorteceria o
resultado. As réguas são `DTWEXAFEGS` (economias avançadas) e `DTWEXEMEGS`
(emergentes), que excluem o Brasil.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Janelas oferecidas na interface, em dias corridos.
JANELAS: dict[str, int] = {"dia": 1, "semana": 7, "mês": 30}

# Pregões usados para estimar o beta. Um ano é longo o bastante para o
# parâmetro ser estável e curto o bastante para acompanhar mudança de regime.
JANELA_BETA = 252


@dataclass(frozen=True)
class Premio:
    """Resultado da comparação entre o real e uma cesta, numa janela."""

    cesta: str
    inicio: dt.date
    fim: dt.date
    var_brl: float          # % de variação do USD/BRL
    var_cesta: float        # % de variação da cesta
    premio_simples: float   # pontos percentuais
    premio_beta: float      # pontos percentuais, líquido do beta
    beta: float
    r2: float
    observacoes: int

    @property
    def leitura(self) -> str:
        """Uma frase que diz o que o número significa, com o sinal certo."""
        if not np.isfinite(self.premio_simples):
            return "Dados insuficientes na janela escolhida."

        if self.premio_simples > 0:
            direcao = (
                f"o real se desvalorizou {abs(self.premio_simples):.2f} p.p. **além** "
                "do que o movimento global do dólar explica"
            )
        else:
            direcao = (
                f"o real se saiu {abs(self.premio_simples):.2f} p.p. **melhor** que a "
                "cesta — o dólar subiu menos aqui do que lá fora"
            )

        return (
            f"No período, o dólar variou {self.var_brl:+.2f}% contra o real e "
            f"{self.var_cesta:+.2f}% contra a cesta {self.cesta}: {direcao}. "
            f"Descontado o beta de {self.beta:.2f}, sobram {self.premio_beta:+.2f} p.p."
        )


def alinhar(cambio: pd.DataFrame, cesta: pd.DataFrame) -> pd.DataFrame:
    """Casa USD/BRL e cesta pelas datas em que ambos têm cotação.

    Interseção, nunca preenchimento: feriado brasileiro e feriado americano não
    coincidem, e arrastar a última cotação criaria um dia de variação zero que
    não existiu — ruído que contaminaria tanto o beta quanto o prêmio.
    """
    if cambio.empty or cesta.empty:
        return pd.DataFrame(columns=["data_ref", "usdbrl", "cesta", "r_brl", "r_cesta"])

    esq = cambio[["data_ref", "valor"]].rename(columns={"valor": "usdbrl"}).copy()
    dir_ = cesta[["data_ref", "valor"]].rename(columns={"valor": "cesta"}).copy()
    for quadro in (esq, dir_):
        quadro["data_ref"] = pd.to_datetime(quadro["data_ref"])

    juntos = (
        pd.merge(esq, dir_, on="data_ref", how="inner")
        .dropna(subset=["usdbrl", "cesta"])
        .sort_values("data_ref")
        .reset_index(drop=True)
    )
    if juntos.empty:
        return juntos.assign(r_brl=[], r_cesta=[])

    # Retornos logarítmicos: aditivos no tempo, o que permite somar os resíduos
    # ao longo da janela para obter o prêmio acumulado.
    juntos["r_brl"] = np.log(juntos["usdbrl"]).diff() * 100
    juntos["r_cesta"] = np.log(juntos["cesta"]).diff() * 100
    return juntos


def estimar_beta(base: pd.DataFrame, janela: int = JANELA_BETA) -> tuple[float, float]:
    """Beta e R² do real contra a cesta, nos últimos `janela` pregões.

    Regressão simples por covariância — sem `statsmodels`, porque aqui só
    interessam o coeficiente e o ajuste, e a conta cabe em duas linhas.
    """
    dados = base[["r_brl", "r_cesta"]].dropna().tail(janela)
    if len(dados) < 30:
        return float("nan"), float("nan")

    x, y = dados["r_cesta"], dados["r_brl"]
    variancia = x.var()
    if not variancia or not np.isfinite(variancia):
        return float("nan"), float("nan")

    beta = float(x.cov(y) / variancia)
    r2 = float(x.corr(y) ** 2)
    return beta, r2


def calcular(
    base: pd.DataFrame,
    janela: str = "dia",
    nome_cesta: str = "cesta",
    janela_beta: int = JANELA_BETA,
) -> Premio:
    """Prêmio do real na janela pedida (`dia`, `semana` ou `mês`)."""
    vazio = Premio(
        nome_cesta, dt.date.today(), dt.date.today(),
        float("nan"), float("nan"), float("nan"), float("nan"),
        float("nan"), float("nan"), 0,
    )
    if base.empty or len(base) < 2:
        return vazio

    dias = JANELAS.get(janela, 1)
    fim = base["data_ref"].max()
    corte = fim - pd.Timedelta(days=dias)

    # O ponto inicial é o último pregão *até* o corte, não o mais próximo dele:
    # numa segunda-feira, a "variação do dia" tem de comparar com a sexta.
    anteriores = base[base["data_ref"] <= corte]
    if anteriores.empty:
        return vazio
    inicio = anteriores["data_ref"].max()

    trecho = base[(base["data_ref"] > inicio) & (base["data_ref"] <= fim)]
    if trecho.empty:
        return vazio

    ponto_inicial = base[base["data_ref"] == inicio].iloc[0]
    ponto_final = base[base["data_ref"] == fim].iloc[0]

    var_brl = (ponto_final["usdbrl"] / ponto_inicial["usdbrl"] - 1) * 100
    var_cesta = (ponto_final["cesta"] / ponto_inicial["cesta"] - 1) * 100

    beta, r2 = estimar_beta(base, janela_beta)
    # Prêmio por beta: o que o real fez menos o que o beta previa a partir da
    # cesta. Com beta indisponível, cai para 1 — que é o prêmio simples.
    fator = beta if np.isfinite(beta) else 1.0
    premio_beta = float(trecho["r_brl"].sum() - fator * trecho["r_cesta"].sum())

    return Premio(
        cesta=nome_cesta,
        inicio=inicio.date(),
        fim=fim.date(),
        var_brl=float(var_brl),
        var_cesta=float(var_cesta),
        premio_simples=float(var_brl - var_cesta),
        premio_beta=premio_beta,
        beta=beta,
        r2=r2,
        observacoes=int(len(trecho)),
    )


def serie_premio(base: pd.DataFrame, janela_beta: int = JANELA_BETA) -> pd.DataFrame:
    """Prêmio acumulado ao longo do tempo, para o gráfico histórico.

    O beta é móvel, então o prêmio de cada dia é medido contra a sensibilidade
    vigente naquele momento, e não contra um beta estimado com informação do
    futuro.
    """
    if base.empty or len(base) < janela_beta:
        return pd.DataFrame(columns=["data_ref", "premio_dia", "premio_acumulado", "beta"])

    dados = base.dropna(subset=["r_brl", "r_cesta"]).copy()
    if len(dados) < janela_beta:
        return pd.DataFrame(columns=["data_ref", "premio_dia", "premio_acumulado", "beta"])

    covariancia = dados["r_brl"].rolling(janela_beta).cov(dados["r_cesta"])
    variancia = dados["r_cesta"].rolling(janela_beta).var()
    dados["beta"] = covariancia / variancia.replace(0, np.nan)

    # Beta defasado em um dia: usar o beta que já era conhecido na véspera evita
    # olhar para o futuro ao explicar o movimento de hoje.
    dados["premio_dia"] = dados["r_brl"] - dados["beta"].shift(1) * dados["r_cesta"]
    dados = dados.dropna(subset=["premio_dia"])
    dados["premio_acumulado"] = dados["premio_dia"].cumsum()

    return dados[["data_ref", "premio_dia", "premio_acumulado", "beta"]].reset_index(drop=True)


def normalizar(base: pd.DataFrame, desde: pd.Timestamp | None = None) -> pd.DataFrame:
    """Séries em base 100 na data inicial, para sobrepor no mesmo eixo.

    Dólar e índice de cesta vivem em escalas diferentes (R$ 5,20 contra 118
    pontos); só normalizados é que dá para ver qual andou mais.
    """
    if base.empty:
        return base

    trecho = base if desde is None else base[base["data_ref"] >= desde]
    if trecho.empty:
        return trecho

    out = trecho.copy()
    out["usdbrl_100"] = out["usdbrl"] / out["usdbrl"].iloc[0] * 100
    out["cesta_100"] = out["cesta"] / out["cesta"].iloc[0] * 100
    return out
