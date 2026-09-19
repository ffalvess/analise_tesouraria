"""Testes de fatores acumulados e do MtM de um swap CDI × IPCA.

O grosso deste arquivo confere a implementação contra **um exemplo completo e
publicado**: um swap hipotético de R$ 10 milhões que recebe CDI + 0,28% e paga
IPCA + 10%, contratado em 15/05/2026 e marcado em 18/09/2026 com as curvas DI1
e DAP daquele pregão. O autor publicou cada passo intermediário — índice
projetado, fatores interpolados, inflação implícita, valores futuros, valores
presentes, MtM e dois cenários de choque —, o que transforma o material num
conjunto raro: uma bateria de valores de referência conferidos fora daqui.

**Tolerâncias.** O exemplo avisa que "a precisão foi reduzida apenas na
apresentação dos números": as taxas aparecem com quatro casas e os fatores com
oito, enquanto a conta original usou os valores cheios. Recalcular a partir dos
números publicados devolve diferenças de até 6e-7 em fator e de R$ 2 em
R$ 10 milhões — um erro relativo de 2e-7, que é o arredondamento da
apresentação e não do método. As tolerâncias abaixo são exatamente essas.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tesouraria.analytics import calendario as cal
from tesouraria.analytics import fatores as fat
from tesouraria.analytics import swap as sw

# Tolerâncias: ver a nota sobre arredondamento no cabeçalho.
FATOR = 1e-6
REAIS = 2.00

NOTIONAL = 10_000_000.0
INICIO = dt.date(2026, 5, 15)
AVALIACAO = dt.date(2026, 9, 18)
LIQUIDACAO = dt.date(2026, 11, 23)

# Vértices de DI1 e DAP no pregão de 18/09/2026, como publicados no exemplo.
PRE = [
    fat.Vertice(du=30, taxa=13.6550, rotulo="DI1X26"),
    fat.Vertice(du=49, taxa=13.5880, rotulo="DI1Z26"),
]
DAP = [
    fat.Vertice(du=39, taxa=8.3150, rotulo="DAPX26"),
    fat.Vertice(du=59, taxa=8.1150, rotulo="DAPZ26"),
]

# CDI acumulado de 15/05 a 18/09, já conhecido na avaliação.
FATOR_CDI = 1.04722628


# --------------------------------------------------------------- fatores


def test_fator_e_taxa_sao_inversos():
    assert fat.fator(13.6550, 30) == pytest.approx(1.01535449, abs=FATOR)
    assert fat.taxa_de_fator(1.01535449, 30) == pytest.approx(13.6550, abs=1e-4)


def test_fatores_dos_vertices_publicados():
    assert PRE[0].fator == pytest.approx(1.01535449, abs=FATOR)
    assert PRE[1].fator == pytest.approx(1.02508317, abs=FATOR)
    assert DAP[0].fator == pytest.approx(1.01243810, abs=FATOR)
    assert DAP[1].fator == pytest.approx(1.01843572, abs=FATOR)


def test_interpolacao_flat_forward_reproduz_o_vertice_da_liquidacao():
    """43 d.u. entre 30 e 49 (PRE) e entre 39 e 59 (DAP)."""
    assert fat.interpolar(PRE, 43) == pytest.approx(1.02200092, abs=FATOR)
    assert fat.interpolar(DAP, 43) == pytest.approx(1.01363479, abs=FATOR)
    assert fat.taxa_interpolada(PRE, 43) == pytest.approx(13.6027, abs=1e-3)
    assert fat.taxa_interpolada(DAP, 43) == pytest.approx(8.2600, abs=1e-3)


def test_interpolar_em_cima_do_vertice_nao_interpola():
    assert fat.interpolar(PRE, 30) == PRE[0].fator
    assert fat.interpolar(PRE, 49) == PRE[1].fator


def test_interpolar_nao_extrapola():
    """Fora dos vértices negociados o preço seria inventado — então falha."""
    with pytest.raises(ValueError, match="não extrapolamos"):
        fat.interpolar(PRE, 60)
    with pytest.raises(ValueError, match="não extrapolamos"):
        fat.interpolar(PRE, 10)


def test_flat_forward_mantem_taxa_a_termo_constante():
    """Com a mesma taxa nos dois vértices, qualquer prazo no meio repete a taxa."""
    plana = [fat.Vertice(du=30, taxa=12.0), fat.Vertice(du=90, taxa=12.0)]
    assert fat.taxa_interpolada(plana, 55) == pytest.approx(12.0, abs=1e-12)


# ----------------------------------------------------- correção do índice


def test_corrigir_indice_com_pro_rata_em_dias_uteis():
    """Passo 1 do exemplo: IPCA de agosto fechado, mais 3/21 da projeção de 0,56%."""
    du_decorridos = cal.dias_uteis(dt.date(2026, 9, 15), AVALIACAO)
    du_mes = cal.dias_uteis(dt.date(2026, 9, 15), dt.date(2026, 10, 15))
    assert (du_decorridos, du_mes) == (3, 21)

    correcao = sw.corrigir_indice(
        indice_inicial=7596.09,
        indice_fechado=7633.23,
        projecao_mensal=0.56,
        du_decorridos=du_decorridos,
        du_mes=du_mes,
    )

    assert correcao.indice_projetado == pytest.approx(7639.32197880, abs=1e-6)
    assert correcao.fator == pytest.approx(1.00569134, abs=FATOR)


def test_corrigir_indice_recusa_pro_rata_fora_da_janela():
    with pytest.raises(ValueError, match="fora da janela mensal"):
        sw.corrigir_indice(7596.09, 7633.23, 0.56, du_decorridos=25, du_mes=21)


def test_fator_cdi_acumulado_exclui_a_data_final():
    serie = pd.DataFrame(
        {
            "data_ref": [dt.date(2026, 5, 15), dt.date(2026, 5, 18), dt.date(2026, 5, 19)],
            "valor": [0.05, 0.05, 0.05],
        }
    )
    # Só os dois primeiros dias entram: 19/05 é a data final, exclusive.
    esperado = 1.0005**2
    assert sw.fator_cdi_acumulado(serie, dt.date(2026, 5, 15), dt.date(2026, 5, 19)) == (
        pytest.approx(esperado, abs=1e-12)
    )


# ------------------------------------------------------------------ MtM


@pytest.fixture
def contrato() -> sw.Swap:
    return sw.Swap(
        notional=NOTIONAL,
        du_total=cal.dias_uteis(INICIO, LIQUIDACAO),
        du_restantes=cal.dias_uteis(AVALIACAO, LIQUIDACAO),
        cupom_ipca=10.0,
        spread_cdi=0.28,
        fator_referencia=1.00569134,
        fator_cdi=FATOR_CDI,
        ponta_recebida="cdi",
    )


def test_prazos_do_contrato(contrato):
    assert contrato.du_total == 131
    assert contrato.du_restantes == 43


def test_mtm_reproduz_o_exemplo_publicado(contrato):
    resultado = sw.avaliar(
        contrato,
        taxa_pre=fat.taxa_interpolada(PRE, contrato.du_restantes),
        taxa_dap=fat.taxa_interpolada(DAP, contrato.du_restantes),
    )

    # Passo 2: inflação implícita do período, pela razão dos fatores.
    assert resultado.inflacao_implicita == pytest.approx(1.00825359, abs=FATOR)
    assert resultado.implicita_pct == pytest.approx(0.8253, abs=1e-3)
    assert resultado.fator_ipca == pytest.approx(1.01399191, abs=FATOR)

    # Passo 3: valores futuros na liquidação.
    assert resultado.valor_futuro_ipca == pytest.approx(10_654_967.24, abs=REAIS)
    assert resultado.valor_futuro_cdi == pytest.approx(10_718_230.13, abs=REAIS)
    assert resultado.valor_futuro_cdi - resultado.valor_futuro_ipca == (
        pytest.approx(63_262.89, abs=REAIS)
    )

    # Passo 4: trazidos a valor presente pela PRE.
    assert resultado.valor_presente_ipca == pytest.approx(10_425_594.48, abs=REAIS)
    assert resultado.valor_presente_cdi == pytest.approx(10_487_495.49, abs=REAIS)

    # Passo 5: o MtM, positivo para quem recebe CDI.
    assert resultado.mtm == pytest.approx(61_901.01, abs=REAIS)


def test_implicita_do_periodo_nao_e_anualizada(contrato):
    """0,8253% em 43 d.u. equivale a ~5% a.a.: exibir a anual como se fosse a do
    período — ou o contrário — erra por um fator de seis."""
    resultado = sw.avaliar(contrato, taxa_pre=13.6027, taxa_dap=8.2600)
    anual = sw.anualizar(resultado.inflacao_implicita, contrato.du_restantes)

    assert resultado.implicita_pct == pytest.approx(0.8253, abs=1e-3)
    assert anual == pytest.approx(4.9351, abs=1e-2)


def test_inverter_a_ponta_recebida_inverte_o_sinal(contrato):
    recebendo_ipca = sw.Swap(
        notional=contrato.notional,
        du_total=contrato.du_total,
        du_restantes=contrato.du_restantes,
        cupom_ipca=contrato.cupom_ipca,
        spread_cdi=contrato.spread_cdi,
        fator_referencia=contrato.fator_referencia,
        fator_cdi=contrato.fator_cdi,
        ponta_recebida="ipca",
    )
    por_ponta = {
        ponta.ponta_recebida: sw.avaliar(ponta, 13.6027, 8.2600).mtm
        for ponta in (contrato, recebendo_ipca)
    }
    assert por_ponta["cdi"] == pytest.approx(-por_ponta["ipca"], abs=1e-9)


def test_swap_recusa_prazo_restante_maior_que_o_contrato():
    with pytest.raises(ValueError, match="fora do prazo"):
        sw.Swap(notional=NOTIONAL, du_total=43, du_restantes=131, cupom_ipca=10.0, spread_cdi=0.28)


# -------------------------------------------------------------- cenários


def test_spread_equivalente_na_contratacao():
    """Seção 3 do exemplo: DAP de 9,4759% em 131 d.u. contra IPCA + 10%."""
    vertices = [
        fat.Vertice(du=127, taxa=9.5140, rotulo="DAPX26"),
        fat.Vertice(du=168, taxa=9.2099, rotulo="DAPF27"),
    ]
    taxa_dap = fat.taxa_interpolada(vertices, 131)
    assert taxa_dap == pytest.approx(9.4759, abs=1e-3)
    assert sw.spread_equivalente(10.0, taxa_dap) == pytest.approx(0.4787, abs=1e-3)


def test_spread_equivalente_independe_do_prazo():
    """O prazo se cancela nos dois lados da igualdade de valor presente."""
    swap_curto = sw.Swap(NOTIONAL, 131, 43, 10.0, sw.spread_equivalente(10.0, 9.4759))
    swap_longo = sw.Swap(NOTIONAL, 500, 43, 10.0, sw.spread_equivalente(10.0, 9.4759))
    assert swap_curto.spread_cdi == swap_longo.spread_cdi


def test_cenarios_reproduzem_o_choque_publicado(contrato):
    quadro = sw.cenarios(contrato, taxa_pre=13.6027, taxa_dap=8.2600, choque_bps=100.0)
    base, pre, dap = (quadro.iloc[i] for i in range(3))

    assert base["implicita_pct"] == pytest.approx(0.8253, abs=1e-3)
    assert pre["implicita_pct"] == pytest.approx(0.9762, abs=1e-3)
    assert dap["implicita_pct"] == pytest.approx(0.6672, abs=1e-3)

    assert base["vp_ipca"] == pytest.approx(10_425_594.48, abs=REAIS)
    assert dap["vp_ipca"] == pytest.approx(10_409_250.35, abs=REAIS)

    assert base["mtm"] == pytest.approx(61_901.01, abs=REAIS)
    assert dap["mtm"] == pytest.approx(78_245.14, abs=REAIS)


def test_alta_paralela_da_pre_nao_muda_o_mtm(contrato):
    """O fator da pré projeta e desconta o mesmo prazo, então se cancela.

    É a propriedade menos intuitiva da marcação deste swap, e a que justifica
    dizer que a exposição é à inflação implícita, não ao nível da curva pré.
    """
    quadro = sw.cenarios(contrato, taxa_pre=13.6027, taxa_dap=8.2600)
    assert quadro.loc[1, "mtm"] == pytest.approx(quadro.loc[0, "mtm"], abs=1e-6)
    assert quadro.loc[1, "vp_ipca"] == pytest.approx(quadro.loc[0, "vp_ipca"], abs=1e-6)

    # Já a DAP move o resultado — e move a ponta paga, então o MtM sobe.
    assert quadro.loc[2, "mtm"] > quadro.loc[0, "mtm"]
