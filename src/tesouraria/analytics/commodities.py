"""Cesta de commodities Brasil: pesos pela pauta exportadora e índice diário.

Os pesos são a participação de cada produto no valor exportado (FOB, US$) dos
últimos 12 meses fechados no Comex Stat, renormalizados para somar 1 entre os
componentes ativos. O índice diário é a média ponderada dos retornos log dos
futuros de cada produto — renormalizada, a cada dia, sobre os componentes que
têm cotação naquele dia (ver `atribuicao.media_ponderada`).

Nada aqui é digitado à mão: sem dados do Comex no banco, os pesos caem para
pesos iguais e a tela diz isso.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

PREFIXO_SERIE = "comex_sh4_"


def serie_id_sh4(codigo: str) -> str:
    return f"{PREFIXO_SERIE}{str(codigo).zfill(4)}"


@dataclass(frozen=True)
class PesosCesta:
    pesos: pd.Series                 # índice = nome do produto, soma 1
    origem: str                      # "comex" | "iguais"
    inicio: pd.Timestamp | None = None
    fim: pd.Timestamp | None = None
    valores_usd: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    aviso: str | None = None


def pesos_iguais(ativos: list[str], aviso: str) -> PesosCesta:
    pesos = pd.Series(1.0 / len(ativos), index=ativos) if ativos else pd.Series(dtype=float)
    return PesosCesta(pesos=pesos, origem="iguais", aviso=aviso)


def calcular_pesos(
    mensal: pd.DataFrame, produtos: dict[str, dict], ativos: list[str], meses: int = 12
) -> PesosCesta:
    """Pesos a partir do valor exportado mensal por SH4.

    `mensal` tem `serie_id`, `data_ref` (primeiro dia do mês) e `valor` (US$).
    A janela termina no último mês em que **todos** os produtos ativos têm
    dado — o Comex publica com defasagem, e somar 12 meses de um produto com 11
    de outro distorceria o peso.
    """
    if not ativos:
        return pesos_iguais([], "nenhum componente ativo na configuração")
    if mensal is None or mensal.empty:
        return pesos_iguais(
            ativos, "Sem dados do Comex Stat no banco: pesos iguais, provisoriamente."
        )

    mapa = {
        serie_id_sh4(codigo): nome
        for nome, cfg in produtos.items()
        if nome in ativos
        for codigo in cfg.get("sh4", [])
    }
    dados = mensal[mensal["serie_id"].isin(mapa)].copy()
    if dados.empty:
        return pesos_iguais(
            ativos, "O Comex Stat não trouxe nenhum dos códigos SH4 da cesta: pesos iguais."
        )

    dados["produto"] = dados["serie_id"].map(mapa)
    dados["mes"] = pd.to_datetime(dados["data_ref"]).dt.to_period("M")
    tabela = dados.pivot_table(index="mes", columns="produto", values="valor", aggfunc="sum")

    faltando = [p for p in ativos if p not in tabela.columns]
    if faltando:
        return pesos_iguais(
            ativos, f"Sem dados do Comex Stat para {', '.join(faltando)}: pesos iguais."
        )

    completos = tabela[ativos].dropna()
    if completos.empty:
        return pesos_iguais(ativos, "Nenhum mês com todos os produtos no Comex Stat: pesos iguais.")
    fim = completos.index.max()
    janela = tabela.loc[(tabela.index > fim - meses) & (tabela.index <= fim), ativos]
    if len(janela.dropna()) < meses:
        return pesos_iguais(
            ativos,
            f"Menos de {meses} meses completos no Comex Stat (há {len(janela.dropna())}): "
            "pesos iguais.",
        )

    valores = janela.sum()
    pesos = valores / valores.sum()
    return PesosCesta(
        pesos=pesos.sort_values(ascending=False),
        origem="comex",
        inicio=janela.index.min().to_timestamp(),
        fim=fim.to_timestamp(),
        valores_usd=valores.sort_values(ascending=False),
    )


def renormalizar(pesos: pd.Series, disponiveis: list[str]) -> pd.Series:
    """Pesos restritos aos componentes disponíveis, somando 1 de novo."""
    restritos = pesos.reindex([p for p in pesos.index if p in disponiveis]).dropna()
    total = restritos.sum()
    return restritos / total if total > 0 else restritos
