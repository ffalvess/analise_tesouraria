"""Macro EUA: inflação, mercado de trabalho e o PIB pelas duas óticas.

É o que move a ponta americana da curva e, por ela, o diferencial de juros. A
página responde, nesta ordem: a inflação está indo para a meta? O mercado de
trabalho esfria ou quebra? E quem puxa o PIB — do lado de quem gasta (consumo,
investimento, governo, exportações e importações) e do lado de quem produz
(agropecuária, indústria e serviços)?

As contas estão em `analytics/macro_eua.py`; aqui fica a montagem. O período
escolhido no topo recorta todos os gráficos de uma vez, e as variações são
calculadas sobre o histórico inteiro antes do recorte — senão o primeiro ponto
de cada gráfico perderia a sua comparação de doze meses.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesouraria.analytics import macro_eua as m
from tesouraria.ui import charts, common

common.configurar("Macro EUA — inflação, emprego e PIB", "🇺🇸")

if not common.exigir_dados():
    st.stop()

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

# Inflação: medida -> (série do FRED, cor). A cor segue a medida em todos os
# gráficos da aba; a ordem é a validada para vizinhos distinguíveis.
MEDIDAS = {
    "CPI": ("CPIAUCSL", charts.BR),
    "Núcleo do CPI": ("CPILFESL", charts.US),
    "PCE": ("PCEPI", charts.VERDE),
    "Núcleo do PCE": ("PCEPILFE", charts.AMBAR),
}
COMPONENTES_CPI = {
    "Alimentos": ("CPIUFDSL", charts.VERDE),
    "Energia": ("CPIENGSL", charts.AMBAR),
    "Bens ex-alimentos e energia": ("CUSR0000SACL1E", charts.ROXO),
    "Moradia (shelter)": ("CUSR0000SAH1", charts.US),
    "Serviços ex-energia": ("CUSR0000SASLE", charts.BR),
}
# Componentes da demanda: coluna de `contribuicoes_despesa` -> (rótulo, nível, cor).
DESPESA = {
    "consumo": ("Consumo das famílias", "PCECC96", charts.BR),
    "investimento": ("Investimento privado", "GPDIC1", charts.US),
    "governo": ("Gastos do governo", "GCEC1", charts.VERDE),
    "exportacoes": ("Exportações", "EXPGSC1", charts.AMBAR),
    "importacoes": ("Importações", "IMPGSC1", charts.ROXO),
}
# Volume de cada setor (índice ou US$ encadeados) -> (série, cor).
VOLUME_SETORES = {
    "Agropecuária": ("VAQIAFH", charts.VERDE),
    "Construção": ("VAQIC", charts.US),
    "Transformação": ("VAQIMA", charts.ROXO),
    "Extrativa": ("VAQIM", charts.AMBAR),
    "Serviços privados": ("RVASPI", charts.BR),
}

SERIES = sorted(
    {sid for sid, _ in MEDIDAS.values()}
    | {sid for sid, _ in COMPONENTES_CPI.values()}
    | {nivel for _, nivel, _ in DESPESA.values()}
    | {sid for sid, _ in VOLUME_SETORES.values()}
    | set(m.CONTRIBUICOES_DESPESA)
    | set(m.CONTRIBUICOES_INDUSTRIA)
    | {
        "GDPC1",
        "UNRATE",
        "U6RATE",
        "PAYEMS",
        "ICSA",
        "CIVPART",
        "CES0500000003",
        "JTSJOL",
        "UNEMPLOY",
        "INDPRO",
        "IPMAN",
    }
)

# Uma consulta só para a página inteira, em vez de quarenta.
_todas = common.cache_serie(SERIES)
_por_id = (
    {sid: grupo.reset_index(drop=True) for sid, grupo in _todas.groupby("serie_id")}
    if not _todas.empty
    else {}
)
VAZIO = pd.DataFrame(columns=["data_ref", "valor"])


def serie(serie_id: str) -> pd.DataFrame:
    return _por_id.get(serie_id, VAZIO)


# ------------------------------------------------------------------ período
PERIODOS = {"2 anos": 2, "5 anos": 5, "10 anos": 10, "Tudo": None}
escolha = st.segmented_control(
    "Período dos gráficos",
    list(PERIODOS),
    default="5 anos",
    key="macro_eua_periodo",
    help=(
        "Recorta todos os gráficos da página. Cinco anos deixam de fora os "
        "trimestres da pandemia, cujas variações de ±30% achatariam todo o resto."
    ),
)
_anos = PERIODOS[escolha or "5 anos"]
DESDE = pd.Timestamp.today().normalize() - pd.DateOffset(years=_anos) if _anos else None


def recorte(dados: pd.DataFrame) -> pd.DataFrame:
    """Aplica o período do topo. As contas já foram feitas sobre o histórico."""
    if dados is None or dados.empty or DESDE is None:
        return dados
    return dados[pd.to_datetime(dados["data_ref"]) >= DESDE]


# ------------------------------------------------------------ apresentação


def rotulo_data(data: pd.Timestamp, freq: str) -> str:
    if freq == "trimestral":
        return f"{data.year} T{(data.month - 1) // 3 + 1}"
    if freq == "mensal":
        return f"{MESES[data.month - 1]}/{data.year}"
    return data.strftime("%d/%m/%Y")


def faltando(*descricoes: str) -> None:
    st.caption(
        "Ainda não coletado: " + ", ".join(descricoes) + ". Séries novas do FRED "
        "entram com o histórico inteiro na próxima coleta diária (exige `FRED_API_KEY`)."
    )


def indicador(
    alvo,
    rotulo: str,
    dados: pd.DataFrame,
    sufixo: str = "%",
    casas: int = 1,
    unidade_delta: str = "p.p.",
    cor_delta: str = "normal",
    ajuda: str = "",
) -> None:
    """Um número de destaque: o último valor e a mudança contra o anterior."""
    data, valor = m.ultimo(dados)
    if data is None:
        alvo.metric(rotulo, "—", help="Série ainda não coletada.")
        return
    _, anterior = m.ultimo(dados, -2)
    delta = None if pd.isna(anterior) else f"{valor - anterior:+,.{casas}f} {unidade_delta}".strip()
    referencia = f"Última observação: {rotulo_data(data, m.frequencia(dados))}."
    alvo.metric(
        rotulo,
        f"{valor:+,.{casas}f}{sufixo}" if sufixo == " p.p." else f"{valor:,.{casas}f}{sufixo}",
        delta=delta,
        delta_color=cor_delta,
        help=f"{ajuda} {referencia}".strip(),
    )


def tabela(series: dict[str, pd.DataFrame], casas: int = 2) -> None:
    """Os números de um gráfico, legíveis sem passar o mouse."""
    colunas = {rotulo: m.serie_temporal(recorte(d)) for rotulo, d in series.items()}
    colunas = {rotulo: s for rotulo, s in colunas.items() if not s.empty}
    if not colunas:
        return
    largo = pd.concat(colunas, axis=1).sort_index(ascending=False)
    freq = m.frequencia(next(iter(colunas.values())))
    largo.index = [rotulo_data(d, freq) for d in largo.index]
    with st.expander("Ver os números"):
        st.dataframe(largo.round(casas), width="stretch")


def grafico(fig) -> None:
    st.plotly_chart(fig, width="stretch")


aba_inflacao, aba_trabalho, aba_despesa, aba_producao = st.tabs(
    ["Inflação", "Mercado de trabalho", "PIB — ótica da despesa", "PIB — ótica da produção"]
)


# =================================================================== inflação
with aba_inflacao:
    anuais = {nome: m.variacao_anual(serie(sid)) for nome, (sid, _) in MEDIDAS.items()}

    for coluna, (nome, dados) in zip(st.columns(len(anuais)), anuais.items(), strict=True):
        indicador(coluna, f"{nome} — 12 meses", dados, cor_delta="inverse")

    presentes = {nome: d for nome, d in anuais.items() if not d.empty}
    if not presentes:
        faltando("CPI e PCE")
    else:
        fig = charts.grafico_series(
            [(nome, recorte(d)) for nome, d in presentes.items()],
            titulo="Inflação em 12 meses",
            eixo_y="% em 12 meses",
            sufixo="%",
            cores=[MEDIDAS[nome][1] for nome in presentes],
        )
        grafico(charts.linha_referencia(fig, m.META_FED, "meta do Fed: 2% no PCE"))
        tabela(presentes)
        if len(presentes) < len(anuais):
            faltando(*(nome for nome in anuais if nome not in presentes))

    st.subheader("Momento: a inflação recente está acelerando?")
    opcoes = [nome for nome in MEDIDAS if not serie(MEDIDAS[nome][0]).empty] or list(MEDIDAS)
    medida = st.segmented_control(
        "Medida",
        opcoes,
        default=next((n for n in ("Núcleo do PCE", "Núcleo do CPI") if n in opcoes), opcoes[0]),
        key="macro_eua_medida",
    )
    indice = serie(MEDIDAS[medida][0]) if medida else VAZIO
    if not medida:
        st.caption("Escolha uma medida acima.")
    elif indice.empty:
        faltando(medida)
    else:
        horizontes = {
            "12 meses": m.variacao_anual(indice),
            "6 meses anualizado": m.variacao_anualizada(indice, 6),
            "3 meses anualizado": m.variacao_anualizada(indice, 3),
        }
        fig = charts.grafico_series(
            [(nome, recorte(d)) for nome, d in horizontes.items()],
            titulo=f"{medida}: a mesma inflação em três horizontes",
            eixo_y="% a.a.",
            sufixo="%",
            cores=[charts.BR, charts.US, charts.VERDE],
        )
        grafico(charts.linha_referencia(fig, m.META_FED, "2%"))

        _, doze = m.ultimo(horizontes["12 meses"])
        _, tres = m.ultimo(horizontes["3 meses anualizado"])
        if pd.notna(doze) and pd.notna(tres):
            if tres < doze - 0.3:
                ritmo = "**desacelerando**: o ritmo dos últimos três meses está abaixo do acumulado"
            elif tres > doze + 0.3:
                ritmo = "**acelerando**: o ritmo dos últimos três meses já supera o acumulado"
            else:
                ritmo = "**estável**: o ritmo recente está em linha com o acumulado"
            st.info(
                f"{medida} {ritmo} em 12 meses ({tres:.1f}% contra {doze:.1f}%). "
                f"Distância da meta: {doze - m.META_FED:+.1f} p.p.",
                icon="🌡️",
            )
        st.caption(
            "O acumulado em 12 meses carrega o passado; a taxa de 3 meses anualizada mostra "
            "para onde a inflação está indo, ao custo de mais ruído. É a leitura que o Fed "
            "usa para decidir se a desinflação continua."
        )

    st.subheader("Composição do CPI")
    componentes = {nome: m.variacao_anual(serie(sid)) for nome, (sid, _) in COMPONENTES_CPI.items()}
    disponiveis = [nome for nome, d in componentes.items() if not d.empty]
    if not disponiveis:
        faltando("componentes do CPI")
    else:
        escolhidos = st.pills(
            "Componentes",
            disponiveis,
            selection_mode="multi",
            default=[n for n in disponiveis if n != "Energia"],
            key="macro_eua_componentes",
            help="Energia começa desligada: oscila dezenas de pontos e achata o resto.",
        )
        if escolhidos:
            grafico(
                charts.grafico_series(
                    [(nome, recorte(componentes[nome])) for nome in escolhidos],
                    titulo="CPI por componente — variação em 12 meses",
                    eixo_y="% em 12 meses",
                    sufixo="%",
                    cores=[COMPONENTES_CPI[nome][1] for nome in escolhidos],
                )
            )
            tabela({nome: componentes[nome] for nome in escolhidos})
        st.caption(
            "Moradia (shelter) pesa cerca de um terço do CPI e reage com atraso aos aluguéis "
            "de mercado; serviços ex-energia é a inflação de serviços que o Fed acompanha. "
            "Bens sem alimentos e energia respondem a câmbio, tarifas e cadeias de suprimento."
        )


# ======================================================== mercado de trabalho
with aba_trabalho:
    u3, u6 = serie("UNRATE"), serie("U6RATE")
    vagas = m.diferenca(serie("PAYEMS"))
    vagas_media = m.media_movel(vagas, 3)
    pedidos = serie("ICSA")
    pedidos_media = m.media_movel(pedidos, 4)
    sahm = m.regra_de_sahm(u3)
    salario = m.variacao_anual(serie("CES0500000003"))

    c1, c2, c3, c4, c5 = st.columns(5)
    indicador(c1, "Desemprego (U-3)", u3, cor_delta="inverse")
    indicador(
        c2,
        "Vagas no mês",
        vagas,
        sufixo=" mil",
        casas=0,
        unidade_delta="mil",
        ajuda="Variação do payroll (emprego formal fora da agropecuária).",
    )
    indicador(
        c3,
        "Seguro-desemprego",
        pedidos_media,
        sufixo="",
        casas=0,
        unidade_delta="",
        cor_delta="inverse",
        ajuda="Pedidos iniciais, média de 4 semanas.",
    )
    indicador(c4, "Salário/hora em 12 meses", salario, cor_delta="off")
    indicador(
        c5,
        "Regra de Sahm",
        sahm,
        sufixo=" p.p.",
        casas=2,
        cor_delta="inverse",
        ajuda=f"O sinal de recessão dispara em {m.LIMIAR_SAHM:.2f} p.p.",
    )

    if u3.empty:
        faltando("taxa de desemprego")
    else:
        taxas = [("U-3 (oficial)", recorte(u3))]
        if not u6.empty:
            taxas.append(("U-6 (amplo)", recorte(u6)))
        grafico(
            charts.grafico_series(
                taxas,
                titulo="Taxa de desemprego",
                eixo_y="% da força de trabalho",
                sufixo="%",
                cores=[charts.US, charts.ROXO],
            )
        )
        st.caption(
            "O U-6 soma ao desemprego oficial quem desistiu de procurar e quem trabalha "
            "meio período por falta de opção — costuma virar antes do U-3."
        )

    st.subheader("Criação de vagas (payroll)")
    if vagas.empty:
        faltando("payroll")
    else:
        grafico(
            charts.grafico_barras_media(
                recorte(vagas),
                recorte(vagas_media),
                "Variação mensal",
                "Média de 3 meses",
                titulo="Vagas criadas por mês",
                eixo_y="mil vagas",
            )
        )
        tabela({"Variação mensal": vagas, "Média de 3 meses": vagas_media}, casas=0)

    st.subheader("Regra de Sahm")
    if sahm.empty:
        faltando("taxa de desemprego")
    else:
        fig = charts.grafico_series(
            [("Regra de Sahm", recorte(sahm))],
            titulo="Indicador de recessão de Sahm",
            eixo_y="p.p.",
            cores=[charts.US],
        )
        grafico(charts.linha_referencia(fig, m.LIMIAR_SAHM, "limiar de recessão"))
        _, atual = m.ultimo(sahm)
        if atual >= m.LIMIAR_SAHM:
            st.warning(
                f"Sinal **disparado**: {atual:.2f} p.p. O indicador acompanhou todas as "
                "recessões americanas desde 1970, mas em 2024 disparou sem que uma viesse — "
                "leia como alerta, não como veredito.",
                icon="🚨",
            )
        else:
            st.info(
                f"Indicador em {atual:.2f} p.p., a {m.LIMIAR_SAHM - atual:.2f} p.p. do limiar.",
                icon="📉",
            )
        st.caption(
            "Média de 3 meses do desemprego menos a menor dessas médias nos 12 meses "
            "anteriores. Fica negativo quando o desemprego marca nova mínima."
        )

    st.subheader("Pedidos de seguro-desemprego")
    if pedidos.empty:
        faltando("pedidos iniciais de seguro-desemprego")
    else:
        grafico(
            charts.grafico_series(
                [("Semanal", recorte(pedidos)), ("Média de 4 semanas", recorte(pedidos_media))],
                titulo="Pedidos iniciais de seguro-desemprego",
                eixo_y="pedidos por semana",
                cores=[charts.CINZA, charts.US],
                formato=",.0f",
            )
        )
        st.caption(
            "O dado semanal é o primeiro a mostrar demissões; a média de 4 semanas tira o ruído "
            "de feriados e greves."
        )

    esquerda, direita = st.columns(2)
    with esquerda:
        participacao = serie("CIVPART")
        if participacao.empty:
            faltando("taxa de participação")
        else:
            grafico(
                charts.grafico_series(
                    [("Participação", recorte(participacao))],
                    titulo="Taxa de participação",
                    eixo_y="% da população",
                    sufixo="%",
                    cores=[charts.US],
                )
            )
    with direita:
        razao = m.razao(serie("JTSJOL"), serie("UNEMPLOY"))
        if razao.empty:
            faltando("vagas em aberto (JOLTS)")
        else:
            grafico(
                charts.grafico_series(
                    [("Vagas por desempregado", recorte(razao))],
                    titulo="Vagas em aberto por desempregado",
                    eixo_y="vagas / desempregado",
                    cores=[charts.US],
                )
            )
    st.caption(
        "Acima de 1, há mais vagas que desempregados — mercado apertado, pressão sobre "
        "salários. É uma das medidas de folga que o Fed cita."
    )

    st.subheader("Salários contra inflação")
    nucleo_pce = anuais["Núcleo do PCE"]
    if salario.empty:
        faltando("salário médio por hora")
    else:
        linhas = [("Salário por hora", recorte(salario))]
        if not nucleo_pce.empty:
            linhas.append(("Núcleo do PCE", recorte(nucleo_pce)))
        grafico(
            charts.grafico_series(
                linhas,
                titulo="Salário por hora e núcleo do PCE — variação em 12 meses",
                eixo_y="% em 12 meses",
                sufixo="%",
                cores=[charts.US, charts.VERDE],
            )
        )
        st.caption(
            "Salário crescendo acima de ~3,5% ao ano com produtividade normal é difícil de "
            "conciliar com inflação de 2%. A distância entre as linhas aproxima o ganho real."
        )


# ============================================================ PIB — despesa
with aba_despesa:
    pib = serie("GDPC1")
    pib_tri = m.variacao_anualizada(pib, 3)
    pib_anual = m.variacao_anual(pib)
    contribuicoes = m.contribuicoes_despesa({sid: serie(sid) for sid in m.CONTRIBUICOES_DESPESA})

    c1, c2, c3, c4, c5 = st.columns(5)
    indicador(c1, "PIB tri anualizado", pib_tri, ajuda="Taxa trimestral anualizada (SAAR).")
    indicador(c2, "PIB em 12 meses", pib_anual)
    for alvo, (coluna, rotulo) in zip(
        (c3, c4, c5),
        (("consumo", "Consumo"), ("investimento", "Investimento"), ("externo", "Setor externo")),
        strict=True,
    ):
        if contribuicoes.empty:
            indicador(alvo, rotulo, VAZIO)
            continue
        valores = (
            contribuicoes["exportacoes"] + contribuicoes["importacoes"]
            if coluna == "externo"
            else contribuicoes[coluna]
        )
        indicador(
            alvo,
            rotulo,
            pd.DataFrame({"data_ref": contribuicoes["data_ref"], "valor": valores}),
            sufixo=" p.p.",
            casas=2,
            ajuda="Contribuição ao crescimento do PIB, em p.p. da taxa anualizada.",
        )
    st.caption(
        "Consumo, investimento e setor externo (exportações menos importações): quanto "
        "cada um somou ao PIB no último trimestre, em pontos percentuais."
    )

    st.subheader("Quem puxou o PIB no trimestre")
    if contribuicoes.empty:
        faltando("contribuições do BEA ao crescimento do PIB")
    else:
        quadro = contribuicoes.merge(
            pib_tri.rename(columns={"valor": "pib"}), on="data_ref", how="left"
        )
        quadro["pib"] = quadro["pib"].fillna(quadro["soma"])
        grafico(
            charts.grafico_contribuicoes(
                recorte(quadro),
                [(coluna, rotulo, cor) for coluna, (rotulo, _, cor) in DESPESA.items()],
                total=("pib", "PIB (tri anualizado)"),
                titulo="Contribuições ao crescimento do PIB — ótica da despesa",
            )
        )
        ultima = quadro.iloc[-1]
        partes = sorted(
            ((DESPESA[c][0], ultima[c]) for c in DESPESA), key=lambda p: p[1], reverse=True
        )
        st.info(
            f"No trimestre {rotulo_data(pd.Timestamp(ultima['data_ref']), 'trimestral')}, "
            "o PIB cresceu "
            f"{ultima['pib']:.1f}% em ritmo anualizado. Quem mais somou: {partes[0][0].lower()} "
            f"({partes[0][1]:+.2f} p.p.); quem mais tirou: {partes[-1][0].lower()} "
            f"({partes[-1][1]:+.2f} p.p.).",
            icon="🧮",
        )
        st.caption(
            "Cada barra é quanto o componente somou à taxa anualizada do PIB; somadas, dão a "
            "linha. Importações entram negativas — importar mais subtrai do PIB, porque o "
            "gasto que vazou para fora já estava contado no consumo ou no investimento. Os "
            "estoques estão dentro do investimento."
        )
        tabela(
            {
                **{
                    rotulo: quadro[["data_ref", c]].rename(columns={c: "valor"})
                    for c, (rotulo, _, _) in DESPESA.items()
                },
                "PIB": quadro[["data_ref", "pib"]].rename(columns={"pib": "valor"}),
            }
        )

    st.subheader("Crescimento de cada componente em 12 meses")
    niveis = {"PIB": (pib, charts.tinta())} | {
        rotulo: (serie(nivel), cor) for rotulo, nivel, cor in DESPESA.values()
    }
    disponiveis = [nome for nome, (dados, _) in niveis.items() if not dados.empty]
    if not disponiveis:
        faltando("PIB real e componentes")
    else:
        escolhidos = st.pills(
            "Componentes",
            disponiveis,
            selection_mode="multi",
            default=[
                n
                for n in ("PIB", "Consumo das famílias", "Investimento privado")
                if n in disponiveis
            ],
            key="macro_eua_despesa",
        )
        if escolhidos:
            anuais_despesa = {nome: m.variacao_anual(niveis[nome][0]) for nome in escolhidos}
            grafico(
                charts.grafico_series(
                    [(nome, recorte(d)) for nome, d in anuais_despesa.items()],
                    titulo="Variação real em 12 meses",
                    eixo_y="% em 12 meses",
                    sufixo="%",
                    cores=[niveis[nome][1] for nome in escolhidos],
                )
            )
            tabela(anuais_despesa)
        st.caption(
            "Em volume (dólares encadeados de 2017). Investimento e comércio exterior oscilam "
            "muito mais que consumo e governo — é por isso que movem o PIB de um trimestre "
            "para outro, apesar do peso menor."
        )


# =========================================================== PIB — produção
with aba_producao:
    contribuicoes_setor = {sid: serie(sid) for sid in m.CONTRIBUICOES_INDUSTRIA}
    producao = m.contribuicoes_producao(contribuicoes_setor)
    industria = m.detalhe_industria(contribuicoes_setor)

    c1, c2, c3, c4 = st.columns(4)
    for alvo, (coluna, rotulo) in zip(
        (c1, c2, c3, c4),
        (
            ("agropecuaria", "Agropecuária"),
            ("industria", "Indústria"),
            ("servicos", "Serviços"),
            ("total", "PIB"),
        ),
        strict=True,
    ):
        dados = (
            VAZIO
            if producao.empty
            else producao[["data_ref", coluna]].rename(columns={coluna: "valor"})
        )
        if coluna == "total":
            indicador(alvo, "PIB tri anualizado", dados, ajuda="Taxa trimestral anualizada.")
        else:
            indicador(
                alvo,
                rotulo,
                dados,
                sufixo=" p.p.",
                casas=2,
                ajuda="Contribuição ao crescimento do PIB, em p.p. da taxa anualizada.",
            )
    st.caption(
        "Quanto cada setor somou ao PIB no último trimestre, em pontos percentuais — "
        "a soma dos três é o crescimento do PIB."
    )

    st.subheader("Agropecuária, indústria e serviços")
    if producao.empty:
        faltando("PIB por setor (GDP by Industry, BEA)")
    else:
        grafico(
            charts.grafico_contribuicoes(
                recorte(producao),
                [
                    ("agropecuaria", "Agropecuária", charts.VERDE),
                    ("industria", "Indústria", charts.AMBAR),
                    ("servicos", "Serviços", charts.BR),
                ],
                total=("total", "PIB (tri anualizado)"),
                titulo="Contribuições ao crescimento do PIB — ótica da produção",
            )
        )
        st.caption(
            "No recorte do IBGE: indústria = extrativa, transformação, construção e utilities "
            "(eletricidade, gás e água); serviços inclui o governo. O BEA agrupa diferente — "
            "utilities ficam em serviços —, e como as contribuições se somam o remapeamento é "
            "exato. Dados trimestrais do GDP by Industry, divulgados com o PIB."
        )
        tabela(
            {
                rotulo: producao[["data_ref", c]].rename(columns={c: "valor"})
                for c, rotulo in (
                    ("agropecuaria", "Agropecuária"),
                    ("industria", "Indústria"),
                    ("servicos", "Serviços"),
                    ("total", "PIB"),
                )
            }
        )

    st.subheader("Dentro da indústria")
    if industria.empty:
        faltando("abertura da indústria")
    else:
        aberta = industria.assign(
            outras=industria["extrativa"] + industria["utilities"],
            total=industria[["transformacao", "construcao", "extrativa", "utilities"]].sum(axis=1),
        )
        grafico(
            charts.grafico_contribuicoes(
                recorte(aberta),
                [
                    ("construcao", "Construção", charts.US),
                    ("transformacao", "Transformação", charts.ROXO),
                    ("outras", "Extrativa e utilities", charts.AMBAR),
                ],
                total=("total", "Indústria"),
                titulo="Contribuições da indústria ao crescimento do PIB",
            )
        )

    st.subheader("Volume de cada setor")
    volumes = {nome: m.variacao_anual(serie(sid)) for nome, (sid, _) in VOLUME_SETORES.items()}
    disponiveis = [nome for nome, d in volumes.items() if not d.empty]
    if not disponiveis:
        faltando("valor adicionado por setor")
    else:
        escolhidos = st.pills(
            "Setores",
            disponiveis,
            selection_mode="multi",
            default=disponiveis,
            key="macro_eua_setores",
        )
        if escolhidos:
            grafico(
                charts.grafico_series(
                    [(nome, recorte(volumes[nome])) for nome in escolhidos],
                    titulo="Valor adicionado em volume — variação em 12 meses",
                    eixo_y="% em 12 meses",
                    sufixo="%",
                    cores=[VOLUME_SETORES[nome][1] for nome in escolhidos],
                )
            )
            tabela({nome: volumes[nome] for nome in escolhidos})
        st.caption(
            "A contribuição diz quanto o setor moveu o PIB; o volume diz quanto o próprio setor "
            "cresceu. A agropecuária pesa cerca de 1% do PIB americano, então mesmo "
            "variações fortes dela mal aparecem nas contribuições."
        )

    st.subheader("Produção industrial — a leitura mensal")
    producao_industrial = {
        "Produção industrial": (m.variacao_anual(serie("INDPRO")), charts.AMBAR),
        "Transformação": (m.variacao_anual(serie("IPMAN")), charts.ROXO),
    }
    linhas = [(nome, recorte(d)) for nome, (d, _) in producao_industrial.items() if not d.empty]
    if not linhas:
        faltando("produção industrial (Fed)")
    else:
        grafico(
            charts.grafico_series(
                linhas,
                titulo="Produção industrial do Fed — variação em 12 meses",
                eixo_y="% em 12 meses",
                sufixo="%",
                cores=[producao_industrial[nome][1] for nome, _ in linhas],
            )
        )
        st.caption(
            "Cobre transformação, extrativa e utilities, todo mês — o PIB por setor só chega "
            "a cada trimestre. Não inclui construção."
        )

common.rodape()
