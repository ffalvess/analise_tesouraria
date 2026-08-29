"""Consultas de leitura sobre o banco.

Camada única entre o DuckDB e tudo que o consome — páginas da interface e
módulos de análise. Concentrar as consultas aqui evita que cada página invente
o seu próprio SQL e permite mudar o esquema em um lugar só.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from tesouraria.db import connection


def datas_disponiveis(tabela: str, fonte: str | None = None, tipo: str | None = None) -> list[dt.date]:
    """Datas com dados, da mais recente para a mais antiga."""
    where, params = [], []
    if fonte:
        where.append("fonte = ?")
        params.append(fonte)
    if tipo:
        where.append("tipo = ?")
        params.append(tipo)
    filtro = f"WHERE {' AND '.join(where)}" if where else ""

    with connection(read_only=True) as con:
        linhas = con.execute(
            f"SELECT DISTINCT data_ref FROM {tabela} {filtro} ORDER BY data_ref DESC", params
        ).fetchall()
    return [linha[0] for linha in linhas]


def curva_br(data_ref: dt.date, fonte: str = "tesouro", tipo: str = "pre") -> pd.DataFrame:
    with connection(read_only=True) as con:
        return con.execute(
            """
            SELECT data_ref, fonte, tipo, instrumento, vencimento,
                   prazo_du, prazo_anos, taxa, preco
            FROM curve_br
            WHERE data_ref = ? AND fonte = ? AND tipo = ?
            ORDER BY prazo_anos
            """,
            [data_ref, fonte, tipo],
        ).df()


def curva_us(data_ref: dt.date, tipo: str = "nominal") -> pd.DataFrame:
    with connection(read_only=True) as con:
        return con.execute(
            """
            SELECT data_ref, tipo, tenor, prazo_anos, taxa
            FROM curve_us
            WHERE data_ref = ? AND tipo = ?
            ORDER BY prazo_anos
            """,
            [data_ref, tipo],
        ).df()


def data_mais_proxima(tabela: str, alvo: dt.date, **filtros: str) -> dt.date | None:
    """Data com dados igual ou imediatamente anterior a `alvo`.

    Evita que a interface fique vazia quando o usuário escolhe um feriado ou um
    fim de semana.
    """
    where = ["data_ref <= ?"]
    params: list = [alvo]
    for coluna, valor in filtros.items():
        where.append(f"{coluna} = ?")
        params.append(valor)

    with connection(read_only=True) as con:
        linha = con.execute(
            f"SELECT MAX(data_ref) FROM {tabela} WHERE {' AND '.join(where)}", params
        ).fetchone()
    return linha[0] if linha and linha[0] else None


def fontes_curva_br() -> pd.DataFrame:
    """Quais combinações de fonte e tipo têm dados, e desde quando."""
    with connection(read_only=True) as con:
        return con.execute(
            """
            SELECT fonte, tipo, COUNT(*) AS linhas,
                   MIN(data_ref) AS data_min, MAX(data_ref) AS data_max
            FROM curve_br
            GROUP BY fonte, tipo
            ORDER BY fonte, tipo
            """
        ).df()


def serie(serie_id: str | list[str], desde: dt.date | None = None) -> pd.DataFrame:
    ids = [serie_id] if isinstance(serie_id, str) else list(serie_id)
    if not ids:
        return pd.DataFrame(columns=["serie_id", "nome", "data_ref", "valor"])

    marcadores = ", ".join("?" for _ in ids)
    filtro_data = "AND data_ref >= ?" if desde else ""
    params = [*ids, desde] if desde else list(ids)

    with connection(read_only=True) as con:
        return con.execute(
            f"""
            SELECT pais, fonte, serie_id, nome, unidade, data_ref, valor
            FROM series_macro
            WHERE serie_id IN ({marcadores}) {filtro_data}
            ORDER BY serie_id, data_ref
            """,
            params,
        ).df()


def catalogo_series() -> pd.DataFrame:
    with connection(read_only=True) as con:
        return con.execute(
            """
            SELECT pais, fonte, serie_id, ANY_VALUE(nome) AS nome,
                   ANY_VALUE(unidade) AS unidade, COUNT(*) AS pontos,
                   MIN(data_ref) AS data_min, MAX(data_ref) AS data_max
            FROM series_macro
            GROUP BY pais, fonte, serie_id
            ORDER BY pais, fonte, serie_id
            """
        ).df()


def fluxo_cambial(desde: dt.date | None = None) -> pd.DataFrame:
    filtro = "WHERE data_ref >= ?" if desde else ""
    params = [desde] if desde else []
    with connection(read_only=True) as con:
        return con.execute(
            f"""
            SELECT data_ref, periodicidade, segmento, compras, vendas, saldo
            FROM fx_flow {filtro}
            ORDER BY data_ref, segmento
            """,
            params,
        ).df()


def focus(
    indicador: str | None = None, tipo: str = "geral", desde: dt.date | None = None
) -> pd.DataFrame:
    where, params = ["tipo = ?"], [tipo]
    if indicador:
        where.append("indicador = ?")
        params.append(indicador)
    if desde:
        where.append("data_coleta >= ?")
        params.append(desde)

    with connection(read_only=True) as con:
        return con.execute(
            f"""
            SELECT data_coleta, tipo, indicador, data_referencia,
                   mediana, media, desvio, minimo, maximo, n_respondentes
            FROM focus
            WHERE {' AND '.join(where)}
            ORDER BY data_coleta, data_referencia
            """,
            params,
        ).df()


def documentos(
    instituicao: str | None = None,
    desde: dt.date | None = None,
    busca: str | None = None,
    limite: int = 500,
) -> pd.DataFrame:
    where, params = ["1=1"], []
    if instituicao:
        where.append("instituicao = ?")
        params.append(instituicao)
    if desde:
        where.append("data_pub >= ?")
        params.append(desde)
    if busca:
        where.append("(lower(titulo) LIKE ? OR lower(texto) LIKE ?)")
        alvo = f"%{busca.lower()}%"
        params.extend([alvo, alvo])
    params.append(limite)

    with connection(read_only=True) as con:
        return con.execute(
            f"""
            SELECT id, fonte, instituicao, autor, titulo, data_pub, url, tipo,
                   idioma, score_tom, n_hawk, n_dove,
                   substr(texto, 1, 1200) AS trecho, length(texto) AS tamanho
            FROM documentos
            WHERE {' AND '.join(where)}
            ORDER BY data_pub DESC NULLS LAST
            LIMIT ?
            """,
            params,
        ).df()


def instituicoes() -> list[str]:
    with connection(read_only=True) as con:
        linhas = con.execute(
            "SELECT DISTINCT instituicao FROM documentos "
            "WHERE instituicao IS NOT NULL ORDER BY instituicao"
        ).fetchall()
    return [linha[0] for linha in linhas]


def historico_curva(
    tabela: str, prazo_alvo: float, tolerancia: float = 0.35, **filtros: str
) -> pd.DataFrame:
    """Série histórica da taxa perto de um prazo — por exemplo, o vértice de 10 anos.

    Toma a mediana dos vértices dentro da tolerância em vez de exigir o prazo
    exato: títulos envelhecem e nenhum fica parado em 10,0 anos.
    """
    where = ["abs(prazo_anos - ?) <= ?"]
    params: list = [prazo_alvo, tolerancia]
    for coluna, valor in filtros.items():
        where.append(f"{coluna} = ?")
        params.append(valor)

    with connection(read_only=True) as con:
        return con.execute(
            f"""
            SELECT data_ref, median(taxa) AS taxa
            FROM {tabela}
            WHERE {' AND '.join(where)}
            GROUP BY data_ref
            ORDER BY data_ref
            """,
            params,
        ).df()
