"""Testes da validação de plausibilidade das séries.

Esta camada nasceu de um erro real: códigos do SGS anotados errado devolveram
séries válidas e sem sentido — "exportações" com valores negativos de seis
dígitos, "reservas internacionais" sete vezes maiores que as reservas do país.
Nada falhou, nada avisou, os gráficos desenharam.

Os testes abaixo travam justamente esse caso: a série trocada tem de ser
rejeitada, e a legítima tem de passar mesmo com um outlier.
"""

from __future__ import annotations

import pandas as pd

from tesouraria.sources import validation


def test_serie_dentro_da_faixa_e_aceita():
    ptax = pd.Series([4.8, 5.1, 5.3, 5.9, 4.2])
    assert validation.validar_faixa(ptax, [0.5, 20]).aceita


def test_serie_trocada_e_rejeitada():
    """O caso real: o código anotado como 'exportações' devolvia negativos."""
    suposta_exportacao = pd.Series([-61_501, 208_205, -171_782, 12_004, -3_200])
    veredito = validation.validar_faixa(suposta_exportacao, [0, 100_000])

    assert not veredito.aceita
    assert "fora da faixa" in veredito.motivo
    assert "código trocado" in veredito.motivo


def test_motivo_traz_o_observado():
    """A mensagem precisa ser acionável: o que se esperava e o que veio."""
    veredito = validation.validar_faixa(pd.Series([2_527_885.0] * 10), [0, 500_000])

    assert "2,527,885" in veredito.motivo
    assert "[0, 500000]" in veredito.motivo


def test_outlier_isolado_nao_derruba_a_serie():
    """Uma revisão pontual não pode invalidar onze anos de história."""
    valores = pd.Series([5.0] * 199 + [99.0])
    assert validation.validar_faixa(valores, [0.5, 20]).aceita


def test_maioria_fora_derruba():
    valores = pd.Series([5.0] * 10 + [999.0] * 10)
    assert not validation.validar_faixa(valores, [0.5, 20]).aceita


def test_sem_faixa_declarada_aceita():
    """A validação é opcional: série nova não fica bloqueada por falta de faixa."""
    assert validation.validar_faixa(pd.Series([1, 2, 3]), None).aceita
    assert validation.validar_faixa(pd.Series([1, 2, 3]), []).aceita


def test_serie_sem_valor_numerico_e_rejeitada():
    veredito = validation.validar_faixa(pd.Series([None, float("nan")]), [0, 10])

    assert not veredito.aceita
    assert "numérico" in veredito.motivo


def test_aceitar_serie_registra_a_rejeicao(caplog):
    parcial = pd.DataFrame({"valor": [-61_501.0, 208_205.0]})
    serie = {"nome": "Exportações (FOB)", "faixa": [0, 100_000]}

    with caplog.at_level("WARNING"):
        aceita = validation.aceitar_serie(parcial, serie, "SGS 2255")

    assert not aceita
    assert "SGS 2255" in caplog.text
    assert "Exportações" in caplog.text


def test_aceitar_serie_deixa_passar_a_legitima(caplog):
    parcial = pd.DataFrame({"valor": [5.0, 5.2, 5.4]})
    serie = {"nome": "Dólar PTAX venda", "faixa": [0.5, 20]}

    with caplog.at_level("WARNING"):
        assert validation.aceitar_serie(parcial, serie, "SGS 1")
    assert caplog.text == ""


def test_faixas_do_projeto_cobrem_as_series_configuradas():
    """Toda série declarada em config/sources.yaml precisa ter faixa.

    Sem faixa, a série volta a ser um ponto cego — exatamente o que esta
    camada existe para eliminar.
    """
    from tesouraria.settings import load_config

    fontes = load_config("sources")["sources"]
    sem_faixa = [
        f"{nome}:{serie.get('codigo') or serie.get('serie_id') or serie.get('tabela')}"
        for nome in ("bcb_sgs", "us_macro", "ibge_sidra")
        for serie in fontes[nome].get("series", [])
        if not serie.get("faixa")
    ]
    assert not sem_faixa, f"séries sem faixa declarada: {sem_faixa}"
