"""Blocos de interface da aba "Câmbio USD/BRL".

A página (`pages/10_Cambio_USDBRL.py`) só organiza as abas; aqui ficam a carga
dos dados (com `st.cache_data`), os cards e os gráficos. As contas vivem em
`analytics/` — `atribuicao`, `commodities` e `painel_cambio` — e são testadas
sem o Streamlit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tesouraria import agenda, calendario, db, queries, sources
from tesouraria.analytics import atribuicao as atrib
from tesouraria.analytics import commodities as cmd_mod
from tesouraria.analytics import painel_cambio as painel
from tesouraria.settings import get_settings, load_config
from tesouraria.ui import charts, common

TTL = common.TTL
ANOS_HISTORICO = 4

ROTULO_FONTE = {
    "yahoo": "Yahoo Finance (não oficial)",
    "sgs": "BCB/SGS",
    "fred": "FRED",
    "curve_us": "US Treasury",
    "curve_br": "curva BR do app",
    "derivado": "calculado no app",
}
FONTE_INGESTAO = {"yahoo": "yahoo", "sgs": "bcb_sgs", "fred": "us_macro", "curve_us": "us_treasury"}
ROTULO_CURVA = {"b3": "B3 (DI1)", "anbima": "ANBIMA ETTJ pré", "tesouro": "Tesouro Direto (prefixados)"}

AVISO_ESCOPO = (
    "Leitura retrospectiva — atribuição e contexto do que já aconteceu. Não é "
    "previsão, sinal de compra ou venda, nem recomendação de hedge."
)


def cfg() -> dict:
    return load_config("cambio")


def hoje_sp() -> dt.date:
    return dt.datetime.now(ZoneInfo(cfg().get("fuso", "America/Sao_Paulo"))).date()


# ------------------------------------------------------------------ carga


def _ids_series() -> list[str]:
    c = cfg()
    ids = {s["serie_id"] for s in c["series"].values() if "serie_id" in s}
    ids |= {s["reserva"]["serie_id"] for s in c["series"].values() if "reserva" in s}
    for produto in c["commodities"]["produtos"].values():
        if produto.get("ticker"):
            ids.add(produto["ticker"])
        if produto.get("reserva_mensal"):
            ids.add(produto["reserva_mensal"])
    return sorted(ids)


@st.cache_data(ttl=TTL, show_spinner=False)
def carregar_niveis() -> pd.DataFrame:
    """Uma coluna por série (Yahoo, SGS, FRED), índice de datas."""
    desde = dt.date.today() - dt.timedelta(days=365 * ANOS_HISTORICO)
    longo = queries.serie(_ids_series(), desde)
    return painel.largo(longo)


@st.cache_data(ttl=TTL, show_spinner=False)
def carregar_treasury(prazo: float) -> pd.Series:
    hist = queries.historico_curva("curve_us", prazo, 0.01, tipo="nominal")
    if hist.empty:
        return pd.Series(dtype=float)
    return pd.Series(hist["taxa"].to_numpy(), index=pd.to_datetime(hist["data_ref"]))


@st.cache_data(ttl=TTL, show_spinner=False)
def carregar_curva_br() -> tuple[str | None, pd.DataFrame]:
    """Fonte escolhida e série de prazo constante (curto, longo) da curva pré."""
    c = cfg()["curva_br"]
    fonte = painel.escolher_fonte_curva(
        queries.contagem_datas_curva_br(c["tipo"]), c["preferencia"], c["minimo_sessoes"]
    )
    if fonte is None:
        return None, pd.DataFrame()
    desde = dt.date.today() - dt.timedelta(days=365 * 3)
    vertices = queries.curva_br_periodo(fonte, c["tipo"], desde)
    serie = painel.serie_prazo_constante(
        vertices, [c["prazo_curto"], c["prazo_longo"]], c.get("metodo", "pchip")
    )
    serie.columns = ["curto", "longo"]
    serie["inclinacao"] = serie["longo"] - serie["curto"]
    return fonte, serie


@st.cache_data(ttl=TTL, show_spinner=False)
def carregar_pesos() -> cmd_mod.PesosCesta:
    c = cfg()["commodities"]
    produtos = c["produtos"]
    ativos = [nome for nome, p in produtos.items() if p.get("ativo")]
    ids = [cmd_mod.serie_id_sh4(s) for p in produtos.values() for s in p.get("sh4", [])]
    mensal = queries.serie(ids)
    return cmd_mod.calcular_pesos(mensal, produtos, ativos, c.get("meses_pesos", 12))


@st.cache_data(ttl=TTL, show_spinner=False)
def erros_por_fonte() -> dict[str, str]:
    """Último erro registrado de cada fonte que não terminou em `ok`."""
    try:
        status = db.status_report()
    except Exception:  # noqa: BLE001 — banco sem registro
        return {}
    saida = {}
    for linha in status.itertuples():
        if linha.status != "ok":
            # `erro` vem NaN (float) do DuckDB quando a fonte só foi pulada ou veio vazia.
            motivo = linha.erro if isinstance(linha.erro, str) and linha.erro else str(linha.status)
            motivo = motivo.strip()
            saida[linha.fonte] = (motivo[:160] + "…") if len(motivo) > 160 else motivo
    return saida


@dataclass
class Modelo:
    fatores: painel.Fatores
    atribuicao: pd.DataFrame
    flags: pd.DataFrame
    contexto: pd.DataFrame
    pesos: cmd_mod.PesosCesta
    ticker_produto: dict[str, str]


@st.cache_data(ttl=TTL, show_spinner="Calculando a atribuição…")
def calcular_modelo(janela: int) -> Modelo:
    c = cfg()
    niveis = carregar_niveis()
    pesos = carregar_pesos()
    produtos = c["commodities"]["produtos"]

    ticker_produto = {p["ticker"]: nome for nome, p in produtos.items() if p.get("ticker")}
    produto_ticker = {nome: t for t, nome in ticker_produto.items()}
    pesos_ticker = pd.Series(
        {produto_ticker[n]: w for n, w in pesos.pesos.items() if n in produto_ticker}, dtype=float
    )

    series = c["series"]
    fatores = painel.montar_fatores(
        niveis,
        usdbrl=series["usdbrl"]["serie_id"],
        dxy=series["dxy"]["serie_id"],
        em=[series[k]["serie_id"] for k in c["cesta_em"]["componentes"]],
        minimo_em=c["cesta_em"]["minimo_componentes"],
        cmd_pesos=pesos_ticker,
        cobertura_minima=c["commodities"]["cobertura_minima"],
        minimo_sessoes_componente=c["commodities"]["minimo_sessoes_componente"],
    )
    # Produtos ativos sem ticker diário (ex.: celulose) também ficam de fora.
    for nome in pesos.pesos.index:
        if nome not in produto_ticker:
            fatores.excluidos[nome] = "sem futuro diário configurado"

    historico = c["atribuicao"]["historico_sessoes"]
    base = fatores.base.tail(historico + janela)
    resultado = atrib.atribuir(base, janela) if len(base) > janela else pd.DataFrame()

    contexto = _contexto(niveis, fatores)
    marcas = (
        atrib.flags(resultado, contexto, c.get("flags"))
        if not resultado.empty
        else pd.DataFrame()
    )
    return Modelo(fatores, resultado, marcas, contexto, pesos, ticker_produto)


def _serie_vix(niveis: pd.DataFrame) -> tuple[pd.Series, str]:
    s = cfg()["series"]["vix"]
    principal = niveis.get(s["serie_id"], pd.Series(dtype=float)).dropna()
    reserva = niveis.get(s["reserva"]["serie_id"], pd.Series(dtype=float)).dropna()
    if not reserva.empty and (principal.empty or reserva.index.max() > principal.index.max()):
        return reserva, s["reserva"]["origem"]
    return principal, s["origem"]


def _contexto(niveis: pd.DataFrame, fatores: painel.Fatores) -> pd.DataFrame:
    _, di = carregar_curva_br()
    vix, _ = _serie_vix(niveis)
    indice = fatores.base.index
    di_bps = (di["longo"].dropna().diff() * 100) if not di.empty else pd.Series(dtype=float)
    vix_pct = vix.pct_change() * 100 if not vix.empty else pd.Series(dtype=float)
    return pd.DataFrame(
        {
            "di_longo_bps": di_bps.reindex(indice),
            "vix_pct": vix_pct.reindex(indice),
            "dxy": fatores.base["dxy"],
            "em": fatores.base["em"],
        },
        index=indice,
    )


# -------------------------------------------------------------- atualizar


def botao_atualizar() -> None:
    """Coleta de novo as fontes diárias da aba e limpa o cache da interface."""
    if get_settings().offline:
        st.sidebar.caption("Modo offline: o botão de atualizar fica desativado.")
        return
    conf = cfg().get("atualizacao", {})
    if not st.sidebar.button("🔄 Atualizar dados", help="Coleta de novo: " + ", ".join(conf["fontes"])):
        return
    desde = dt.date.today() - dt.timedelta(days=int(conf.get("dias", 10)))
    resumo = []
    with st.sidebar.status("Atualizando…", expanded=False) as caixa:
        with db.connection() as con:
            for nome in conf["fontes"]:
                resultado = sources.criar(nome).run(con, since=desde)
                resumo.append(f"{nome}: {resultado.status}")
        caixa.update(label="Atualização concluída", state="complete")
    st.cache_data.clear()
    st.sidebar.caption(" · ".join(resumo))


# ------------------------------------------------------------------ cards


def _formatar(valor: float, casas: int) -> str:
    if not np.isfinite(valor):
        return "—"
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _var(valor: float, unidade: str) -> str:
    if not np.isfinite(valor):
        return "—"
    if unidade == "bps":
        return f"{valor:+.1f} bps".replace(".", ",")
    return f"{valor:+.2f}%".replace(".", ",")


def card(
    rotulo: str,
    serie: pd.Series,
    *,
    tipo: str,
    casas: int,
    unidade: str,
    fonte: str,
    horario: str,
    tolerancia_du: int,
    erro: str | None = None,
    nota: str | None = None,
) -> None:
    """Card com nível, Δ dia, Δ 5 du, Δ 21 du, fonte, data e horário."""
    with st.container(border=True):
        if serie is None or serie.dropna().empty:
            st.markdown(f"**{rotulo}**")
            st.markdown("🚫 **indisponível**")
            st.caption(f"{fonte}" + (f" — {erro}" if erro else " — sem dados no banco"))
            return
        r = painel.resumo_serie(serie, tipo, hoje_sp(), tolerancia_du)
        selo = f" · ⚠️ defasado ({r.atraso_du} du)" if r.defasado else ""
        st.metric(
            rotulo,
            # `$` abre LaTeX no markdown do Streamlit ("R$/US$" virava fórmula).
            f"{_formatar(r.nivel, casas)} {unidade}".strip().replace("$", "\\$"),
            _var(r.var_1d, r.unidade_var) + " no dia",
            delta_color="off",
        )
        st.caption(
            f"5 du: **{_var(r.var_5du, r.unidade_var)}** · 21 du: **{_var(r.var_21du, r.unidade_var)}**"
        )
        st.caption(f"{fonte} · {r.data:%d/%m/%Y} · {horario}{selo}")
        if nota:
            st.caption(nota)


def card_lacuna(rotulo: str, motivo: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{rotulo}**")
        st.markdown("🚫 **indisponível**")
        st.caption(motivo)


def card_config(chave: str, niveis: pd.DataFrame, erros: dict[str, str]) -> None:
    s = cfg()["series"][chave]
    origem = s["origem"]
    nota = None
    if origem == "curve_us":
        serie = carregar_treasury(float(s["prazo"]))
    elif chave == "vix":
        serie, origem = _serie_vix(niveis)
        if origem != s["origem"]:
            nota = "^VIX do Yahoo indisponível ou atrasado: usando VIXCLS (FRED), defasado em um dia."
    else:
        serie = niveis.get(s["serie_id"], pd.Series(dtype=float))
    card(
        s["rotulo"],
        serie,
        tipo=s["variacao"],
        casas=s.get("casas", 2),
        unidade=s.get("unidade", ""),
        fonte=ROTULO_FONTE.get(origem, origem),
        horario=s.get("horario", ""),
        tolerancia_du=int(s.get("tolerancia_du", 1)),
        erro=erros.get(FONTE_INGESTAO.get(origem, "")),
        nota=nota,
    )


def indice_base_100(retornos: pd.Series) -> pd.Series:
    limpa = retornos.dropna()
    if limpa.empty:
        return limpa
    return 100 * np.exp(limpa.cumsum())


# ------------------------------------------------------------- seções


def secao_painel(modelo: Modelo) -> None:
    c = cfg()
    niveis = carregar_niveis()
    erros = erros_por_fonte()
    st.caption(
        "Variação do dia contra a observação anterior; 5 e 21 dias úteis pelo "
        "calendário brasileiro (feriados nacionais). Selo ⚠️ quando a última "
        "observação é mais antiga que o esperado."
    )

    st.markdown("##### Câmbio e dólar global")
    colunas = st.columns(4)
    for coluna, chave in zip(colunas, ["usdbrl", "ptax", "dxy", "vix"], strict=True):
        with coluna:
            card_config(chave, niveis, erros)

    st.markdown("##### Juros")
    colunas = st.columns(4)
    with colunas[0]:
        card_config("ust2", niveis, erros)
    with colunas[1]:
        card_config("ust10", niveis, erros)
    fonte_br, di = carregar_curva_br()
    conf_br = c["curva_br"]
    rotulo_br = ROTULO_CURVA.get(fonte_br or "", "curva BR")
    for coluna, (campo, rotulo) in zip(
        colunas[2:],
        [("longo", f"DI longo (~{conf_br['prazo_longo']:.0f} anos)"),
         ("inclinacao", f"Inclinação {conf_br['prazo_longo']:.0f}a − {conf_br['prazo_curto']:.0f}a")],
        strict=True,
    ):
        with coluna:
            card(
                rotulo,
                di[campo] if not di.empty else pd.Series(dtype=float),
                tipo="bps",
                casas=2,
                unidade="% a.a." if campo == "longo" else "p.p.",
                fonte=f"curva do app — {rotulo_br}",
                horario="interpolação pchip em prazo constante",
                tolerancia_du=int(conf_br.get("tolerancia_du", 2)),
                erro="nenhuma curva pré no banco" if fonte_br is None else None,
            )
    if fonte_br == "tesouro":
        st.caption(
            "Sem histórico suficiente de DI1 (B3) ou ANBIMA no banco: o 'DI longo' usa a curva "
            "dos prefixados do Tesouro Direto, que é taxa de título, não de DI."
        )

    st.markdown("##### Pares emergentes (moeda local por US$)")
    colunas = st.columns(5)
    for coluna, chave in zip(colunas[:4], c["cesta_em"]["componentes"], strict=True):
        with coluna:
            card_config(chave, niveis, erros)
    with colunas[4]:
        card(
            "Cesta EM (média)",
            indice_base_100(modelo.fatores.base.get("em", pd.Series(dtype=float))),
            tipo="pct",
            casas=2,
            unidade="",
            fonte=ROTULO_FONTE["derivado"],
            horario="média simples dos retornos log de MXN, CLP, COP e ZAR",
            tolerancia_du=1,
            erro=erros.get("yahoo"),
            nota="Índice base 100 no início do histórico.",
        )

    st.markdown("##### Cesta de commodities Brasil")
    _cards_commodities(modelo, niveis, erros)

    st.markdown("##### Risco e intervenção")
    colunas = st.columns(4)
    for coluna, lacuna in zip(colunas, c.get("lacunas", {}).values(), strict=False):
        with coluna:
            card_lacuna(lacuna["rotulo"], lacuna["motivo"])


def _cards_commodities(modelo: Modelo, niveis: pd.DataFrame, erros: dict[str, str]) -> None:
    c = cfg()["commodities"]
    produtos = c["produtos"]
    ativos = [n for n, p in produtos.items() if p.get("ativo")]
    tolerancia = int(c.get("tolerancia_du", 2))
    itens = ["__indice__", *ativos]
    for inicio in range(0, len(itens), 4):
        colunas = st.columns(4)
        for coluna, item in zip(colunas, itens[inicio : inicio + 4], strict=False):
            with coluna:
                if item == "__indice__":
                    card(
                        "Índice commodities Brasil",
                        indice_base_100(modelo.fatores.base.get("cmd", pd.Series(dtype=float))),
                        tipo="pct",
                        casas=2,
                        unidade="",
                        fonte=ROTULO_FONTE["derivado"],
                        horario="média ponderada dos retornos log (pesos Comex Stat)",
                        tolerancia_du=tolerancia,
                        erro=erros.get("yahoo"),
                        nota="Índice base 100 no início do histórico.",
                    )
                    continue
                p = produtos[item]
                ticker = p.get("ticker")
                serie = niveis.get(ticker, pd.Series(dtype=float)) if ticker else pd.Series(dtype=float)
                peso = modelo.pesos.pesos.get(item, np.nan)
                nota = f"Peso na cesta: {peso:.1%}" if np.isfinite(peso) else None
                motivo = modelo.fatores.excluidos.get(ticker or item)
                if motivo:
                    nota = f"⚠️ fora do índice diário: {motivo}."
                card(
                    p["rotulo"],
                    serie,
                    tipo="pct",
                    casas=2,
                    unidade="",
                    fonte=f"{ROTULO_FONTE['yahoo']} · {ticker}" if ticker else "sem ticker",
                    horario="fechamento do pregão",
                    tolerancia_du=tolerancia,
                    erro=erros.get("yahoo") if ticker else "sem futuro diário configurado",
                    nota=nota,
                )


def secao_commodities(modelo: Modelo) -> None:
    c = cfg()["commodities"]
    produtos = c["produtos"]
    pesos = modelo.pesos
    niveis = carregar_niveis()

    esquerda, direita = st.columns([2, 3])
    with esquerda:
        st.markdown("##### Pesos")
        if pesos.origem == "comex":
            st.caption(
                f"Participação no valor exportado (FOB, US$) de "
                f"{pesos.inicio:%m/%Y} a {pesos.fim:%m/%Y}, Comex Stat/MDIC, "
                "renormalizada para somar 1. Recalculada a cada coleta."
            )
        else:
            st.warning(pesos.aviso or "Pesos iguais.", icon="⚠️")
        tabela = pd.DataFrame(
            {
                "produto": [produtos[n]["rotulo"] for n in pesos.pesos.index],
                "ticker": [produtos[n].get("ticker") or "—" for n in pesos.pesos.index],
                "peso": pesos.pesos.to_numpy(),
            }
        )
        if not modelo.fatores.pesos_usados.empty:
            usados = {modelo.ticker_produto.get(t, t): w for t, w in modelo.fatores.pesos_usados.items()}
            tabela["peso no índice diário"] = [usados.get(n, 0.0) for n in pesos.pesos.index]
        if pesos.origem == "comex":
            tabela["exportado 12m (US$ bi)"] = (pesos.valores_usd.reindex(pesos.pesos.index) / 1e9).to_numpy()
        st.dataframe(
            tabela,
            hide_index=True,
            width="stretch",
            column_config={
                "peso": st.column_config.NumberColumn(format="percent"),
                "peso no índice diário": st.column_config.NumberColumn(format="percent"),
                "exportado 12m (US$ bi)": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        desligados = [p["rotulo"] for p in produtos.values() if not p.get("ativo")]
        st.caption(
            "Desligados por padrão: " + ", ".join(desligados) + ". Cobre não entra na cesta. "
            "Ligar ou desligar: `config/cambio.yaml`."
        )
        for item, motivo in modelo.fatores.excluidos.items():
            nome = produtos.get(modelo.ticker_produto.get(item, item), {}).get("rotulo", item)
            st.warning(f"**{nome}** fora do índice diário: {motivo}. Pesos renormalizados.", icon="⚠️")

    with direita:
        base = modelo.fatores.base
        if base.empty:
            st.info("Sem dados suficientes para o índice: faltam cotações do Yahoo no banco.")
        else:
            corte = base.index.max() - pd.Timedelta(days=365)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=base.index[base.index >= corte],
                y=indice_base_100(base.loc[base.index >= corte, "cmd"]),
                name="Índice commodities Brasil", line={"width": 2.6, "color": charts.VERDE},
            ))
            fig.add_trace(go.Scatter(
                x=base.index[base.index >= corte],
                y=indice_base_100(-base.loc[base.index >= corte, "usdbrl"]),
                name="Real (US$/R$, invertido: alta = real apreciando)",
                line={"width": 2, "color": charts.BR, "dash": "dot"},
            ))
            fig.update_yaxes(title="base 100")
            st.plotly_chart(_legenda_embaixo(charts.aplicar_tema(fig, titulo="Últimos 12 meses")), width="stretch")

    minerio = produtos.get("minerio_ferro", {})
    reserva = minerio.get("reserva_mensal")
    if reserva and minerio.get("ticker") in modelo.fatores.excluidos:
        mensal = niveis.get(reserva, pd.Series(dtype=float)).dropna()
        st.warning(
            f"**Minério de ferro:** o futuro diário ({minerio['ticker']}) está fora do índice. "
            f"Como contexto, a série mensal do FMI ({reserva}, via FRED) — que não entra na "
            "atribuição diária.",
            icon="⚠️",
        )
        if not mensal.empty:
            st.plotly_chart(
                charts.grafico_series(
                    [("Minério de ferro (FMI, mensal)", mensal.tail(36).rename("valor").rename_axis("data_ref").reset_index())],
                    titulo="Minério de ferro — FMI (US$/t, mensal)",
                    cores=[charts.AMBAR],
                ),
                width="stretch",
            )


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%".replace(".", ",") if np.isfinite(x) else "—"


def secao_atribuicao(modelo: Modelo, janela: int) -> None:
    conf = cfg()
    resultado = modelo.atribuicao
    st.info(atrib.TEXTO_ORDEM, icon="🧮")
    if resultado.empty:
        st.warning(
            f"Atribuição indisponível: são necessárias mais de {janela} sessões com USD/BRL, "
            f"DXY, cesta EM e cesta de commodities (há {len(modelo.fatores.base)}). "
            + (f"Último erro do Yahoo: {erros_por_fonte()['yahoo']}" if "yahoo" in erros_por_fonte() else ""),
            icon="⚠️",
        )
        return

    ultima = resultado.iloc[-1]
    data = resultado.index[-1]
    diferenca = float(atrib.conferir_identidade(resultado.tail(1)).iloc[0])
    st.markdown(f"##### Sessão de {data:%d/%m/%Y}")
    colunas = st.columns(6)
    partes = [
        ("Observado", "Variação observada do USD/BRL", ultima["observado"]),
        ("DXY", atrib.ROTULOS["dxy"], ultima["contrib_dxy"]),
        ("EM ⊥", atrib.ROTULOS["em"], ultima["contrib_em"]),
        ("Commodities ⊥", atrib.ROTULOS["cmd"], ultima["contrib_cmd"]),
        ("Constante", atrib.ROTULOS["constante"], ultima["constante"]),
        ("Brasil (resíduo)", atrib.ROTULOS["residuo"], ultima["residuo"]),
    ]
    for coluna, (rotulo, ajuda, valor) in zip(colunas, partes, strict=True):
        coluna.metric(rotulo, _pct(valor), help=ajuda)
    st.caption(
        f"Retornos log do USD/BRL (positivo = real se depreciando). z do resíduo: "
        f"**{ultima['z_residuo']:+.2f}** (desvio do resíduo na janela: {ultima['desvio_residuo'] * 100:.2f}%). "
        f"Conferência: observado − (constante + contribuições + resíduo) = {diferenca:.1e}. "
        f"Janela de {janela} sessões; R² {ultima['r2']:.2f}. "
        "Fechamentos do Yahoo (câmbio e DXY no fim do dia de NY; futuros agrícolas no "
        "fechamento de Chicago, Brent em Londres): os horários não são idênticos, e a "
        "diferença aparece no resíduo."
    )

    _flags(modelo)

    n = int(conf["atribuicao"]["sessoes_grafico"])
    recorte = resultado.tail(n)
    # Eixo categórico com rótulos dd/mm: sessões consecutivas, sem buracos de fim de semana.
    eixo = [d.strftime("%d/%m") for d in recorte.index]
    fig = go.Figure()
    cores = {"dxy": charts.US, "em": charts.ROXO, "cmd": charts.VERDE}
    for fator in atrib.FATORES:
        fig.add_trace(go.Bar(x=eixo, y=recorte[f"contrib_{fator}"] * 100,
                             name=atrib.ROTULOS[fator], marker_color=cores[fator]))
    fig.add_trace(go.Bar(x=eixo, y=recorte["constante"] * 100,
                         name=atrib.ROTULOS["constante"], marker_color=charts.CINZA))
    fig.add_trace(go.Bar(x=eixo, y=recorte["residuo"] * 100,
                         name=atrib.ROTULOS["residuo"], marker_color=charts.BR))
    fig.add_trace(go.Scatter(x=eixo, y=recorte["observado"] * 100, mode="markers",
                             name="Observado", marker={"symbol": "diamond", "size": 9, "color": "#1F2733"}))
    marcas = modelo.flags.reindex(recorte.index)
    simbolos = {"estresse_brasil": "🇧🇷", "risk_off_global": "🌐", "choque_commodities": "🛢️"}
    positivas = recorte[[f"contrib_{f}" for f in atrib.FATORES] + ["constante", "residuo"]].clip(lower=0).sum(axis=1)
    for rotulo, (data_, linha) in zip(eixo, marcas.iterrows(), strict=True):
        texto = "".join(s for k, s in simbolos.items() if bool(linha.get(k)))
        if texto:
            topo = max(positivas.loc[data_], recorte.loc[data_, "observado"]) * 100
            fig.add_annotation(x=rotulo, y=topo, text=texto, showarrow=False, yshift=14)
    fig.update_layout(barmode="relative")
    fig.update_yaxes(title="contribuição (%)", ticksuffix="%")
    fig.update_xaxes(type="category")
    st.plotly_chart(
        _legenda_embaixo(charts.aplicar_tema(fig, titulo=f"Atribuição das últimas {len(recorte)} sessões")),
        width="stretch",
    )
    st.caption("🇧🇷 estresse Brasil · 🌐 risk-off global · 🛢️ choque de commodities")

    esquerda, direita = st.columns(2)
    with esquerda:
        fig = go.Figure()
        for fator in atrib.FATORES:
            fig.add_trace(go.Scatter(x=resultado.index, y=resultado[f"beta_{fator}"],
                                     name=atrib.ROTULOS[fator], line={"width": 2, "color": cores[fator]}))
        fig.update_yaxes(title="beta")
        st.plotly_chart(_legenda_embaixo(charts.aplicar_tema(fig, titulo=f"Betas rolling ({janela} sessões)")),
                        width="stretch")
    with direita:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=resultado.index, y=resultado["r2"], name="R²",
                                 line={"width": 2, "color": charts.BR}))
        fig.add_trace(go.Scatter(x=resultado.index, y=resultado["z_residuo"], name="z do resíduo",
                                 yaxis="y2", line={"width": 1, "color": charts.CINZA}))
        fig.update_layout(yaxis={"title": "R²", "range": [0, 1]},
                          yaxis2={"title": "z", "overlaying": "y", "side": "right", "showgrid": False})
        st.plotly_chart(_legenda_embaixo(charts.aplicar_tema(fig, titulo="R² rolling e z do resíduo")),
                        width="stretch")

    with st.expander("Tabela das últimas sessões"):
        tabela = recorte[["observado", "constante", "contrib_dxy", "contrib_em", "contrib_cmd",
                          "residuo", "z_residuo", "r2"]].copy()
        for col in ["observado", "constante", "contrib_dxy", "contrib_em", "contrib_cmd", "residuo"]:
            tabela[col] = tabela[col] * 100
        tabela = tabela.join(modelo.flags.reindex(recorte.index))
        tabela.index = tabela.index.strftime("%d/%m/%Y")
        st.dataframe(tabela.iloc[::-1].round(3), width="stretch")


def _legenda_embaixo(fig: go.Figure) -> go.Figure:
    """Em gráficos de meia largura, a legenda no topo encavala no título."""
    fig.update_layout(legend={"orientation": "h", "y": -0.18, "yanchor": "top", "x": 0})
    return fig


def _flags(modelo: Modelo) -> None:
    lim = {**atrib.LIMIARES_PADRAO, **(cfg().get("flags") or {})}
    if modelo.flags.empty:
        return
    hoje = modelo.flags.iloc[-1]
    ctx = modelo.contexto.reindex(modelo.flags.index).iloc[-1]
    ultima = modelo.atribuicao.iloc[-1]

    def leitura(valor: float, fmt: str) -> str:
        return format(valor, fmt) if np.isfinite(valor) else "sem dado"

    definicoes = [
        ("estresse_brasil", "🇧🇷 Estresse Brasil",
         f"z do resíduo ≥ +{lim['estresse_z_residuo']:g} e DI longo ≥ +{lim['estresse_di_bps']:g} bps no dia",
         f"z {leitura(ultima['z_residuo'], '+.2f')} · DI {leitura(ctx['di_longo_bps'], '+.1f')} bps"),
        ("risk_off_global", "🌐 Risk-off global",
         f"VIX ≥ +{lim['riskoff_vix_pct']:g}% no dia, DXY em alta e cesta EM depreciando",
         f"VIX {leitura(ctx['vix_pct'], '+.1f')}% · DXY {_pct(ctx['dxy'])} · EM {_pct(ctx['em'])}"),
        ("choque_commodities", "🛢️ Choque de commodities",
         f"|f3| ≥ {lim['choque_cmd_desvios']:g} desvios-padrão da janela",
         f"f3 = {leitura(ultima['z_cmd'], '+.2f')} desvios"),
    ]
    colunas = st.columns(3)
    for coluna, (chave, titulo, regra, valores) in zip(colunas, definicoes, strict=True):
        with coluna, st.container(border=True):
            ativo = bool(hoje[chave])
            st.markdown(f"**{titulo}** — {'**ATIVA**' if ativo else 'inativa'}")
            st.caption(f"Regra: {regra}. Hoje: {valores}.")
    st.caption("Limiares em `config/cambio.yaml` (`flags`). Uma flag descreve o regime do dia; não é sinal.")


def secao_calendario() -> None:
    hoje = hoje_sp()
    dias, eventos = agenda.proximos_dias_uteis(hoje, 10)
    st.caption(
        f"Próximos 10 dias úteis (calendário brasileiro) a partir de {hoje:%d/%m/%Y}. "
        "Horários de Brasília. ⚠️ = data a confirmar no calendário oficial; "
        "ver `config/calendario_eventos.yaml`."
    )
    feriados = calendario.feriados_entre(hoje, dias[-1])
    if feriados:
        st.caption("Feriados nacionais no período: " + "; ".join(
            f"{d:%d/%m} ({nome})" for d, nome in feriados.items()))

    dias_semana = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    linhas = []
    for linha in eventos.itertuples():
        linhas.append({
            "data": f"{dias_semana[linha.data.weekday()]} {linha.data:%d/%m}",
            "hora (BRT)": linha.hora,
            "evento": linha.evento + (" ⚠️" if linha.a_confirmar else ""),
            "região": linha.regiao,
            "importância": "●" * linha.importancia,
            "observação": linha.nota,
        })
    if not linhas:
        st.info("Nenhum evento configurado para o período.")
        return
    tabela = pd.DataFrame(linhas)
    # Altura para caber tudo: a tabela não deve rolar por dentro.
    st.dataframe(tabela, hide_index=True, width="stretch", height=35 * (len(tabela) + 1) + 3)
    sem_evento = [d for d in dias if d not in set(eventos["data"])]
    if sem_evento:
        st.caption("Dias úteis sem evento configurado: " + ", ".join(f"{d:%d/%m}" for d in sem_evento) + ".")
