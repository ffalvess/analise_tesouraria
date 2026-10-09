"""Inflação, mercado de trabalho e PIB dos Estados Unidos.

As séries chegam do FRED no formato longo do banco (`data_ref`, `valor`) e em
três frequências — semanal, mensal e trimestral. Tudo aqui devolve o mesmo
formato longo, que é o que os gráficos consomem.

Duas decisões atravessam o módulo:

* **Variações são calculadas por data, não por posição.** A paralisação do
  governo americano em outubro de 2025 deixou meses sem divulgação, e o FRED
  simplesmente não tem essas linhas. Um `pct_change(12)` contaria doze linhas
  para trás e compararia setembro de 2026 com agosto de 2025 sem avisar.
  Deslocar pela data devolve vazio onde falta o mês de comparação, que é o
  correto.
* **Contribuições se somam; níveis encadeados, não.** O PIB real em dólares
  encadeados não é a soma dos componentes em dólares encadeados — a soma
  erra mais quanto mais longe do ano-base. As contribuições ao crescimento que
  o BEA publica, sim, somam o crescimento do PIB por construção. Por isso a
  decomposição — pela despesa e pela produção — sai sempre das contribuições.
"""

from __future__ import annotations

import pandas as pd

# Limiar da regra de Sahm: média de 3 meses do desemprego 0,5 p.p. acima da
# mínima das médias dos 12 meses anteriores.
LIMIAR_SAHM = 0.5
# A meta do Fed é de 2% no PCE, não no CPI.
META_FED = 2.0

# Ótica da despesa: série de contribuição do BEA -> componente.
CONTRIBUICOES_DESPESA = {
    "DPCERY2Q224SBEA": "consumo",
    "A006RY2Q224SBEA": "investimento",
    "A822RY2Q224SBEA": "governo",
    "A020RY2Q224SBEA": "exportacoes",
    "A021RY2Q224SBEA": "importacoes",
}

# Ótica da produção: as séries do GDP by Industry que bastam para o recorte do
# IBGE. Ver `contribuicoes_producao`.
CONTRIBUICOES_PRODUCAO = ("CPGDPAI", "CPGDPGPI", "CPGDPAFH", "CPGDPU")
CONTRIBUICOES_INDUSTRIA = (*CONTRIBUICOES_PRODUCAO, "CPGDPC", "CPGDPMD", "CPGDPMN")


# ------------------------------------------------------------------ básicos


def serie_temporal(dados: pd.DataFrame | None) -> pd.Series:
    """Formato longo -> série indexada por data, ordenada e sem duplicatas."""
    if dados is None or dados.empty:
        return pd.Series(dtype=float)
    serie = pd.Series(
        pd.to_numeric(dados["valor"], errors="coerce").to_numpy(),
        index=pd.to_datetime(dados["data_ref"]),
        dtype=float,
    )
    serie = serie[~serie.index.duplicated(keep="last")].sort_index()
    return serie.dropna()


def para_quadro(serie: pd.Series) -> pd.DataFrame:
    """Série indexada por data -> formato longo (`data_ref`, `valor`), sem vazios."""
    limpo = serie.dropna()
    return pd.DataFrame({"data_ref": limpo.index, "valor": limpo.to_numpy()})


def frequencia(dados: pd.DataFrame | pd.Series | None) -> str:
    """'diaria', 'semanal', 'mensal' ou 'trimestral', pelo intervalo típico."""
    serie = dados if isinstance(dados, pd.Series) else serie_temporal(dados)
    if len(serie) < 2:
        return "mensal"
    dias = serie.index.to_series().diff().dt.days.median()
    if dias > 80:
        return "trimestral"
    if dias > 20:
        return "mensal"
    return "semanal" if dias > 3 else "diaria"


def _defasagem(serie: pd.Series, meses: int) -> pd.Series:
    """Valor de `meses` meses antes, alinhado à data de cada observação.

    Séries semanais (pedidos de seguro-desemprego) não caem no mesmo dia da
    semana de um ano para o outro; para elas, 12 meses viram 52 semanas.
    """
    if serie.empty:
        return serie
    if frequencia(serie) in ("semanal", "diaria"):
        deslocamento = pd.DateOffset(weeks=round(meses * 52 / 12))
    else:
        deslocamento = pd.DateOffset(months=meses)
    return serie.shift(freq=deslocamento).reindex(serie.index)


def variacao_anual(dados: pd.DataFrame | None) -> pd.DataFrame:
    """Variação em 12 meses (%), contra o mesmo período do ano anterior."""
    serie = serie_temporal(dados)
    return para_quadro((serie / _defasagem(serie, 12) - 1) * 100)


def variacao_anualizada(dados: pd.DataFrame | None, meses: int) -> pd.DataFrame:
    """Variação nos últimos `meses` meses, anualizada (%).

    Com `meses=3` sobre uma série trimestral, é a taxa trimestral anualizada
    (SAAR) — a convenção em que o PIB americano é divulgado. Sobre um índice de
    preços mensal, é a leitura de "momento" que o Fed acompanha: a inflação dos
    últimos três ou seis meses, posta em ritmo anual.
    """
    serie = serie_temporal(dados)
    return para_quadro(((serie / _defasagem(serie, meses)) ** (12 / meses) - 1) * 100)


def diferenca(dados: pd.DataFrame | None, meses: int = 1) -> pd.DataFrame:
    """Variação absoluta em `meses` meses — a criação de vagas do payroll."""
    serie = serie_temporal(dados)
    return para_quadro(serie - _defasagem(serie, meses))


def _grade(serie: pd.Series) -> pd.Series:
    """Põe séries mensais e trimestrais numa grade regular, com vazio onde falta.

    Semanais e diárias ficam como estão: não têm buracos de divulgação, e a
    âncora do dia da semana varia de série para série.
    """
    regra = {"trimestral": "QS", "mensal": "MS"}.get(frequencia(serie))
    if len(serie) < 2 or regra is None:
        return serie
    return serie.resample(regra).mean()


def media_movel(dados: pd.DataFrame | None, janela: int) -> pd.DataFrame:
    """Média dos últimos `janela` períodos, tolerando um ausente."""
    serie = _grade(serie_temporal(dados))
    if serie.empty:
        return para_quadro(serie)
    return para_quadro(serie.rolling(janela, min_periods=max(1, janela - 1)).mean())


def razao(numerador: pd.DataFrame | None, denominador: pd.DataFrame | None) -> pd.DataFrame:
    """Quociente entre duas séries na mesma data — vagas por desempregado."""
    a, b = serie_temporal(numerador), serie_temporal(denominador)
    return para_quadro((a / b.where(b != 0)).dropna())


def ultimo(dados: pd.DataFrame | None, posicao: int = -1) -> tuple[pd.Timestamp | None, float]:
    """Data e valor de uma observação contada do fim (-1 = a mais recente)."""
    serie = serie_temporal(dados)
    if len(serie) < abs(posicao):
        return None, float("nan")
    return serie.index[posicao], float(serie.iloc[posicao])


# ------------------------------------------------------- mercado de trabalho


def regra_de_sahm(desemprego: pd.DataFrame | None) -> pd.DataFrame:
    """Indicador de recessão de Claudia Sahm, em pontos percentuais.

    Média móvel de 3 meses da taxa de desemprego menos a mínima dessa mesma
    média nos **12 meses anteriores** — o mês corrente fica fora da janela, e
    por isso o indicador fica negativo quando o desemprego marca nova mínima,
    como na série SAHMREALTIME do FRED. Acima de 0,5 p.p., o sinal dispara.

    A série é posta numa grade mensal antes das médias, para que um mês sem
    divulgação conte como ausente em vez de encurtar a janela.
    """
    serie = serie_temporal(desemprego)
    if serie.empty:
        return para_quadro(serie)
    media3 = serie.resample("MS").mean().rolling(3, min_periods=2).mean()
    minima_anterior = media3.shift(1).rolling(12, min_periods=6).min()
    return para_quadro(media3 - minima_anterior)


# --------------------------------------------------------------------- PIB


def _largo(series: dict[str, pd.DataFrame | None], ids) -> pd.DataFrame | None:
    """Junta as séries pedidas por data; `None` se faltar alguma."""
    colunas = {}
    for serie_id in ids:
        serie = serie_temporal(series.get(serie_id))
        if serie.empty:
            return None
        colunas[serie_id] = serie
    return pd.concat(colunas, axis=1).dropna()


def _quadro_largo(largo: pd.DataFrame, colunas: dict[str, pd.Series]) -> pd.DataFrame:
    out = pd.DataFrame(colunas, index=largo.index)
    return out.rename_axis("data_ref").reset_index()


def contribuicoes_despesa(series: dict[str, pd.DataFrame | None]) -> pd.DataFrame:
    """Contribuição de cada componente da demanda ao crescimento do PIB.

    Em pontos percentuais da taxa trimestral anualizada. As importações
    entram com o sinal do BEA: importar mais **subtrai** do PIB, porque o
    gasto que vazou para fora já estava contado no consumo ou no investimento.
    """
    largo = _largo(series, CONTRIBUICOES_DESPESA)
    if largo is None or largo.empty:
        return pd.DataFrame()
    colunas = {nome: largo[serie_id] for serie_id, nome in CONTRIBUICOES_DESPESA.items()}
    out = _quadro_largo(largo, colunas)
    out["soma"] = out[list(CONTRIBUICOES_DESPESA.values())].sum(axis=1)
    return out


def contribuicoes_producao(series: dict[str, pd.DataFrame | None]) -> pd.DataFrame:
    """Agropecuária, indústria e serviços no recorte do IBGE, a partir do BEA.

    O BEA agrupa os setores de outro jeito: "bens" junta agro, extrativa,
    construção e transformação, e "serviços" inclui as utilities (eletricidade,
    gás e água), que o IBGE conta na indústria. Como as contribuições se somam,
    o remapeamento é exato:

    * agropecuária = agro;
    * indústria = bens − agro + utilities (extrativa, construção,
      transformação e utilities);
    * serviços = PIB − bens − utilities (serviços privados sem utilities, mais
      o governo, que o IBGE também conta em serviços).

    A única imprecisão é o arredondamento do FRED, em centésimos de ponto.
    """
    largo = _largo(series, CONTRIBUICOES_PRODUCAO)
    if largo is None or largo.empty:
        return pd.DataFrame()
    total, bens = largo["CPGDPAI"], largo["CPGDPGPI"]
    agro, utilities = largo["CPGDPAFH"], largo["CPGDPU"]
    return _quadro_largo(
        largo,
        {
            "agropecuaria": agro,
            "industria": bens - agro + utilities,
            "servicos": total - bens - utilities,
            "total": total,
        },
    )


def detalhe_industria(series: dict[str, pd.DataFrame | None]) -> pd.DataFrame:
    """A indústria aberta: transformação, construção, utilities e extrativa.

    A extrativa sai por diferença (bens − agro − construção − transformação),
    porque é o que fecha a conta com as séries que o FRED publica.
    """
    largo = _largo(series, CONTRIBUICOES_INDUSTRIA)
    if largo is None or largo.empty:
        return pd.DataFrame()
    transformacao = largo["CPGDPMD"] + largo["CPGDPMN"]
    return _quadro_largo(
        largo,
        {
            "transformacao": transformacao,
            "construcao": largo["CPGDPC"],
            "utilities": largo["CPGDPU"],
            "extrativa": largo["CPGDPGPI"] - largo["CPGDPAFH"] - largo["CPGDPC"] - transformacao,
        },
    )
