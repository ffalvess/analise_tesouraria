"""Marcação a mercado de um swap CDI × IPCA, passo a passo.

As outras telas leem a inflação implícita como indicador. Esta a usa como
preço: monta um swap, projeta as duas pontas com as curvas PRE e DAP do dia e
mostra quanto vale hoje a diferença entre receber CDI e pagar IPCA.

A tela tem duas origens de dados, escolhidas na barra lateral:

- **Exemplo publicado**, com tudo editável. É a conta documentada em
  `tests/test_swap.py`, conferida linha a linha contra a publicação original —
  serve para verificar a ferramenta antes de confiar nela com números próprios.
- **Dados ingeridos**, que puxa as curvas DI1 e DAP do pregão escolhido, o
  número-índice do IPCA, a projeção mensal do Focus e o CDI acumulado. Cada
  campo continua editável, e a tela diz de onde veio cada número.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from tesouraria import queries
from tesouraria.analytics import calendario as cal
from tesouraria.analytics import fatores as fat
from tesouraria.analytics import swap as sw
from tesouraria.ui import charts, common

common.configurar("Swap CDI × IPCA", "🔁")

SERIE_CDI = "12"  # SGS: CDI diário, em % ao dia
SERIE_INDICE = "1737-2266"  # SIDRA: IPCA em número-índice

# Valores do exemplo publicado, usados como padrão e como rede de segurança
# quando a série correspondente não está no banco.
EXEMPLO = {
    "indice_inicial": 7596.09,
    "indice_fechado": 7633.23,
    "projecao": 0.56,
    "fator_cdi": 1.04722628,
}


def reais(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def aniversario(referencia: dt.date, dia: int) -> tuple[dt.date, dt.date]:
    """Aniversário mensal vigente na data e o seguinte.

    É a janela em que a projeção mensal do IPCA é rateada *pro rata*. Meses
    curtos são tratados pelo próprio calendário: `dia` maior que o último dia
    do mês cai no último.
    """

    def no_mes(ano: int, mes: int) -> dt.date:
        ultimo = pd.Timestamp(year=ano, month=mes, day=1).days_in_month
        return dt.date(ano, mes, min(dia, ultimo))

    atual = no_mes(referencia.year, referencia.month)
    if atual > referencia:
        anterior = pd.Timestamp(atual) - pd.DateOffset(months=1)
        atual = no_mes(anterior.year, anterior.month)

    seguinte = pd.Timestamp(atual) + pd.DateOffset(months=1)
    return atual, no_mes(seguinte.year, seguinte.month)


@st.cache_data(ttl=common.TTL, show_spinner=False)
def serie_macro(serie_id: str) -> pd.DataFrame:
    return queries.serie(serie_id)


@st.cache_data(ttl=common.TTL, show_spinner=False)
def focus_mensal() -> pd.DataFrame:
    return queries.focus(indicador="IPCA", tipo="mensal")


def indice_da_ancora(serie: pd.DataFrame, ancora: dt.date) -> float | None:
    """Número-índice que corrige o VNA a partir de um aniversário.

    A correção que entra em vigor no aniversário do mês M é a do IPCA de M−1 —
    a defasagem de indexação. Devolve `None` quando a competência não está no
    banco, em vez de cair no índice mais próximo: usar o mês errado erra a
    correção por um mês inteiro de inflação sem nenhum aviso.
    """
    if serie.empty:
        return None
    competencia = (pd.Timestamp(ancora) - pd.DateOffset(months=1)).replace(day=1).date()
    linhas = serie[pd.to_datetime(serie["data_ref"]).dt.date == competencia]
    return None if linhas.empty else float(linhas["valor"].iloc[-1])


def projecao_do_focus(mensal: pd.DataFrame, ancora: dt.date, ate: dt.date) -> float | None:
    """Mediana do Focus para a competência do aniversário, na coleta mais recente."""
    if mensal.empty:
        return None
    competencia = f"{ancora.month:02d}/{ancora.year}"
    linhas = mensal[
        (mensal["data_referencia"] == competencia)
        & (pd.to_datetime(mensal["data_coleta"]).dt.date <= ate)
    ]
    return None if linhas.empty else float(linhas["mediana"].iloc[-1])


def vertices_do_banco(data_ref: dt.date, tipo: str, avaliacao: dt.date) -> list[fat.Vertice]:
    """Vértices da curva ingerida da B3, com o prazo recontado até a avaliação."""
    curva = common.cache_curva_br(data_ref, "b3", tipo)
    vertices: list[fat.Vertice] = []
    for _, linha in curva.iterrows():
        if pd.isna(linha["vencimento"]) or pd.isna(linha["taxa"]):
            continue
        du = cal.dias_uteis(avaliacao, pd.Timestamp(linha["vencimento"]).date())
        if du > 0:
            vertices.append(
                fat.Vertice(du=du, taxa=float(linha["taxa"]), rotulo=str(linha["instrumento"]))
            )
    return vertices


def vertices_do_editor(editor: pd.DataFrame, avaliacao: dt.date, rotulo: str) -> list[fat.Vertice]:
    """Converte a tabela editável em vértices com prazo em dias úteis."""
    vertices: list[fat.Vertice] = []
    for _, linha in editor.iterrows():
        if pd.isna(linha["vencimento"]) or pd.isna(linha["taxa"]):
            continue
        vencimento = cal.proximo_dia_util(pd.Timestamp(linha["vencimento"]).date())
        du = cal.dias_uteis(avaliacao, vencimento)
        if du <= 0:
            continue
        vertices.append(
            fat.Vertice(du=du, taxa=float(linha["taxa"]), rotulo=str(linha["contrato"] or rotulo))
        )
    return vertices


def tabela_curva(vertices: list[fat.Vertice], du_alvo: int, vizinhos: int = 2) -> pd.DataFrame:
    """Vértices ao redor do prazo procurado, com a linha interpolada no meio.

    Uma curva de DI tem dezenas de vencimentos; o que importa para esta conta
    são os que cercam a liquidação.
    """
    ordenados = sorted(vertices, key=lambda v: v.du)
    antes = [v for v in ordenados if v.du < du_alvo][-vizinhos:]
    depois = [v for v in ordenados if v.du > du_alvo][:vizinhos]
    exatos = [v for v in ordenados if v.du == du_alvo]

    linhas = [
        {"Contrato": v.rotulo, "Prazo (d.u.)": v.du, "Taxa (% a.a.)": v.taxa, "Fator Q": v.fator}
        for v in [*antes, *exatos, *depois]
    ]
    if not exatos:
        linhas.append(
            {
                "Contrato": "Interpolado",
                "Prazo (d.u.)": du_alvo,
                "Taxa (% a.a.)": fat.taxa_interpolada(vertices, du_alvo),
                "Fator Q": fat.interpolar(vertices, du_alvo),
            }
        )
    quadro = pd.DataFrame(linhas).sort_values("Prazo (d.u.)").reset_index(drop=True)
    quadro["Taxa (% a.a.)"] = quadro["Taxa (% a.a.)"].round(4)
    quadro["Fator Q"] = quadro["Fator Q"].round(8)
    return quadro


# --------------------------------------------------------------- contrato

st.sidebar.subheader("Origem dos dados")
origem = st.sidebar.radio(
    "Curvas e índices",
    ["exemplo", "banco"],
    key="origem_dados",
    format_func=lambda o: "Exemplo publicado" if o == "exemplo" else "Dados ingeridos (B3)",
    help=(
        "O exemplo abre com as curvas de 18/09/2026 e é totalmente editável. "
        "A outra opção usa o pregão da B3 que estiver no banco."
    ),
)

st.sidebar.subheader("Contrato")
notional = st.sidebar.number_input(
    "Notional (R$)", min_value=0.0, value=10_000_000.0, step=100_000.0, format="%.2f"
)
ponta = st.sidebar.radio(
    "Ponta recebida",
    ["cdi", "ipca"],
    format_func=lambda p: "Recebe CDI, paga IPCA" if p == "cdi" else "Recebe IPCA, paga CDI",
)
inicio = st.sidebar.date_input("Início", value=dt.date(2026, 5, 15), format="DD/MM/YYYY")

if origem == "banco":
    avaliacao = common.seletor_data("curve_br", "Avaliação (pregão)", "data_swap", "b3", "pre")
    if avaliacao is None:
        st.warning(
            "Nenhuma curva da B3 no banco. Rode `tesouraria ingest --source b3_di` "
            "ou volte para o exemplo publicado na barra lateral."
        )
        st.stop()
else:
    avaliacao = st.sidebar.date_input("Avaliação", value=dt.date(2026, 9, 18), format="DD/MM/YYYY")

vencimento_contratual = st.sidebar.date_input(
    "Vencimento", value=dt.date(2026, 11, 20), format="DD/MM/YYYY"
)
cupom = st.sidebar.number_input("Cupom da ponta IPCA (% a.a.)", value=10.0, step=0.05, format="%.4f")
spread = st.sidebar.number_input(
    "Spread da ponta CDI (% a.a.)", value=0.28, step=0.01, format="%.4f"
)

liquidacao = cal.proximo_dia_util(vencimento_contratual)
if liquidacao != vencimento_contratual:
    st.sidebar.caption(
        f"{vencimento_contratual:%d/%m} não é dia útil; a liquidação vai para {liquidacao:%d/%m}."
    )

du_total = cal.dias_uteis(inicio, liquidacao)
du_restantes = cal.dias_uteis(avaliacao, liquidacao)
if du_total <= 0 or du_restantes <= 0 or du_restantes > du_total:
    st.error(
        "As datas não formam um contrato válido: é preciso início < avaliação < liquidação, "
        f"e hoje temos {du_total} d.u. de prazo total e {du_restantes} d.u. restantes."
    )
    st.stop()

st.caption(
    f"**{du_total} dias úteis** de contrato, dos quais **{du_restantes}** ainda faltam. "
    "O cupom e o spread remuneram o contrato inteiro; as curvas projetam só o que falta."
)

# ------------------------------------------------- 1. correção do índice

st.subheader("1. Correção do IPCA até a avaliação")

ancora, proxima = aniversario(avaliacao, inicio.day)
ancora_inicial, _ = aniversario(inicio, inicio.day)
du_decorridos = cal.dias_uteis(ancora, avaliacao)
du_mes = cal.dias_uteis(ancora, proxima)

padroes = dict(EXEMPLO)
procedencia: list[str] = []
if origem == "banco":
    indice = serie_macro(SERIE_INDICE)
    do_banco = {
        "indice_inicial": indice_da_ancora(indice, ancora_inicial),
        "indice_fechado": indice_da_ancora(indice, ancora),
        "projecao": projecao_do_focus(focus_mensal(), ancora, avaliacao),
    }
    for campo, valor in do_banco.items():
        if valor is not None:
            padroes[campo] = valor
    faltando = [campo for campo, valor in do_banco.items() if valor is None]
    if faltando:
        st.warning(
            "Sem dado ingerido para: "
            + ", ".join(faltando)
            + ". Os campos abaixo ficaram com os valores do exemplo — ajuste antes de usar. "
            "Rode `tesouraria ingest --source ibge_sidra --source focus` para preenchê-los."
        )
    else:
        procedencia.append(
            "Números-índice do SIDRA (1737-2266) e projeção mensal do Focus (mediana)."
        )

col_a, col_b, col_c = st.columns(3)
indice_inicial = col_a.number_input(
    "Número-índice na contratação (I₀)",
    value=padroes["indice_inicial"],
    step=0.01,
    format="%.2f",
    key=f"i0_{origem}",
    help=f"Competência de {(pd.Timestamp(ancora_inicial) - pd.DateOffset(months=1)):%m/%Y}.",
)
indice_fechado = col_b.number_input(
    "Último número-índice divulgado",
    value=padroes["indice_fechado"],
    step=0.01,
    format="%.2f",
    key=f"ifechado_{origem}",
    help=f"Competência de {(pd.Timestamp(ancora) - pd.DateOffset(months=1)):%m/%Y}.",
)
projecao = col_c.number_input(
    "Projeção mensal do IPCA (%)",
    value=padroes["projecao"],
    step=0.01,
    format="%.2f",
    key=f"projecao_{origem}",
    help=(
        "A convenção de mesa é a projeção da ANBIMA, que não tem API aberta. "
        "Com dados ingeridos, a tela usa a mediana do Focus para a competência."
    ),
)

correcao = sw.corrigir_indice(indice_inicial, indice_fechado, projecao, du_decorridos, du_mes)

col_d, col_e, col_f = st.columns(3)
col_d.metric("Índice projetado", f"{correcao.indice_projetado:,.8f}".replace(",", "."))
col_e.metric("Fator de referência", f"{correcao.fator:.8f}")
col_f.metric("Correção acumulada", charts.formatar_pct(correcao.variacao_pct, 4))

st.caption(
    f"Aniversário em {ancora:%d/%m/%Y}: {du_decorridos} de {du_mes} dias úteis decorridos, "
    f"rateados sobre a projeção de {projecao:.2f}%. O índice divulgado é dado; a fração "
    "*pro rata* ainda é estimativa — é a defasagem de indexação aparecendo no preço. "
    + " ".join(procedencia)
)

# ------------------------------------------------------------ 2. curvas

st.subheader("2. Curvas PRE e DAP no dia da avaliação")

if origem == "banco":
    vertices_pre = vertices_do_banco(avaliacao, "pre", avaliacao)
    vertices_dap = vertices_do_banco(avaliacao, "dap", avaliacao)
    st.caption(
        f"Ajustes de DI1 e DAP de {avaliacao:%d/%m/%Y}: "
        f"{len(vertices_pre)} vértices de PRE e {len(vertices_dap)} de DAP no banco."
    )
    if not vertices_dap:
        st.warning(
            "Nenhum vértice de DAP para esta data. A curva de cupom de IPCA entrou na "
            "ingestão junto com o DI1 — rode `tesouraria ingest --source b3_di` de novo "
            "para que o histórico passe a incluí-la."
        )
        st.stop()
else:
    st.caption(
        "Taxas efetivas anuais em base 252. Os vértices abrem com os ajustes de DI1 e DAP "
        "de 18/09/2026; troque por outro pregão editando as linhas."
    )
    padrao_pre = pd.DataFrame(
        {
            "contrato": ["DI1X26", "DI1Z26"],
            "vencimento": [dt.date(2026, 11, 3), dt.date(2026, 12, 1)],
            "taxa": [13.6550, 13.5880],
        }
    )
    padrao_dap = pd.DataFrame(
        {
            "contrato": ["DAPX26", "DAPZ26"],
            "vencimento": [dt.date(2026, 11, 16), dt.date(2026, 12, 15)],
            "taxa": [8.3150, 8.1150],
        }
    )
    config_colunas = {
        "contrato": st.column_config.TextColumn("Contrato"),
        "vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
        "taxa": st.column_config.NumberColumn("Taxa (% a.a.)", format="%.4f"),
    }

    esquerda, direita = st.columns(2)
    with esquerda:
        st.markdown("**PRE — futuro de DI (DI1)**")
        editor_pre = st.data_editor(
            padrao_pre, column_config=config_colunas, num_rows="dynamic", hide_index=True,
            key="vertices_pre", width="stretch",
        )
    with direita:
        st.markdown("**DAP — cupom de IPCA**")
        editor_dap = st.data_editor(
            padrao_dap, column_config=config_colunas, num_rows="dynamic", hide_index=True,
            key="vertices_dap", width="stretch",
        )

    vertices_pre = vertices_do_editor(editor_pre, avaliacao, "PRE")
    vertices_dap = vertices_do_editor(editor_dap, avaliacao, "DAP")

try:
    taxa_pre = fat.taxa_interpolada(vertices_pre, du_restantes)
    taxa_dap = fat.taxa_interpolada(vertices_dap, du_restantes)
except ValueError as erro:
    st.error(
        f"Não dá para interpolar em {du_restantes} d.u.: {erro}. É preciso ter vértices que "
        "cerquem a data de liquidação — a página não extrapola a ponta da curva."
    )
    st.stop()

col_g, col_h = st.columns(2)
with col_g:
    st.dataframe(tabela_curva(vertices_pre, du_restantes), hide_index=True, width="stretch")
with col_h:
    st.dataframe(tabela_curva(vertices_dap, du_restantes), hide_index=True, width="stretch")

st.caption(
    "A interpolação é *flat-forward* sobre o fator acumulado — a convenção com que a B3 e "
    "as mesas marcam esses contratos —, e não uma spline sobre a taxa."
)

# ------------------------------------------------------- 3. CDI acumulado

st.subheader("3. CDI já acumulado")

fator_cdi = EXEMPLO["fator_cdi"]
veio_do_banco = False
if origem == "banco":
    try:
        fator_cdi = sw.fator_cdi_acumulado(serie_macro(SERIE_CDI), inicio, avaliacao)
        veio_do_banco = True
        st.success(
            f"Série 12 do SGS, de {inicio:%d/%m/%Y} a {avaliacao:%d/%m/%Y}: "
            f"fator **{fator_cdi:.8f}**"
        )
    except (ValueError, KeyError) as erro:
        st.warning(
            f"Não foi possível montar o fator a partir do banco ({erro}). "
            "Rode `tesouraria ingest --source bcb_sgs` ou informe o fator abaixo."
        )
if not veio_do_banco:
    fator_cdi = st.number_input(
        "Fator do CDI acumulado do início até a avaliação",
        value=EXEMPLO["fator_cdi"],
        format="%.8f",
        key=f"cdi_{origem}",
    )

# ---------------------------------------------------------------- 4. MtM

contrato = sw.Swap(
    notional=notional,
    du_total=du_total,
    du_restantes=du_restantes,
    cupom_ipca=cupom,
    spread_cdi=spread,
    fator_referencia=correcao.fator,
    fator_cdi=fator_cdi,
    ponta_recebida=ponta,
)
resultado = sw.avaliar(contrato, taxa_pre, taxa_dap)

st.subheader("4. Projeção, desconto e marcação")

col_i, col_j, col_k = st.columns(3)
col_i.metric(
    f"Inflação implícita ({du_restantes} d.u.)",
    charts.formatar_pct(resultado.implicita_pct, 4),
    help="Razão entre os fatores da PRE e da DAP. É a inflação do período, não uma taxa anual.",
)
col_j.metric("Correção total do IPCA", charts.formatar_pct(resultado.correcao_pct, 4))
col_k.metric(
    "MtM",
    reais(resultado.mtm),
    help=(
        "Positivo para quem recebe a ponta escolhida na barra lateral: o direito a receber "
        "vale mais que a obrigação a pagar."
    ),
)

quadro = pd.DataFrame(
    [
        {
            "Ponta": "CDI (recebe)" if ponta == "cdi" else "CDI (paga)",
            f"VF em {liquidacao:%d/%m/%Y}": resultado.valor_futuro_cdi,
            f"VP em {avaliacao:%d/%m/%Y}": resultado.valor_presente_cdi,
        },
        {
            "Ponta": "IPCA (paga)" if ponta == "cdi" else "IPCA (recebe)",
            f"VF em {liquidacao:%d/%m/%Y}": resultado.valor_futuro_ipca,
            f"VP em {avaliacao:%d/%m/%Y}": resultado.valor_presente_ipca,
        },
    ]
)
st.dataframe(charts.arredondar(quadro, 2), hide_index=True, width="stretch")

with st.expander("Como cada número foi formado"):
    st.markdown(
        f"""
| Fator | Valor | Prazo |
|---|---|---|
| Correção do IPCA até a avaliação (`F_ref`) | {correcao.fator:.8f} | contratação → avaliação |
| Inflação implícita do período (`Q_PRE / Q_DAP`) | {resultado.inflacao_implicita:.8f} | {du_restantes} d.u. |
| Correção total (`F_IPCA`) | {resultado.fator_ipca:.8f} | contratação → liquidação |
| Fator da PRE (`Q_PRE`) | {resultado.fator_pre:.8f} | {du_restantes} d.u. |
| Fator da DAP (`Q_DAP`) | {resultado.fator_dap:.8f} | {du_restantes} d.u. |
| Cupom da ponta IPCA | {(1 + cupom / 100) ** (du_total / 252):.9f} | {du_total} d.u. |
| Spread da ponta CDI | {(1 + spread / 100) ** (du_total / 252):.9f} | {du_total} d.u. |
| CDI acumulado conhecido | {fator_cdi:.8f} | contratação → avaliação |

O desconto dos dois valores futuros usa o mesmo `Q_PRE`, então o principal e o
nível da curva pré se compensam entre as pontas.
        """
    )

st.caption(
    f"Spread de equilíbrio para um cupom de {cupom:.2f}% contra a DAP de "
    f"{taxa_dap:.4f}%: **{sw.spread_equivalente(cupom, taxa_dap):.4f}% a.a.** — o CDI+ que "
    "zeraria o valor presente das duas pontas. A diferença para o spread contratado é a "
    "margem embutida na cotação."
)

# ----------------------------------------------------------- 5. cenários

st.subheader("5. E se a curva mudar?")
choque = st.slider("Choque paralelo (bps)", 25, 300, 100, 25)
cenarios = sw.cenarios(contrato, taxa_pre, taxa_dap, choque_bps=float(choque))

exibir = cenarios.rename(
    columns={
        "cenario": "Cenário",
        "taxa_pre": "PRE (% a.a.)",
        "taxa_dap": "DAP (% a.a.)",
        "implicita_pct": f"Implícita em {du_restantes} d.u. (%)",
        "vp_ipca": "VP IPCA (R$)",
        "vp_cdi": "VP CDI (R$)",
        "mtm": "MtM (R$)",
    }
)
st.dataframe(charts.arredondar(exibir, 4), hide_index=True, width="stretch")

delta_dap = cenarios.loc[2, "mtm"] - cenarios.loc[0, "mtm"]
st.caption(
    f"Um choque de {choque} bps na pré não move o MtM: o mesmo fator projeta e desconta o "
    "mesmo prazo, e se cancela nas duas pontas. O mesmo choque na DAP move "
    f"{reais(abs(delta_dap))} — a exposição do contrato é à inflação implícita, não ao "
    "nível da curva pré."
)

st.divider()
st.caption(
    "Metodologia conferida contra o exemplo *IPCA × CDI: do índice ao MtM*, de Wallace Santo "
    "(calculadora em profsant.com.br/calculadora), cujos valores publicados são reproduzidos "
    "pelos testes em `tests/test_swap.py`. Fontes: ajustes de DI1 e DAP da B3, números-índice "
    "do IPCA (IBGE/SIDRA 1737), projeção mensal (ANBIMA na mesa, Focus aqui) e CDI diário "
    "(BCB/SGS série 12)."
)

common.rodape()
