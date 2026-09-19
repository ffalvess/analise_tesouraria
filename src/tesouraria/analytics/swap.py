"""Marcação a mercado de um swap CDI × IPCA.

O swap CDI × IPCA é o instrumento em que a inflação implícita deixa de ser
leitura de tela e vira caixa: uma ponta paga IPCA mais um cupom, a outra paga
CDI mais um spread, e o que sobra na diferença é o que o mercado cobra hoje
pela inflação de amanhã.

A conta tem cinco passos, e nenhum deles é a subtração ingênua entre as duas
taxas:

1. **Corrigir o índice até a avaliação.** O último IPCA divulgado cobre o mês
   anterior; do aniversário do contrato até hoje usa-se a projeção mensal
   *pro rata* em dias úteis. Parte da correção, portanto, é dado publicado e
   parte é estimativa — `corrigir_indice` devolve as duas coisas juntas porque
   é assim que elas entram no preço.
2. **Interpolar PRE e DAP** no prazo exato da liquidação, em fator e por
   flat-forward (ver `analytics/fatores.py`).
3. **Extrair a inflação implícita** do período pela razão `Q_PRE / Q_DAP`. É um
   fator do intervalo, não uma taxa anual: anualizar aqui é o erro clássico.
4. **Projetar cada ponta até a liquidação** — a correção e o cupom sobre o
   prazo a que cada um se refere, que não são o mesmo prazo.
5. **Descontar pela PRE** e subtrair.

O passo 5 esconde uma propriedade útil: quando projeção e desconto usam a mesma
curva e o mesmo prazo, `Q_PRE` se cancela nas duas pontas, e o MtM fica imune a
um deslocamento paralelo da curva pré. A sensibilidade que resta é à DAP — ou
seja, à inflação implícita. `cenarios` mostra isso numericamente.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from tesouraria.analytics.fatores import DIAS_UTEIS_ANO, fator

PONTAS = ("cdi", "ipca")


# ------------------------------------------------------- correção do índice


@dataclass(frozen=True)
class CorrecaoIPCA:
    """Correção do IPCA entre a âncora do contrato e a data de avaliação."""

    indice_projetado: float
    fator: float

    @property
    def variacao_pct(self) -> float:
        return (self.fator - 1) * 100.0


def corrigir_indice(
    indice_inicial: float,
    indice_fechado: float,
    projecao_mensal: float,
    du_decorridos: int,
    du_mes: int,
) -> CorrecaoIPCA:
    """Atualiza o número-índice do IPCA até a avaliação e devolve o fator.

    - `indice_inicial`: número-índice da âncora do contrato (I₀).
    - `indice_fechado`: último número-índice divulgado pelo IBGE.
    - `projecao_mensal`: projeção do mês corrente em %, tipicamente da ANBIMA.
    - `du_decorridos` / `du_mes`: dias úteis já passados desde o aniversário e
      total de dias úteis da janela mensal — a fração *pro rata*.

    A defasagem é justamente o ponto delicado: entre o aniversário e hoje não
    existe IPCA divulgado, então o índice é levado adiante por uma estimativa.
    """
    if indice_inicial <= 0 or indice_fechado <= 0:
        raise ValueError("números-índice precisam ser positivos")
    if du_mes <= 0:
        raise ValueError("a janela mensal precisa ter pelo menos um dia útil")
    if not 0 <= du_decorridos <= du_mes:
        raise ValueError(
            f"dias úteis decorridos ({du_decorridos}) fora da janela mensal ({du_mes})"
        )

    projetado = indice_fechado * (1 + projecao_mensal / 100.0) ** (du_decorridos / du_mes)
    return CorrecaoIPCA(indice_projetado=projetado, fator=projetado / indice_inicial)


def fator_cdi_acumulado(
    serie: pd.DataFrame, inicio: dt.date, fim: dt.date, coluna: str = "valor"
) -> float:
    """Fator do CDI acumulado em `[inicio, fim)`, a partir da série diária do SGS.

    A série 12 do SGS traz a taxa **do dia**, em % ao dia, num registro por
    pregão. O acumulado é o produtório de `(1 + taxa/100)`. O intervalo exclui
    a data final pela mesma convenção de `calendario.dias_uteis`: o CDI de hoje
    só é conhecido no fim do dia e remunera o dia seguinte.
    """
    if serie.empty:
        raise ValueError("série de CDI vazia")

    datas = pd.to_datetime(serie["data_ref"]).dt.date
    janela = serie[(datas >= inicio) & (datas < fim)]
    if janela.empty:
        raise ValueError(f"série de CDI sem observações entre {inicio} e {fim}")

    return float((1 + janela[coluna].astype(float) / 100.0).prod())


# --------------------------------------------------------------- contrato


@dataclass(frozen=True)
class Swap:
    """Um swap CDI × IPCA com pagamento único no vencimento.

    - `du_total`: dias úteis do contrato inteiro, do início à liquidação. É o
      prazo do cupom de IPCA e do spread de CDI.
    - `du_restantes`: dias úteis da avaliação até a liquidação. É o prazo que as
      curvas projetam e pelo qual se desconta.
    - `fator_referencia`: correção do IPCA do início até a avaliação (`F_ref`).
    - `fator_cdi`: CDI acumulado do início até a avaliação, já conhecido.

    Os dois prazos servem a funções diferentes, e confundi-los é o erro que
    passa despercebido: as curvas cobrem o que falta, as taxas contratadas
    remuneram o contrato inteiro.
    """

    notional: float
    du_total: int
    du_restantes: int
    cupom_ipca: float
    spread_cdi: float
    fator_referencia: float = 1.0
    fator_cdi: float = 1.0
    ponta_recebida: str = "cdi"

    def __post_init__(self) -> None:
        if self.ponta_recebida not in PONTAS:
            raise ValueError(f"ponta recebida deve ser uma de {PONTAS}: {self.ponta_recebida!r}")
        if self.du_total <= 0:
            raise ValueError("o contrato precisa ter prazo positivo")
        if not 0 < self.du_restantes <= self.du_total:
            raise ValueError(
                f"dias úteis restantes ({self.du_restantes}) fora do prazo "
                f"do contrato ({self.du_total})"
            )


@dataclass(frozen=True)
class ResultadoMtM:
    """O MtM e todos os fatores intermediários que o formaram.

    Guardar os intermediários não é luxo: é o que permite conferir a conta
    linha a linha contra a marcação da contraparte, que é o uso real disto.
    """

    fator_pre: float
    fator_dap: float
    inflacao_implicita: float
    fator_ipca: float
    valor_futuro_ipca: float
    valor_futuro_cdi: float
    valor_presente_ipca: float
    valor_presente_cdi: float
    mtm: float

    @property
    def implicita_pct(self) -> float:
        """Inflação implícita do período, em %. Não é taxa anual."""
        return (self.inflacao_implicita - 1) * 100.0

    @property
    def correcao_pct(self) -> float:
        return (self.fator_ipca - 1) * 100.0


def avaliar(swap: Swap, taxa_pre: float, taxa_dap: float) -> ResultadoMtM:
    """MtM do swap com as taxas PRE e DAP já interpoladas no prazo restante.

    As duas taxas entram em % ao ano, base 252 — o que sai de
    `fatores.taxa_interpolada` sobre os vértices do dia.
    """
    q_pre = fator(taxa_pre, swap.du_restantes)
    q_dap = fator(taxa_dap, swap.du_restantes)

    implicita = q_pre / q_dap
    fator_ipca = swap.fator_referencia * implicita

    juros_cupom = fator(swap.cupom_ipca, swap.du_total)
    juros_spread = fator(swap.spread_cdi, swap.du_total)

    vf_ipca = swap.notional * fator_ipca * juros_cupom
    vf_cdi = swap.notional * swap.fator_cdi * q_pre * juros_spread

    vp_ipca = vf_ipca / q_pre
    vp_cdi = vf_cdi / q_pre

    mtm = vp_cdi - vp_ipca if swap.ponta_recebida == "cdi" else vp_ipca - vp_cdi

    return ResultadoMtM(
        fator_pre=q_pre,
        fator_dap=q_dap,
        inflacao_implicita=implicita,
        fator_ipca=fator_ipca,
        valor_futuro_ipca=vf_ipca,
        valor_futuro_cdi=vf_cdi,
        valor_presente_ipca=vp_ipca,
        valor_presente_cdi=vp_cdi,
        mtm=mtm,
    )


def spread_equivalente(cupom_ipca: float, taxa_dap: float) -> float:
    """Spread de CDI, em % a.a., que iguala as duas pontas na contratação.

    Vem de exigir mesmo valor presente para as duas pernas no início:

        (1 + s)^(du/252) = (1 + c)^(du/252) / (1 + r_dap)^(du/252)

    O prazo se cancela, então o spread de equilíbrio não depende do prazo do
    contrato — só do cupom pedido na ponta IPCA e da DAP daquele vencimento.
    O que a mesa oferece na prática é esse número menos a margem embutida.
    """
    return ((1 + cupom_ipca / 100.0) / (1 + taxa_dap / 100.0) - 1) * 100.0


def cenarios(
    swap: Swap, taxa_pre: float, taxa_dap: float, choque_bps: float = 100.0
) -> pd.DataFrame:
    """MtM no cenário base e com choque paralelo em cada curva, uma de cada vez.

    Serve para ver de onde vem o risco. A linha da PRE reproduz o mesmo MtM do
    base: o fator da pré aparece na projeção e no desconto, e se cancela. O que
    move o resultado é a DAP — isto é, a inflação implícita.
    """
    choque = choque_bps / 100.0
    linhas = [
        ("Base", taxa_pre, taxa_dap),
        (f"PRE +{choque_bps:g} bps", taxa_pre + choque, taxa_dap),
        (f"DAP +{choque_bps:g} bps", taxa_pre, taxa_dap + choque),
    ]

    registros = []
    for nome, pre, dap in linhas:
        resultado = avaliar(swap, pre, dap)
        registros.append(
            {
                "cenario": nome,
                "taxa_pre": pre,
                "taxa_dap": dap,
                "implicita_pct": resultado.implicita_pct,
                "vp_ipca": resultado.valor_presente_ipca,
                "vp_cdi": resultado.valor_presente_cdi,
                "mtm": resultado.mtm,
            }
        )
    return pd.DataFrame(registros)


def anualizar(fator_periodo: float, du: int) -> float:
    """Taxa anual equivalente a um fator de período, em %.

    Existe para quando a comparação com a curva ou com o Focus exigir a mesma
    base. A inflação implícita curta **não** deve ser exibida assim por padrão:
    anualizar 0,8% de 43 dias úteis produz um número que parece precificar
    inflação de 5% ao ano, sem que ninguém tenha dito isso.
    """
    if du <= 0:
        raise ValueError(f"prazo em dias úteis precisa ser positivo: {du}")
    return (fator_periodo ** (DIAS_UTEIS_ANO / du) - 1) * 100.0
