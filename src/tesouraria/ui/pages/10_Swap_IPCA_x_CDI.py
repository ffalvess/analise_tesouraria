"""Marcação a mercado de um swap CDI × IPCA, passo a passo.

As outras telas leem a inflação implícita como indicador. Esta a usa como
preço: monta um swap, projeta as duas pontas com as curvas PRE e DAP do dia e
mostra quanto vale hoje a diferença entre receber CDI e pagar IPCA.

A página abre preenchida com um exemplo publicado e conferido (ver o rodapé),
de modo que a primeira coisa que ela mostra é uma conta cujo resultado se pode
verificar linha a linha antes de confiar nela com números próprios.
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


def reais(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def aniversario(avaliacao: dt.date, dia: int) -> tuple[dt.date, dt.date]:
    """Aniversário mensal vigente na avaliação e o seguinte.

    É a janela em que a projeção mensal do IPCA é rateada *pro rata*. Meses
    curtos são tratados pelo próprio calendário: `dia` maior que o último dia
    do mês cai no último.
    """
    def no_mes(ano: int, mes: int) -> dt.date:
        ultimo = pd.Timestamp(year=ano, month=mes, day=1).days_in_month
        return dt.date(ano, mes, min(dia, ultimo))

    atual = no_mes(avaliacao.year, avaliacao.month)
    if atual > avaliacao:
        anterior = pd.Timestamp(atual) - pd.DateOffset(months=1)
        atual = no_mes(anterior.year, anterior.month)

    seguinte = pd.Timestamp(atual) + pd.DateOffset(months=1)
    return atual, no_mes(seguinte.year, seguinte.month)


@st.cache_data(ttl=common.TTL, show_spinner=False)
def cdi_diario() -> pd.DataFrame:
    return queries.serie(SERIE_CDI)


def vertices_de(editor: pd.DataFrame, avaliacao: dt.date, rotulo: str) -> list[fat.Vertice]:
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


def tabela_curva(vertices: list[fat.Vertice], du_alvo: int) -> pd.DataFrame:
    """Vértices da curva com a linha interpolada no meio, como numa planilha de mesa."""
    linhas = [
        {"Contrato": v.rotulo, "Prazo (d.u.)": v.du, "Taxa (% a.a.)": v.taxa, "Fator Q": v.fator}
        for v in sorted(vertices, key=lambda v: v.du)
    ]
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
avaliacao = st.sidebar.date_input("Avaliação", value=dt.date(2026, 9, 18), format="DD/MM/YYYY")
vencimento_contratual = st.sidebar.date_input(
    "Vencimento", value=dt.date(2026, 11, 20), format="DD/MM/YYYY"
)
cupom = st.sidebar.number_input("Cupom da ponta IPCA (% a.a.)", value=10.0, step=0.05, format="%.4f")
spread = st.sidebar.number_input("Spread da ponta CDI (% a.a.)", value=0.28, step=0.01, format="%.4f")

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
du_decorridos = cal.dias_uteis(ancora, avaliacao)
du_mes = cal.dias_uteis(ancora, proxima)

col_a, col_b, col_c = st.columns(3)
indice_inicial = col_a.number_input(
    "Número-índice na contratação (I₀)", value=7596.09, step=0.01, format="%.2f"
)
indice_fechado = col_b.number_input(
    "Último número-índice divulgado", value=7633.23, step=0.01, format="%.2f",
    help="IBGE/SIDRA, tabela 1737, variável 2266 — o índice do mês fechado mais recente.",
)
projecao = col_c.number_input(
    "Projeção mensal do IPCA (%)", value=0.56, step=0.01, format="%.2f",
    help="Projeção do mês corrente, tipicamente a da ANBIMA.",
)

correcao = sw.corrigir_indice(indice_inicial, indice_fechado, projecao, du_decorridos, du_mes)

col_d, col_e, col_f = st.columns(3)
col_d.metric("Índice projetado", f"{correcao.indice_projetado:,.8f}".replace(",", "."))
col_e.metric("Fator de referência", f"{correcao.fator:.8f}")
col_f.metric("Correção acumulada", charts.formatar_pct(correcao.variacao_pct, 4))

st.caption(
    f"Aniversário em {ancora:%d/%m/%Y}: {du_decorridos} de {du_mes} dias úteis decorridos, "
    f"rateados sobre a projeção de {projecao:.2f}%. O índice divulgado é dado; a fração "
    "*pro rata* ainda é estimativa — é a defasagem de indexação aparecendo no preço."
)

# ------------------------------------------------------------ 2. curvas

st.subheader("2. Curvas PRE e DAP no dia da avaliação")
st.caption(
    "Taxas efetivas anuais em base 252. Os vértices abrem com os ajustes de DI1 e DAP de "
    "18/09/2026; troque por outro pregão editando as linhas."
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

vertices_pre = vertices_de(editor_pre, avaliacao, "PRE")
vertices_dap = vertices_de(editor_dap, avaliacao, "DAP")

try:
    taxa_pre = fat.taxa_interpolada(vertices_pre, du_restantes)
    taxa_dap = fat.taxa_interpolada(vertices_dap, du_restantes)
except ValueError as erro:
    st.error(
        f"Não dá para interpolar em {du_restantes} d.u.: {erro}. Inclua vértices que "
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

usar_banco = st.checkbox(
    "Calcular a partir da série 12 do SGS (CDI diário) já ingerida", value=False
)
fator_cdi = 1.04722628
if usar_banco:
    try:
        fator_cdi = sw.fator_cdi_acumulado(cdi_diario(), inicio, avaliacao)
        st.success(
            f"Fator do CDI entre {inicio:%d/%m/%Y} e {avaliacao:%d/%m/%Y}: **{fator_cdi:.8f}**"
        )
    except (ValueError, KeyError) as erro:
        st.warning(
            f"Não foi possível montar o fator a partir do banco ({erro}). "
            "Rode `tesouraria ingest --source bcb_sgs` ou informe o fator abaixo."
        )
        usar_banco = False
if not usar_banco:
    fator_cdi = st.number_input(
        "Fator do CDI acumulado do início até a avaliação", value=1.04722628, format="%.8f"
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
    "pelos testes em `tests/test_swap.py`. Fontes dos dados do exemplo: ajustes de DI1 e DAP "
    "da B3, números-índice do IPCA (IBGE/SIDRA 1737), projeção mensal da ANBIMA e CDI diário "
    "(BCB/SGS série 12)."
)

common.rodape()
