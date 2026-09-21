"""Testes das janelas de tempo compartilhadas pelos gráficos de série.

O recorte parece trivial, mas a âncora não é: ancorar em `hoje` encolhe a
janela todo fim de semana e em todo dia em que a coleta ainda não rodou, o que
faz o gráfico parecer desatualizado quando só faltou pregão.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tesouraria.ui import common


def serie_diaria(inicio: dt.date, dias: int, taxa_inicial: float = 10.0) -> pd.DataFrame:
    """Série de pregões consecutivos, uma observação por dia útil."""
    datas = pd.bdate_range(inicio, periods=dias)
    return pd.DataFrame(
        {"data_ref": datas, "taxa": [taxa_inicial + i * 0.01 for i in range(len(datas))]}
    )


def test_periodos_cobrem_as_janelas_curtas():
    assert list(common.PERIODOS) == ["1 semana", "1 mês", "3 meses", "6 meses", "1 ano", "Tudo"]
    assert common.PERIODOS["1 semana"] == 7
    assert common.PERIODOS["1 ano"] == 365
    assert common.PERIODOS["Tudo"] is None


def test_recorte_ancora_na_ultima_observacao_e_nao_em_hoje():
    """Uma série que parou há um mês ainda devolve uma semana inteira de dados."""
    parada = serie_diaria(dt.date.today() - dt.timedelta(days=60), 40)
    recortada = common.recortar_periodo(parada, common.PERIODOS["1 semana"])

    assert not recortada.empty
    ultima = pd.to_datetime(parada["data_ref"]).max()
    assert (ultima - pd.to_datetime(recortada["data_ref"]).min()).days <= 7
    assert pd.to_datetime(recortada["data_ref"]).max() == ultima


def test_recorte_de_uma_semana_pega_so_os_pregoes_da_semana():
    serie = serie_diaria(dt.date(2026, 1, 1), 60)
    recortada = common.recortar_periodo(serie, 7)

    # Sete dias corridos contêm cinco pregões, mais o do próprio dia de corte.
    assert 5 <= len(recortada) <= 6
    assert len(recortada) < len(serie)


def test_janelas_maiores_contem_as_menores():
    serie = serie_diaria(dt.date(2024, 1, 1), 500)
    tamanhos = [
        len(common.recortar_periodo(serie, common.PERIODOS[nome]))
        for nome in ["1 semana", "1 mês", "3 meses", "6 meses", "1 ano", "Tudo"]
    ]
    assert tamanhos == sorted(tamanhos)
    assert tamanhos[-1] == len(serie)


def test_recorte_sem_janela_ou_sem_dados():
    serie = serie_diaria(dt.date(2026, 1, 1), 10)
    assert len(common.recortar_periodo(serie, None)) == len(serie)

    vazia = pd.DataFrame(columns=["data_ref", "taxa"])
    assert common.recortar_periodo(vazia, 30).empty


def test_resumo_do_periodo():
    serie = pd.DataFrame(
        {
            "data_ref": pd.bdate_range(dt.date(2026, 1, 1), periods=4),
            "taxa": [10.0, 10.5, 9.8, 10.25],
        }
    )
    resumo = common.resumo_periodo(serie)

    assert resumo["atual"] == pytest.approx(10.25)
    # Do primeiro ao último ponto da janela: +0,25 p.p. = +25 bps.
    assert resumo["variacao_bps"] == pytest.approx(25.0)
    assert resumo["minimo"] == pytest.approx(9.8)
    assert resumo["maximo"] == pytest.approx(10.5)


def test_resumo_usa_a_janela_recortada_e_nao_a_serie_inteira():
    """A variação exibida tem de ser a do período escolhido."""
    serie = pd.DataFrame(
        {
            "data_ref": pd.bdate_range(dt.date(2026, 1, 1), periods=40),
            # O degrau fica fora da janela de uma semana de propósito: o
            # resumo curto não pode enxergar um movimento de um mês atrás.
            "taxa": [10.0] * 30 + [11.0] * 10,
        }
    )
    completa = common.resumo_periodo(serie)
    janela = common.resumo_periodo(common.recortar_periodo(serie, 7))

    assert completa["variacao_bps"] == pytest.approx(100.0)
    assert janela["variacao_bps"] == pytest.approx(0.0)
