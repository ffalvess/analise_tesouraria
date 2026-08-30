"""Testes do prêmio do real.

O que precisa ficar travado aqui é o **sinal** e o **significado** das duas
medidas. Um prêmio com o sinal invertido não parece errado num gráfico: parece
uma tese. Por isso quase todo teste abaixo constrói um cenário de resposta
conhecida — o real seguindo a cesta, andando o dobro, ou se descolando por um
valor exato — e confere o número que sai.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tesouraria.analytics import currency


def cenario(retornos_cesta, beta_real=1.0, ruido=0.0, semente=3) -> pd.DataFrame:
    """Monta USD/BRL e cesta com uma relação conhecida entre eles."""
    gerador = np.random.default_rng(semente)
    datas = pd.bdate_range("2024-01-01", periods=len(retornos_cesta))

    r_cesta = np.asarray(retornos_cesta, dtype=float)
    r_brl = beta_real * r_cesta + gerador.normal(0, ruido, len(r_cesta))

    cesta = 100 * np.exp(np.cumsum(r_cesta / 100))
    usdbrl = 5.0 * np.exp(np.cumsum(r_brl / 100))

    return currency.alinhar(
        pd.DataFrame({"data_ref": datas.date, "valor": usdbrl}),
        pd.DataFrame({"data_ref": datas.date, "valor": cesta}),
    )


# ------------------------------------------------------------- alinhamento


def test_alinhar_usa_apenas_datas_comuns():
    """Feriado de um lado não pode virar um dia de variação zero."""
    cambio = pd.DataFrame(
        {"data_ref": pd.to_datetime(["2026-08-26", "2026-08-27", "2026-08-28"]).date,
         "valor": [5.0, 5.1, 5.2]}
    )
    cesta = pd.DataFrame(
        {"data_ref": pd.to_datetime(["2026-08-26", "2026-08-28"]).date, "valor": [100.0, 101.0]}
    )

    base = currency.alinhar(cambio, cesta)
    assert len(base) == 2
    assert list(pd.to_datetime(base["data_ref"]).dt.strftime("%Y-%m-%d")) == [
        "2026-08-26", "2026-08-28"
    ]


def test_alinhar_com_entrada_vazia():
    assert currency.alinhar(pd.DataFrame(), pd.DataFrame()).empty


# ------------------------------------------------------------ prêmio simples


def test_real_acompanhando_a_cesta_nao_tem_premio():
    """Se o real anda exatamente com a cesta, não há nada específico do Brasil."""
    base = cenario([0.5, -0.3, 0.8, -0.2, 0.4] * 12, beta_real=1.0)
    resultado = currency.calcular(base, "semana", "avançadas")

    assert resultado.premio_simples == pytest.approx(0.0, abs=1e-6)


def test_real_andando_o_dobro_gera_premio_positivo():
    """Beta 2: o real cai mais que a cesta, e o prêmio simples acusa."""
    base = cenario([0.5] * 60, beta_real=2.0)
    resultado = currency.calcular(base, "semana", "emergentes")

    assert resultado.var_brl > resultado.var_cesta > 0
    assert resultado.premio_simples > 0


def test_real_melhor_que_a_cesta_gera_premio_negativo():
    base = cenario([1.0] * 60, beta_real=0.4)
    resultado = currency.calcular(base, "semana", "avançadas")

    assert resultado.premio_simples < 0
    assert "melhor" in resultado.leitura


def test_premio_simples_e_a_diferenca_exata():
    base = cenario([0.7, -0.4, 0.2] * 25, beta_real=1.5, ruido=0.1)
    resultado = currency.calcular(base, "mês", "avançadas")

    assert resultado.premio_simples == pytest.approx(
        resultado.var_brl - resultado.var_cesta, abs=1e-9
    )


# --------------------------------------------------------- prêmio por beta


def test_beta_estimado_recupera_o_verdadeiro():
    base = cenario(list(np.random.default_rng(1).normal(0, 0.6, 400)), beta_real=1.8, ruido=0.05)
    beta, r2 = currency.estimar_beta(base)

    assert beta == pytest.approx(1.8, abs=0.1)
    assert r2 > 0.9


def test_premio_por_beta_zera_o_excesso_estrutural():
    """O ponto do ajuste: alto beta não é prêmio de risco.

    O real anda o dobro da cesta e nada mais acontece. O prêmio simples fica
    positivo — e enganaria —, enquanto o ajustado reconhece que o movimento era
    exatamente o previsto pela sensibilidade histórica.
    """
    base = cenario(list(np.random.default_rng(2).normal(0, 0.6, 400)), beta_real=2.0, ruido=0.02)
    resultado = currency.calcular(base, "mês", "emergentes")

    assert abs(resultado.premio_simples) > abs(resultado.premio_beta)
    assert resultado.premio_beta == pytest.approx(0.0, abs=0.6)


def test_beta_exige_amostra_minima():
    base = cenario([0.4] * 10)
    beta, r2 = currency.estimar_beta(base)

    assert np.isnan(beta) and np.isnan(r2)


def test_sem_beta_o_premio_ajustado_cai_no_simples():
    base = cenario([0.5, -0.2] * 6, beta_real=1.7)
    resultado = currency.calcular(base, "semana", "avançadas")

    assert np.isnan(resultado.beta)
    assert np.isfinite(resultado.premio_beta)


# ------------------------------------------------------------------ janelas


@pytest.mark.parametrize(("janela", "dias"), [("dia", 1), ("semana", 7), ("mês", 30)])
def test_janela_seleciona_o_intervalo_certo(janela, dias):
    base = cenario(list(np.random.default_rng(4).normal(0, 0.5, 200)))
    resultado = currency.calcular(base, janela)

    intervalo = (pd.Timestamp(resultado.fim) - pd.Timestamp(resultado.inicio)).days
    # O início é o último pregão até o corte, então o intervalo cobre a janela
    # sem ficar muito além dela (fim de semana e feriado esticam um pouco).
    assert dias <= intervalo <= dias + 5


def test_janela_do_dia_compara_com_o_pregao_anterior():
    base = cenario([0.0] * 30 + [1.0])
    resultado = currency.calcular(base, "dia")

    assert resultado.observacoes == 1
    assert resultado.var_cesta == pytest.approx(np.expm1(0.01) * 100, abs=1e-6)


def test_base_curta_demais_devolve_vazio():
    base = cenario([0.5])
    resultado = currency.calcular(base, "mês")

    assert np.isnan(resultado.premio_simples)
    assert "insuficientes" in resultado.leitura


# -------------------------------------------------------- série e normalização


def test_serie_premio_acumula():
    base = cenario(list(np.random.default_rng(5).normal(0, 0.6, 400)), beta_real=1.5, ruido=0.3)
    serie = currency.serie_premio(base)

    assert not serie.empty
    assert {"data_ref", "premio_dia", "premio_acumulado", "beta"} == set(serie.columns)
    np.testing.assert_allclose(
        serie["premio_acumulado"].to_numpy(), serie["premio_dia"].cumsum().to_numpy(), rtol=1e-9
    )


def test_serie_premio_exige_historico():
    assert currency.serie_premio(cenario([0.3] * 50)).empty


def test_normalizar_poe_as_duas_series_em_base_100():
    base = cenario([0.5, -0.3, 0.8] * 20, beta_real=1.4)
    out = currency.normalizar(base)

    assert out["usdbrl_100"].iloc[0] == pytest.approx(100.0)
    assert out["cesta_100"].iloc[0] == pytest.approx(100.0)


def test_leitura_descreve_o_sinal():
    base = cenario([0.5] * 60, beta_real=2.5)
    leitura = currency.calcular(base, "semana", "emergentes").leitura

    assert "além" in leitura
    assert "emergentes" in leitura
