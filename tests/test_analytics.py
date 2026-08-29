"""Testes de diferenciais, fluxo cambial e análise de tom."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tesouraria.analytics import differentials as dif
from tesouraria.analytics import fxflow, tone

# ------------------------------------------------------------- diferenciais


def quadro_curva(prazos, taxas) -> pd.DataFrame:
    return pd.DataFrame({"prazo_anos": prazos, "taxa": taxas})


def test_curva_efetiva_converte_pela_convencao_do_pais():
    """A mesma taxa nominal produz curvas diferentes conforme o país."""
    br = dif.curva_efetiva(quadro_curva([1.0, 5.0], [4.5, 4.5]), "BR")
    us = dif.curva_efetiva(quadro_curva([1.0, 5.0], [4.5, 4.5]), "US")

    assert br.taxas[0] == pytest.approx(4.5)          # já é efetiva anual
    assert us.taxas[0] == pytest.approx(4.550625)     # semestral -> efetiva


def test_diferencial_por_vertice_com_valores_conhecidos():
    br = dif.curva_efetiva(quadro_curva([1.0, 2.0, 5.0], [14.0, 14.0, 14.0]), "BR")
    us = dif.curva_efetiva(quadro_curva([1.0, 2.0, 5.0], [4.0, 4.0, 4.0]), "US")

    resultado = dif.diferencial_por_vertice(br, us, prazos=[1.0, 2.0, 5.0])

    # 14% menos (1,02^2 - 1) = 14 - 4,04 = 9,96 p.p.
    esperado = 14.0 - ((1 + 0.04 / 2) ** 2 - 1) * 100
    np.testing.assert_allclose(resultado["diferencial_pp"], [esperado] * 3, rtol=1e-9)
    np.testing.assert_allclose(resultado["diferencial_bps"], [esperado * 100] * 3, rtol=1e-9)


def test_diferencial_fora_do_intervalo_e_nan():
    """Sem observação num vértice, o diferencial fica ausente — nunca zero."""
    br = dif.curva_efetiva(quadro_curva([1.0, 2.0, 5.0], [14.0, 13.8, 13.5]), "BR")
    us = dif.curva_efetiva(quadro_curva([1.0, 2.0, 30.0], [4.0, 4.1, 4.5]), "US")

    resultado = dif.diferencial_por_vertice(br, us, prazos=[2.0, 30.0])
    assert np.isfinite(resultado["diferencial_pp"].iloc[0])
    assert np.isnan(resultado["diferencial_pp"].iloc[1])  # BR não chega a 30 anos


def test_inflacao_implicita_usa_fisher_exato():
    nominal = dif.curva_efetiva(quadro_curva([5.0, 10.0], [14.0, 14.0]), "BR")
    real = dif.curva_efetiva(quadro_curva([5.0, 10.0], [7.0, 7.0]), "BR")

    resultado = dif.inflacao_implicita(nominal, real, prazos=[5.0, 10.0])

    esperado = (1.14 / 1.07 - 1) * 100  # ≈ 6,542%, não os 7,0 da subtração simples
    assert resultado["implicita"].iloc[0] == pytest.approx(esperado, abs=1e-9)
    assert resultado["implicita"].iloc[0] < 7.0


def test_serie_diferencial_casa_apenas_datas_comuns():
    """Um feriado em um dos países não pode virar movimento falso."""
    br = pd.DataFrame(
        {"data_ref": [dt.date(2026, 8, 26), dt.date(2026, 8, 27), dt.date(2026, 8, 28)],
         "taxa": [14.0, 14.1, 14.2]}
    )
    us = pd.DataFrame(
        {"data_ref": [dt.date(2026, 8, 26), dt.date(2026, 8, 28)], "taxa": [4.0, 4.2]}
    )
    resultado = dif.serie_diferencial(br, us)

    assert len(resultado) == 2
    assert list(resultado["data_ref"]) == [dt.date(2026, 8, 26), dt.date(2026, 8, 28)]


def test_serie_diferencial_com_entrada_vazia():
    assert dif.serie_diferencial(pd.DataFrame(), pd.DataFrame()).empty


def test_carry_curto_anualiza_o_cdi():
    """CDI diário de 0,05% em 252 dias úteis dá ~13,4% ao ano."""
    cdi = pd.DataFrame({"data_ref": [dt.date(2026, 8, 28)], "valor": [0.05]})
    fed = pd.DataFrame({"data_ref": [dt.date(2026, 8, 1)], "valor": [4.3]})

    resultado = dif.carry_curto(cdi, fed)
    esperado = ((1.0005) ** 252 - 1) * 100

    assert resultado["cdi_anual"].iloc[0] == pytest.approx(esperado, abs=1e-9)
    assert resultado["carry_pp"].iloc[0] == pytest.approx(esperado - 4.3, abs=1e-9)


# ------------------------------------------------------------- fluxo cambial


def base_de_teste(semanas: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    datas = pd.date_range("2025-01-03", periods=semanas, freq="W-FRI")
    gerador = np.random.default_rng(7)

    # Câmbio construído para responder negativamente ao fluxo, com ruído.
    saldo = gerador.normal(0, 1000, semanas)
    retorno = -saldo * 0.001 + gerador.normal(0, 0.2, semanas)
    cotacao = 5.0 * np.exp(np.cumsum(retorno / 100))

    fluxo = pd.DataFrame(
        {
            "data_ref": datas.date,
            "periodicidade": "semanal",
            "segmento": "total",
            "compras": 10_000.0,
            "vendas": 10_000.0 - saldo,
            "saldo": saldo,
        }
    )
    cambio = pd.DataFrame({"data_ref": datas.date, "valor": cotacao})
    return fluxo, cambio


def test_base_semanal_alinha_as_series():
    fluxo, cambio = base_de_teste()
    base = fxflow.base_semanal(fluxo, cambio, "total")

    assert not base.empty
    assert {"data_ref", "saldo", "cambio", "var_cambio"} <= set(base.columns)
    assert base["var_cambio"].notna().all()


def test_base_semanal_com_segmento_inexistente():
    fluxo, cambio = base_de_teste()
    assert fxflow.base_semanal(fluxo, cambio, "financeiro").empty


def test_regressao_encontra_o_sinal_negativo():
    """Com fluxo e câmbio construídos em relação inversa, o beta tem de ser negativo."""
    fluxo, cambio = base_de_teste(120)
    resultado = fxflow.regressao(fxflow.base_semanal(fluxo, cambio, "total"))

    assert "erro" not in resultado
    beta = resultado["coeficientes"].set_index("variavel").loc["saldo", "coeficiente"]
    assert beta < 0
    assert resultado["r2"] > 0.5
    assert "queda" in resultado["leitura"]


def test_regressao_recusa_amostra_curta():
    fluxo, cambio = base_de_teste(6)
    resultado = fxflow.regressao(fxflow.base_semanal(fluxo, cambio, "total"))
    assert "erro" in resultado


def test_beta_movel_precisa_da_janela_completa():
    fluxo, cambio = base_de_teste(30)
    base = fxflow.base_semanal(fluxo, cambio, "total")

    assert fxflow.beta_movel(base, janela=52).empty
    beta = fxflow.beta_movel(base, janela=13)
    assert not beta.empty
    assert beta["beta"].median() < 0


def test_acumulados_reiniciam_a_cada_periodo():
    fluxo, _ = base_de_teste(60)
    resultado = fxflow.acumulados(fluxo, "total")

    por_mes = resultado.groupby(resultado["data_ref"].dt.to_period("M"))
    for _, grupo in por_mes:
        # O primeiro acumulado de cada mês é o próprio saldo da semana.
        assert grupo["acum_mes"].iloc[0] == pytest.approx(grupo["saldo"].iloc[0])


def test_resumo_por_segmento():
    fluxo, _ = base_de_teste(40)
    resumo = fxflow.resumo_por_segmento(fluxo, semanas=12)
    assert list(resumo["segmento"]) == ["total"]
    assert resumo["semanas"].iloc[0] <= 13


# --------------------------------------------------------------------- tom


HAWK_PT = (
    "O Comitê avalia que o cenário exige política monetária em terreno contracionista "
    "por período bastante prolongado, com expectativas desancoradas e riscos de alta."
)
DOVE_PT = (
    "O processo desinflacionário segue em curso, com expectativas ancoradas e "
    "arrefecimento da atividade, o que abre espaço para o ciclo de cortes."
)
HAWK_EN = (
    "A restrictive stance remains appropriate and additional firming may be needed, "
    "given upside risks to inflation and a tight labor market."
)
DOVE_EN = (
    "Disinflation has broadened and the labor market is cooling, with inflation "
    "expectations well anchored, so an easing cycle is appropriate."
)


def test_tom_hawkish_e_positivo():
    assert tone.pontuar(HAWK_PT, "pt").score > 0.5
    assert tone.pontuar(HAWK_EN, "en").score > 0.5


def test_tom_dovish_e_negativo():
    assert tone.pontuar(DOVE_PT, "pt").score < -0.5
    assert tone.pontuar(DOVE_EN, "en").score < -0.5


def test_tom_neutro_fica_perto_de_zero():
    texto = "O relatório apresenta os dados coletados nas últimas semanas pela equipe."
    resultado = tone.pontuar(texto, "pt")
    assert resultado.score == 0.0
    assert resultado.rotulo == "neutro"


def test_texto_vazio():
    assert tone.pontuar("", "pt").score == 0.0
    assert tone.pontuar("   ", "pt").n_hawk == 0


def test_negacao_inverte_o_lado():
    """'não haverá aperto monetário' não pode contar como hawkish."""
    com_negacao = tone.pontuar("Não haverá aperto monetário neste ciclo.", "pt")
    sem_negacao = tone.pontuar("Haverá aperto monetário neste ciclo.", "pt")

    assert sem_negacao.score > 0
    assert com_negacao.score < 0


def test_expressao_composta_tem_prioridade():
    """'riscos de alta' consome os tokens antes que 'alta' os pegue sozinha."""
    resultado = tone.pontuar("Os riscos de alta seguem presentes.", "pt")
    assert resultado.n_hawk == 1


def test_acentuacao_nao_altera_o_resultado():
    com = tone.pontuar("O processo desinflacionário segue.", "pt")
    sem = tone.pontuar("O processo desinflacionario segue.", "pt")
    assert com.score == sem.score


def test_idioma_desconhecido_cai_no_portugues():
    assert tone.pontuar(HAWK_PT, "es").score > 0.5


def test_pontuar_quadro_respeita_o_idioma_de_cada_linha():
    df = pd.DataFrame(
        {"texto": [HAWK_PT, DOVE_EN], "idioma": ["pt", "en"]}
    )
    resultado = tone.pontuar_quadro(df)
    assert resultado["score_tom"].iloc[0] > 0
    assert resultado["score_tom"].iloc[1] < 0


def test_serie_de_tom_calcula_media_movel_por_instituicao():
    df = pd.DataFrame(
        {
            "data_pub": pd.date_range("2026-01-01", periods=6, freq="W"),
            "instituicao": ["BCB", "Fed"] * 3,
            "autor": ["a"] * 6,
            "titulo": ["t"] * 6,
            "score_tom": [1.0, -1.0, 1.0, -1.0, 1.0, -1.0],
        }
    )
    serie = tone.serie_de_tom(df, janela=3)

    bcb = serie[serie["instituicao"] == "BCB"]["media_movel"]
    assert (bcb == 1.0).all()
