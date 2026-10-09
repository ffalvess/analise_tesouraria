"""Paleta, tema e construtores de gráfico.

Concentrar isto num módulo é o que faz as onze páginas lerem como um único
sistema: mesmas cores para os mesmos significados (Brasil sempre azul, EUA
sempre laranja), mesma altura, mesma grade, mesmo comportamento de hover.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Cores com papel fixo em todo o aplicativo.
BR = "#2F6FED"
US = "#E4572E"
VERDE = "#2E9E6B"
ROXO = "#7C5CD6"
AMBAR = "#D99A00"
CINZA = "#7A8798"
VERMELHO = "#D64545"

SEQUENCIA = [BR, US, VERDE, ROXO, AMBAR, CINZA, VERMELHO]

ALTURA_PADRAO = 420


def _escuro() -> bool:
    try:
        return str(st.get_option("theme.base") or "light").lower() == "dark"
    except Exception:  # noqa: BLE001 — fora do runtime do Streamlit
        return False


def tinta() -> str:
    """Cor do texto do tema. Desenha também a linha de total, que não é uma série."""
    return "#E6EAF2" if _escuro() else "#1F2733"


def superficie() -> str:
    """Fundo da página — a cor do filete que separa segmentos empilhados."""
    return "#0E1117" if _escuro() else "#FFFFFF"


def aplicar_tema(fig: go.Figure, altura: int = ALTURA_PADRAO, titulo: str = "") -> go.Figure:
    escuro = _escuro()
    grade = "rgba(255,255,255,0.10)" if escuro else "rgba(0,0,0,0.08)"
    texto = tinta()

    # A legenda fica logo acima da área do gráfico; o título, no topo da figura.
    # Com os dois, a margem de cima abre espaço para ambos — antes o título caía
    # em cima dos primeiros itens da legenda.
    com_legenda = sum(1 for trace in fig.data if trace.showlegend is not False) > 1
    fig.update_layout(
        template="plotly_dark" if escuro else "plotly_white",
        height=altura,
        title=(
            {
                "text": titulo,
                "x": 0,
                "xanchor": "left",
                "y": 1,
                "yanchor": "top",
                "yref": "container",
                "pad": {"t": 10},
            }
            if titulo
            else None
        ),
        margin={"l": 10, "r": 10, "t": (85 if com_legenda else 50) if titulo else 30, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": texto, "size": 13},
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "title": None,
        },
    )
    fig.update_xaxes(gridcolor=grade, zeroline=False)
    fig.update_yaxes(gridcolor=grade, zeroline=False)
    return fig


def degrade(n: int, cor_base: str = BR) -> list[str]:
    """Tons de uma cor, do mais claro ao mais escuro.

    Usado na sobreposição de datas: a curva mais antiga sai clara e a mais
    recente, saturada, de modo que a ordem cronológica se lê sem consultar a
    legenda.
    """
    if n <= 1:
        return [cor_base]

    base = cor_base.lstrip("#")
    canais = [int(base[i : i + 2], 16) for i in (0, 2, 4)]

    cores = []
    for k in range(n):
        # peso 0 = mistura total com branco; peso 1 = cor cheia.
        peso = 0.30 + 0.70 * (k / (n - 1))
        r, g, b = (int(canal * peso + 255 * (1 - peso)) for canal in canais)
        cores.append(f"rgb({r}, {g}, {b})")
    return cores


def formato_data(datas) -> str:
    """Formato da data no cabeçalho do hover, conforme a frequência da série.

    Sem isso o plotly escreve "Apr 1, 2026" para o PIB do segundo trimestre —
    em inglês e com um dia que o dado não tem.
    """
    unicas = pd.Series(pd.to_datetime(pd.Series(datas)).dropna().unique()).sort_values()
    dias = unicas.diff().dt.days.median() if len(unicas) > 1 else 1
    if dias > 80:
        return "%Y T%q"
    if dias > 20:
        return "%m/%Y"
    return "%d/%m/%Y"


def coluna(dados: pd.DataFrame, nome: str) -> pd.Series:
    """Extrai uma coluna tolerando nomes duplicados.

    Renomear uma coluna para `valor` num quadro que já tem `valor` produz duas
    colunas homônimas, e o plotly falha com um erro obscuro lá no fundo. Aqui a
    última vence, que é a intenção de quem renomeou.
    """
    obtido = dados[nome]
    if isinstance(obtido, pd.DataFrame):
        return obtido.iloc[:, -1]
    return obtido


def arredondar(dados: pd.DataFrame, casas: int = 2) -> pd.DataFrame:
    """Arredonda apenas as colunas numéricas.

    `DataFrame.round` avisa e não faz nada em colunas de data; restringir às
    numéricas mantém a saída limpa.
    """
    out = dados.copy()
    numericas = out.select_dtypes(include="number").columns
    out[numericas] = out[numericas].round(casas)
    return out


def grafico_curva(
    curvas: list[tuple[str, pd.DataFrame]],
    titulo: str = "",
    cores: list[str] | None = None,
    eixo_y: str = "Taxa (% a.a.)",
    log_x: bool = True,
) -> go.Figure:
    """Sobrepõe uma ou mais curvas (prazo em anos no eixo X).

    O eixo X é logarítmico por padrão: a curva tem vértices muito juntos no
    curto prazo e muito espaçados no longo, e a escala linear esmaga
    exatamente a parte onde a política monetária age.
    """
    fig = go.Figure()
    cores = cores or SEQUENCIA

    for indice, (rotulo, dados) in enumerate(curvas):
        if dados is None or dados.empty:
            continue
        limpo = dados.assign(taxa=coluna(dados, "taxa")).dropna(subset=["taxa"])
        fig.add_trace(
            go.Scatter(
                x=limpo["prazo_anos"],
                y=limpo["taxa"],
                name=rotulo,
                mode="lines+markers",
                line={"width": 2.4, "color": cores[indice % len(cores)]},
                marker={"size": 6},
                hovertemplate="%{y:.2f}%<extra>" + rotulo + "</extra>",
            )
        )

    fig.update_xaxes(
        title="Prazo (anos)",
        type="log" if log_x else "linear",
        tickvals=[0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30],
        ticktext=["3m", "6m", "1a", "2a", "3a", "5a", "7a", "10a", "15a", "20a", "30a"],
    )
    fig.update_yaxes(title=eixo_y, ticksuffix="%")
    return aplicar_tema(fig, titulo=titulo)


def grafico_series(
    series: list[tuple[str, pd.DataFrame]],
    coluna_valor: str = "valor",
    coluna_data: str = "data_ref",
    titulo: str = "",
    eixo_y: str = "",
    sufixo: str = "",
    cores: list[str] | None = None,
    formato: str = ".2f",
) -> go.Figure:
    """Linhas temporais empilhadas no mesmo eixo."""
    fig = go.Figure()
    cores = cores or SEQUENCIA

    for indice, (rotulo, dados) in enumerate(series):
        if dados is None or dados.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(coluna(dados, coluna_data)),
                y=coluna(dados, coluna_valor),
                name=rotulo,
                mode="lines",
                line={"width": 2, "color": cores[indice % len(cores)]},
                hovertemplate="%{y:" + formato + "}" + sufixo + "<extra>" + rotulo + "</extra>",
            )
        )

    datas = [
        pd.to_datetime(coluna(d, coluna_data)) for _, d in series if d is not None and not d.empty
    ]
    if datas:
        fig.update_xaxes(hoverformat=formato_data(pd.concat(datas)))
    fig.update_yaxes(title=eixo_y, ticksuffix=sufixo)
    return aplicar_tema(fig, titulo=titulo)


def grafico_barras_linha(
    dados: pd.DataFrame,
    coluna_data: str,
    coluna_barra: str,
    coluna_linha: str,
    rotulo_barra: str,
    rotulo_linha: str,
    titulo: str = "",
    sufixo_linha: str = "",
) -> go.Figure:
    """Barras num eixo e linha no outro — fluxo contra cotação.

    As barras são coloridas pelo sinal: entrada líquida em verde, saída em
    vermelho, de modo que o padrão salta antes de o leitor olhar a escala.
    """
    fig = go.Figure()
    valores = pd.to_numeric(coluna(dados, coluna_barra), errors="coerce")

    fig.add_trace(
        go.Bar(
            x=pd.to_datetime(dados[coluna_data]),
            y=valores,
            name=rotulo_barra,
            marker_color=[VERDE if v >= 0 else VERMELHO for v in valores.fillna(0)],
            opacity=0.75,
            hovertemplate="%{y:,.0f}<extra>" + rotulo_barra + "</extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=pd.to_datetime(dados[coluna_data]),
            y=coluna(dados, coluna_linha),
            name=rotulo_linha,
            mode="lines",
            yaxis="y2",
            line={"width": 2.4, "color": BR},
            hovertemplate="%{y:.3f}" + sufixo_linha + "<extra>" + rotulo_linha + "</extra>",
        )
    )

    fig.update_layout(
        yaxis={"title": rotulo_barra},
        yaxis2={
            "title": rotulo_linha,
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
        barmode="relative",
    )
    return aplicar_tema(fig, titulo=titulo)


def linha_referencia(fig: go.Figure, y: float, texto: str) -> go.Figure:
    """Linha horizontal de referência — uma meta, um limiar —, rotulada no gráfico.

    Pontilhada porque é um patamar, não um dado; o rótulo fica na própria linha
    para ninguém precisar de legenda para saber o que ela marca.
    """
    fig.add_hline(
        y=y,
        line_dash="dot",
        line_width=1.2,
        line_color=CINZA,
        annotation_text=texto,
        annotation_position="top left",
        annotation_font={"size": 12, "color": CINZA},
    )
    return fig


def grafico_contribuicoes(
    quadro: pd.DataFrame,
    componentes: list[tuple[str, str, str]],
    total: tuple[str, str] | None = None,
    titulo: str = "",
    eixo_y: str = "p.p. da taxa anualizada",
) -> go.Figure:
    """Contribuições empilhadas por período, com a linha do total por cima.

    `componentes` traz (coluna, rótulo, cor) na ordem do empilhamento. O
    empilhamento é relativo — as contribuições negativas descem do zero e as
    positivas sobem —, de modo que a altura líquida das barras é o total, e a
    linha confirma a conta. Tudo em pontos percentuais, num eixo só. Um filete
    na cor do fundo separa os segmentos, para vizinhos de cor parecida não se
    fundirem.
    """
    fig = go.Figure()
    if quadro is None or quadro.empty:
        return aplicar_tema(fig, titulo=titulo)

    x = pd.to_datetime(quadro["data_ref"])
    for nome_coluna, rotulo, cor in componentes:
        fig.add_trace(
            go.Bar(
                x=x,
                y=quadro[nome_coluna],
                name=rotulo,
                marker={"color": cor, "line": {"color": superficie(), "width": 1}},
                hovertemplate="%{y:.2f} p.p.<extra>" + rotulo + "</extra>",
            )
        )
    if total is not None:
        coluna_total, rotulo_total = total
        fig.add_trace(
            go.Scatter(
                x=x,
                y=quadro[coluna_total],
                name=rotulo_total,
                mode="lines+markers",
                line={"color": tinta(), "width": 2},
                marker={"size": 8, "color": tinta(), "line": {"color": superficie(), "width": 2}},
                hovertemplate="%{y:.2f}%<extra>" + rotulo_total + "</extra>",
            )
        )

    fig.add_hline(y=0, line_width=1, line_color=CINZA, opacity=0.6)
    fig.update_layout(barmode="relative", bargap=0.3)
    fig.update_xaxes(hoverformat=formato_data(x))
    fig.update_yaxes(title=eixo_y)
    return aplicar_tema(fig, titulo=titulo)


def grafico_barras_media(
    barras: pd.DataFrame,
    media: pd.DataFrame,
    rotulo_barras: str,
    rotulo_media: str,
    titulo: str = "",
    eixo_y: str = "",
    formato: str = ",.0f",
) -> go.Figure:
    """O dado bruto em barras cinzas e a média móvel em destaque, na mesma escala.

    O mês a mês é ruidoso: fica em cinza, como contexto. A média é o sinal e
    leva a cor — é ela que o olho deve seguir.
    """
    fig = go.Figure()
    if barras is not None and not barras.empty:
        fig.add_trace(
            go.Bar(
                x=pd.to_datetime(barras["data_ref"]),
                y=barras["valor"],
                name=rotulo_barras,
                marker={"color": CINZA, "opacity": 0.45},
                hovertemplate="%{y:" + formato + "}<extra>" + rotulo_barras + "</extra>",
            )
        )
    if media is not None and not media.empty:
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(media["data_ref"]),
                y=media["valor"],
                name=rotulo_media,
                mode="lines",
                line={"color": US, "width": 2.4},
                hovertemplate="%{y:" + formato + "}<extra>" + rotulo_media + "</extra>",
            )
        )
    fig.add_hline(y=0, line_width=1, line_color=CINZA, opacity=0.6)
    if barras is not None and not barras.empty:
        fig.update_xaxes(hoverformat=formato_data(barras["data_ref"]))
    fig.update_yaxes(title=eixo_y)
    return aplicar_tema(fig, titulo=titulo)


def grafico_dispersao_tom(documentos: pd.DataFrame, titulo: str = "") -> go.Figure:
    """Tom de cada documento no tempo, colorido por instituição."""
    fig = go.Figure()
    if documentos.empty:
        return aplicar_tema(fig, titulo=titulo)

    cores = {"BCB": BR, "Fed": US}
    for indice, (instituicao, grupo) in enumerate(documentos.groupby("instituicao")):
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(grupo["data_pub"]),
                y=grupo["score_tom"],
                name=str(instituicao),
                mode="markers",
                marker={
                    "size": 9,
                    "color": cores.get(str(instituicao), SEQUENCIA[indice % len(SEQUENCIA)]),
                    "opacity": 0.75,
                },
                text=grupo["titulo"],
                hovertemplate="%{text}<br>tom: %{y:.2f}<extra></extra>",
            )
        )

    fig.add_hline(y=0, line_dash="dot", line_color=CINZA, opacity=0.6)
    fig.update_yaxes(title="← dovish   ·   hawkish →", range=[-1.05, 1.05])
    fig.update_layout(hovermode="closest")
    return aplicar_tema(fig, titulo=titulo)


def heatmap_variacao(matriz: pd.DataFrame, titulo: str = "") -> go.Figure:
    """Mapa de calor de variações em bps: linhas = datas, colunas = vértices."""
    fig = go.Figure(
        go.Heatmap(
            z=matriz.to_numpy(),
            x=[f"{c:g}a" for c in matriz.columns],
            y=[str(i) for i in matriz.index],
            colorscale="RdBu_r",
            zmid=0,
            colorbar={"title": "bps"},
            hovertemplate="%{y} · %{x}: %{z:.0f} bps<extra></extra>",
        )
    )
    return aplicar_tema(fig, altura=max(260, 40 * len(matriz)), titulo=titulo)


def formatar_bps(valor: float) -> str:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return "—"
    return f"{valor:+,.0f} bps"


def formatar_pct(valor: float, casas: int = 2) -> str:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return "—"
    return f"{valor:.{casas}f}%"
