"""Pontuação de tom (hawkish/dovish) de discursos, atas e relatórios.

O método é deliberadamente simples e determinístico: um léxico ponderado por
idioma, casamento de expressões compostas antes de palavras isoladas e
inversão do lado quando uma negação aparece perto. Não há chamada de modelo,
o que torna o resultado reprodutível, auditável e testável — e é isso que
permite comparar o tom de hoje com o de dois anos atrás sem que a régua tenha
mudado no meio.

    score = (peso_hawkish - peso_dovish) / (peso_hawkish + peso_dovish)

O resultado fica em [-1, 1]: positivo indica comunicação mais dura (viés de
juros mais altos), negativo indica comunicação mais branda.

Limitação a ter em conta ao ler os gráficos: um léxico não entende ironia,
condicional ou citação de terceiros. O score serve para detectar *mudanças de
inflexão* numa série de discursos da mesma autoridade, não para julgar um
texto isolado.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass

import pandas as pd

from tesouraria.settings import load_config

NAO_ALFANUM = re.compile(r"[^a-z0-9]+")


def normalizar(texto: str) -> list[str]:
    """Minúsculas, sem acentos e sem pontuação, devolvido como lista de tokens."""
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFKD", texto.lower())
        if not unicodedata.combining(c)
    )
    return NAO_ALFANUM.sub(" ", sem_acento).split()


@dataclass(frozen=True)
class Termo:
    tokens: tuple[str, ...]
    peso: float
    lado: str  # hawkish | dovish


@dataclass(frozen=True)
class Lexico:
    termos: tuple[Termo, ...]
    negacoes: frozenset[str]
    janela_negacao: int


@dataclass(frozen=True)
class Tom:
    score: float
    n_hawk: int
    n_dove: int
    peso_hawk: float
    peso_dove: float

    @property
    def rotulo(self) -> str:
        if self.score >= 0.25:
            return "hawkish"
        if self.score <= -0.25:
            return "dovish"
        return "neutro"


@functools.lru_cache(maxsize=4)
def carregar_lexico(idioma: str = "pt") -> Lexico:
    bruto = load_config(f"lexicon_{idioma}")
    termos: list[Termo] = []
    for lado in ("hawkish", "dovish"):
        for expressao, peso in (bruto.get(lado) or {}).items():
            tokens = tuple(normalizar(str(expressao)))
            if tokens:
                termos.append(Termo(tokens, float(peso), lado))

    # Expressões mais longas primeiro: "riscos de alta" deve consumir os
    # tokens antes que "alta" os pegue isoladamente.
    termos.sort(key=lambda t: len(t.tokens), reverse=True)

    return Lexico(
        termos=tuple(termos),
        negacoes=frozenset(normalizar(" ".join(bruto.get("negacoes") or []))),
        janela_negacao=int(bruto.get("janela_negacao", 3)),
    )


def pontuar(texto: str, idioma: str = "pt") -> Tom:
    """Calcula o tom de um texto."""
    if not texto or not texto.strip():
        return Tom(0.0, 0, 0, 0.0, 0.0)

    lexico = carregar_lexico(idioma if idioma in ("pt", "en") else "pt")
    tokens = normalizar(texto)
    consumido = [False] * len(tokens)

    pesos = {"hawkish": 0.0, "dovish": 0.0}
    contagens = {"hawkish": 0, "dovish": 0}

    for termo in lexico.termos:
        n = len(termo.tokens)
        alvo = list(termo.tokens)
        for i in range(len(tokens) - n + 1):
            if any(consumido[i : i + n]):
                continue
            if tokens[i : i + n] != alvo:
                continue

            for j in range(i, i + n):
                consumido[j] = True

            inicio = max(0, i - lexico.janela_negacao)
            negado = any(t in lexico.negacoes for t in tokens[inicio:i])
            lado = _inverter(termo.lado) if negado else termo.lado

            pesos[lado] += termo.peso
            contagens[lado] += 1

    total = pesos["hawkish"] + pesos["dovish"]
    score = (pesos["hawkish"] - pesos["dovish"]) / total if total else 0.0

    return Tom(
        score=round(score, 4),
        n_hawk=contagens["hawkish"],
        n_dove=contagens["dovish"],
        peso_hawk=round(pesos["hawkish"], 3),
        peso_dove=round(pesos["dovish"], 3),
    )


def _inverter(lado: str) -> str:
    return "dovish" if lado == "hawkish" else "hawkish"


def pontuar_quadro(df: pd.DataFrame, coluna: str = "texto") -> pd.DataFrame:
    """Aplica `pontuar` a um quadro de documentos, respeitando o idioma de cada um."""
    if df.empty:
        return df.assign(score_tom=pd.NA, n_hawk=pd.NA, n_dove=pd.NA)

    resultados = [
        pontuar(str(linha.get(coluna) or ""), str(linha.get("idioma") or "pt"))
        for _, linha in df.iterrows()
    ]
    out = df.copy()
    out["score_tom"] = [r.score for r in resultados]
    out["n_hawk"] = [r.n_hawk for r in resultados]
    out["n_dove"] = [r.n_dove for r in resultados]
    return out


def serie_de_tom(documentos: pd.DataFrame, janela: int = 5) -> pd.DataFrame:
    """Série temporal do tom com média móvel, para sobrepor à curva de juros."""
    if documentos.empty:
        return pd.DataFrame(columns=["data_pub", "instituicao", "score_tom", "media_movel"])

    df = documentos.dropna(subset=["data_pub", "score_tom"]).copy()
    df["data_pub"] = pd.to_datetime(df["data_pub"])
    df = df.sort_values("data_pub")
    df["media_movel"] = (
        df.groupby("instituicao")["score_tom"]
        .transform(lambda s: s.rolling(janela, min_periods=1).mean())
        .round(4)
    )
    return df[["data_pub", "instituicao", "autor", "titulo", "score_tom", "media_movel"]]
