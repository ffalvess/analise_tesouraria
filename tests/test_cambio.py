"""Aba de câmbio: calendário, OLS, ortogonalização, atribuição, cesta e parsers.

Tudo com dados sintéticos de parâmetros conhecidos — o teste diz qual número
tem de sair, não só que algo saiu.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tesouraria import agenda, calendario
from tesouraria.analytics import atribuicao as atrib
from tesouraria.analytics import commodities as cmd
from tesouraria.analytics import painel_cambio as painel
from tesouraria.analytics.ols import ols
from tesouraria.sources.comex_pesos import ComexPesosSource
from tesouraria.sources.yahoo import YahooSource

# ------------------------------------------------------------- calendário


def test_pascoa_e_feriados_moveis():
    assert calendario.pascoa(2026) == dt.date(2026, 4, 5)
    assert calendario.pascoa(2027) == dt.date(2027, 3, 28)
    f27 = calendario.feriados(2027)
    assert f27[dt.date(2027, 2, 8)] == "Carnaval (segunda)"
    assert f27[dt.date(2027, 2, 9)] == "Carnaval (terça)"
    assert f27[dt.date(2027, 3, 26)] == "Sexta-feira Santa"
    assert f27[dt.date(2027, 5, 27)] == "Corpus Christi"


def test_consciencia_negra_so_a_partir_de_2024():
    assert dt.date(2024, 11, 20) in calendario.feriados(2024)
    assert dt.date(2023, 11, 20) not in calendario.feriados(2023)


def test_contagem_de_dias_uteis_exclui_inicio_e_inclui_fim():
    # 19/11/2026 (qui) → 23/11/2026 (seg): 20/11 é feriado, 21-22 fim de semana.
    assert calendario.dias_uteis(dt.date(2026, 11, 19), dt.date(2026, 11, 23)) == 1
    # Janeiro de 2027: 21 dias úteis (1/1 feriado).
    assert calendario.dias_uteis(dt.date(2026, 12, 31), dt.date(2027, 1, 31)) == 20
    assert calendario.dias_uteis(dt.date(2027, 1, 31), dt.date(2026, 12, 31)) == -20


def test_somar_du_pula_feriado_e_fim_de_semana():
    assert calendario.somar_du(dt.date(2026, 11, 19), 1) == dt.date(2026, 11, 23)
    assert calendario.somar_du(dt.date(2026, 11, 23), -5) == dt.date(2026, 11, 13)
    assert calendario.somar_du(dt.date(2026, 11, 21), 1) == dt.date(2026, 11, 23)
    assert calendario.ultimo_dia_util(dt.date(2026, 11, 22)) == dt.date(2026, 11, 19)


def test_proximos_dez_dias_uteis():
    dias = calendario.proximos_dias_uteis(dt.date(2026, 11, 20), 10)
    assert len(dias) == 10
    assert dias[0] == dt.date(2026, 11, 23)
    assert all(calendario.eh_dia_util(d) for d in dias)


def test_feriados_americanos():
    us = calendario.feriados_eua(2026)
    assert us[dt.date(2026, 11, 26)] == "Thanksgiving"
    assert us[dt.date(2026, 7, 3)] == "Independence Day"  # 4/7 cai num sábado


# ---------------------------------------------------------------- agenda


def test_agenda_marca_evento_americano_em_feriado_dos_eua():
    eventos = [{"nome": "Claims", "regiao": "US", "regra": "semanal", "dia_semana": 3,
                "hora": "08:30", "fuso": "America/New_York"}]
    saida = agenda.expandir(eventos, dt.date(2026, 11, 23), dt.date(2026, 12, 4))
    thanksgiving = saida[saida["data"] == dt.date(2026, 11, 26)].iloc[0]
    assert thanksgiving["a_confirmar"]
    assert "Thanksgiving" in thanksgiving["nota"]
    # 8h30 em Nova York, horário padrão (UTC−5) = 10h30 em Brasília.
    assert thanksgiving["hora"] == "10:30"


def test_agenda_omite_evento_brasileiro_em_feriado():
    eventos = [{"nome": "Algo", "regiao": "BR", "regra": "datas", "datas": ["2026-11-20"], "hora": "09:00"}]
    assert agenda.expandir(eventos, dt.date(2026, 11, 1), dt.date(2026, 11, 30)).empty


def test_agenda_da_configuracao_cobre_dez_dias_uteis():
    dias, eventos = agenda.proximos_dias_uteis(dt.date(2026, 11, 23), 10)
    assert len(dias) == 10
    assert not eventos.empty
    assert eventos["data"].between(dias[0], dias[-1]).all()


# ------------------------------------------------------------------- OLS


def test_ols_recupera_coeficientes_conhecidos():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(5000, 3))
    y = 0.5 + x @ np.array([1.2, -0.7, 0.3]) + rng.normal(0, 0.01, 5000)
    ajuste = ols(y, x)
    assert ajuste.constante == pytest.approx(0.5, abs=1e-3)
    np.testing.assert_allclose(ajuste.betas, [1.2, -0.7, 0.3], atol=1e-3)
    assert ajuste.r2 > 0.999
    assert ajuste.desvio_residuo == pytest.approx(0.01, rel=0.05)


def test_ols_recusa_amostra_curta():
    with pytest.raises(ValueError):
        ols(np.arange(3.0), np.ones((3, 3)))


# ------------------------------------------------------- ortogonalização


def _fatores_sinteticos(n: int, seed: int = 7):
    """Fatores ortogonais verdadeiros e as versões brutas, correlacionadas."""
    rng = np.random.default_rng(seed)
    o1 = rng.normal(0, 0.005, n)
    o2 = rng.normal(0, 0.004, n)
    o3 = rng.normal(0, 0.008, n)
    dxy = o1
    em = 0.8 * o1 + o2
    cmd_ = -0.5 * o1 + 0.3 * o2 + o3
    return np.column_stack([dxy, em, cmd_]), np.column_stack([o1, o2, o3])


def test_ortogonalizacao_produz_fatores_ortogonais_na_ordem_fixa():
    bruto, _ = _fatores_sinteticos(2000)
    orto, _ = atrib.ortogonalizar(bruto)
    np.testing.assert_allclose(orto[:, 0], bruto[:, 0])  # f1 é o próprio DXY
    assert abs(orto[:, 1] @ orto[:, 0]) < 1e-12
    assert abs(orto[:, 2] @ orto[:, 0]) < 1e-12
    assert abs(orto[:, 2] @ orto[:, 1]) < 1e-12
    assert abs(orto[:, 1].mean()) < 1e-15 and abs(orto[:, 2].mean()) < 1e-15


def test_ortogonalizacao_recupera_os_coeficientes_da_mistura():
    bruto, _ = _fatores_sinteticos(20000)
    _, coefs = atrib.ortogonalizar(bruto)
    assert coefs.em_em_dxy.betas[0] == pytest.approx(0.8, abs=0.02)
    np.testing.assert_allclose(coefs.cmd_em_f1f2.betas, [-0.5, 0.3], atol=0.03)


def _base_conhecida(n: int = 400, ruido: float = 0.0005):
    bruto, orto = _fatores_sinteticos(n, seed=11)
    rng = np.random.default_rng(3)
    betas = np.array([0.9, 1.1, -0.4])
    y = 0.0002 + orto @ betas + rng.normal(0, ruido, n)
    datas = pd.bdate_range("2025-01-02", periods=n)
    base = pd.DataFrame({"usdbrl": y, "dxy": bruto[:, 0], "em": bruto[:, 1], "cmd": bruto[:, 2]},
                        index=datas)
    return base, betas


def test_atribuicao_recupera_betas_dos_fatores_ortogonais():
    """Fatores exatamente ortogonais na janela → os betas saem exatos.

    Com fatores só "aproximadamente" independentes, a correlação amostral
    residual iria para o fator que vem antes na ordem — que é a definição do
    método, não erro. Por isso a construção exata.
    """
    rng = np.random.default_rng(21)
    janela = 250
    z = rng.normal(size=(janela, 3))
    z -= z.mean(axis=0)
    q, _ = np.linalg.qr(z)                       # colunas ortonormais e de média zero
    orto = np.vstack([q * np.array([0.08, 0.06, 0.12]), rng.normal(0, 0.005, (1, 3))])
    bruto = np.column_stack([
        orto[:, 0],
        0.8 * orto[:, 0] + orto[:, 1],
        -0.5 * orto[:, 0] + 0.3 * orto[:, 1] + orto[:, 2],
    ])
    betas = np.array([0.9, 1.1, -0.4])
    y = 0.0002 + orto @ betas + rng.normal(0, 1e-7, janela + 1)
    base = pd.DataFrame({"usdbrl": y, "dxy": bruto[:, 0], "em": bruto[:, 1], "cmd": bruto[:, 2]},
                        index=pd.bdate_range("2025-01-02", periods=janela + 1))
    resultado = atrib.atribuir(base, janela=janela)
    assert len(resultado) == 1
    linha = resultado.iloc[0]
    np.testing.assert_allclose(linha[["beta_dxy", "beta_em", "beta_cmd"]].to_numpy(float), betas, atol=1e-3)
    assert linha["constante"] == pytest.approx(0.0002, abs=1e-6)
    # Fora da amostra, o dia analisado também é decomposto corretamente.
    np.testing.assert_allclose(
        [linha["contrib_dxy"], linha["contrib_em"], linha["contrib_cmd"]], orto[-1] * betas, atol=1e-5
    )
    assert abs(linha["residuo"]) < 1e-5
    assert linha["r2"] > 0.9999


def test_atribuicao_estatistica_com_betas_conhecidos():
    base, betas = _base_conhecida(n=3000)
    resultado = atrib.atribuir(base, janela=1500)
    medios = resultado[["beta_dxy", "beta_em", "beta_cmd"]].mean().to_numpy()
    np.testing.assert_allclose(medios, betas, atol=0.08)


def test_partes_somam_a_variacao_observada():
    base, _ = _base_conhecida()
    resultado = atrib.atribuir(base, janela=60)
    assert len(resultado) == len(base) - 60
    np.testing.assert_allclose(atrib.conferir_identidade(resultado), 0.0, atol=1e-14)


def test_dia_analisado_nao_entra_na_propria_estimacao():
    base, _ = _base_conhecida()
    normal = atrib.atribuir(base, janela=60)
    choque = base.copy()
    choque.iloc[-1, 0] += 0.05  # um choque de 5% só no USD/BRL, no último dia
    com_choque = atrib.atribuir(choque, janela=60)
    # Betas do último dia idênticos: estimados até a sessão anterior.
    np.testing.assert_allclose(
        normal.iloc[-1][["beta_dxy", "beta_em", "beta_cmd"]],
        com_choque.iloc[-1][["beta_dxy", "beta_em", "beta_cmd"]],
    )
    assert com_choque.iloc[-1]["residuo"] - normal.iloc[-1]["residuo"] == pytest.approx(0.05)
    assert com_choque.iloc[-1]["z_residuo"] > 10


# ----------------------------------------------------------------- flags


def _resultado_e_contexto():
    datas = pd.bdate_range("2026-01-05", periods=3)
    resultado = pd.DataFrame({"z_residuo": [2.5, 2.5, 0.1], "z_cmd": [0.0, -2.1, 1.0]}, index=datas)
    contexto = pd.DataFrame(
        {"di_longo_bps": [12.0, 5.0, np.nan], "vix_pct": [11.0, 15.0, 20.0],
         "dxy": [0.001, -0.001, 0.002], "em": [0.002, 0.003, 0.001]},
        index=datas,
    )
    return resultado, contexto


def test_flags_seguem_as_regras():
    resultado, contexto = _resultado_e_contexto()
    f = atrib.flags(resultado, contexto)
    assert f["estresse_brasil"].tolist() == [True, False, False]
    assert f["risk_off_global"].tolist() == [True, False, True]
    assert f["choque_commodities"].tolist() == [False, True, False]


def test_flags_usam_limiares_da_configuracao():
    resultado, contexto = _resultado_e_contexto()
    f = atrib.flags(resultado, contexto, {"estresse_di_bps": 4.0, "riskoff_vix_pct": 12.0})
    assert f["estresse_brasil"].tolist() == [True, True, False]
    assert f["risk_off_global"].tolist() == [False, False, True]


# ----------------------------------------------------- cesta de commodities


PRODUTOS = {
    "petroleo": {"sh4": ["2709"]},
    "soja": {"sh4": ["1201"]},
    "carne": {"sh4": ["0201", "0202"]},
}


def _mensal(meses: int = 14, falta_ultimo_soja: bool = False) -> pd.DataFrame:
    linhas = []
    for i, mes in enumerate(pd.period_range("2025-01", periods=meses, freq="M")):
        for serie, valor in [("comex_sh4_2709", 3e9), ("comex_sh4_1201", 1e9),
                             ("comex_sh4_0201", 0.5e9), ("comex_sh4_0202", 0.5e9)]:
            if falta_ultimo_soja and serie == "comex_sh4_1201" and i == meses - 1:
                continue
            linhas.append({"serie_id": serie, "data_ref": mes.to_timestamp().date(), "valor": valor})
    return pd.DataFrame(linhas)


def test_pesos_somam_um_e_seguem_o_valor_exportado():
    pesos = cmd.calcular_pesos(_mensal(), PRODUTOS, ["petroleo", "soja", "carne"])
    assert pesos.origem == "comex"
    assert pesos.pesos.sum() == pytest.approx(1.0)
    assert pesos.pesos["petroleo"] == pytest.approx(0.6)
    assert pesos.pesos["carne"] == pytest.approx(0.2)  # dois SH4 somados


def test_pesos_so_dos_ativos():
    pesos = cmd.calcular_pesos(_mensal(), PRODUTOS, ["petroleo", "soja"])
    assert set(pesos.pesos.index) == {"petroleo", "soja"}
    assert pesos.pesos.sum() == pytest.approx(1.0)
    assert pesos.pesos["petroleo"] == pytest.approx(0.75)


def test_janela_termina_no_ultimo_mes_completo():
    pesos = cmd.calcular_pesos(_mensal(falta_ultimo_soja=True), PRODUTOS, ["petroleo", "soja"])
    assert pesos.fim == pd.Timestamp("2026-01-01")  # o mês 14 está incompleto
    assert pesos.inicio == pd.Timestamp("2025-02-01")


def test_sem_comex_os_pesos_sao_iguais_e_avisados():
    pesos = cmd.calcular_pesos(pd.DataFrame(), PRODUTOS, ["petroleo", "soja", "carne"])
    assert pesos.origem == "iguais"
    assert pesos.aviso
    assert pesos.pesos.sum() == pytest.approx(1.0)


def test_media_ponderada_renormaliza_e_respeita_cobertura():
    retornos = pd.DataFrame({"a": [0.01, 0.01, np.nan], "b": [0.03, np.nan, np.nan],
                             "c": [0.0, 0.0, 0.02]})
    pesos = pd.Series({"a": 0.5, "b": 0.3, "c": 0.2})
    saida = atrib.media_ponderada(retornos, pesos, cobertura_minima=0.5)
    assert saida["retorno"].iloc[0] == pytest.approx(0.5 * 0.01 + 0.3 * 0.03)
    # Dia 2 sem "b": renormaliza sobre a e c (0,5 + 0,2).
    assert saida["retorno"].iloc[1] == pytest.approx(0.5 * 0.01 / 0.7)
    # Dia 3 só com 20% do peso: abaixo do mínimo, vira lacuna.
    assert np.isnan(saida["retorno"].iloc[2])


def test_montar_fatores_exclui_componente_ralo():
    datas = pd.bdate_range("2026-01-02", periods=80)
    rng = np.random.default_rng(5)
    niveis = pd.DataFrame(
        {t: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 80)))
         for t in ["BRL", "DXY", "MXN", "ZAR", "OIL", "ORE"]},
        index=datas,
    )
    niveis.loc[niveis.index[:70], "ORE"] = np.nan  # minério só nas últimas 10 sessões
    f = painel.montar_fatores(
        niveis, "BRL", "DXY", ["MXN", "ZAR"], 2,
        pd.Series({"OIL": 0.6, "ORE": 0.4}), 0.5, 40,
    )
    assert "ORE" in f.excluidos
    assert f.pesos_usados.to_dict() == {"OIL": 1.0}
    assert list(f.base.columns) == ["usdbrl", "dxy", "em", "cmd"]
    assert len(f.base) == 79


# ----------------------------------------------------------------- cards


def test_resumo_usa_dias_uteis_e_marca_defasagem():
    datas = pd.bdate_range("2026-10-01", "2026-11-19")
    datas = datas[[calendario.eh_dia_util(d.date()) for d in datas]]
    serie = pd.Series(np.arange(len(datas), dtype=float) + 100, index=datas)
    r = painel.resumo_serie(serie, "pct", dt.date(2026, 11, 23), tolerancia_du=1)
    assert r.data == dt.date(2026, 11, 19)
    assert r.var_1d == pytest.approx((serie.iloc[-1] / serie.iloc[-2] - 1) * 100)
    assert r.var_5du == pytest.approx((serie.iloc[-1] / serie.iloc[-6] - 1) * 100)
    assert r.atraso_du == 1 and not r.defasado  # 20/11 feriado: só a segunda conta
    atrasado = painel.resumo_serie(serie, "pct", dt.date(2026, 11, 26), tolerancia_du=1)
    assert atrasado.defasado and atrasado.atraso_du == 4


def test_resumo_em_bps():
    serie = pd.Series([13.50, 13.62], index=pd.to_datetime(["2026-11-18", "2026-11-19"]))
    r = painel.resumo_serie(serie, "bps", dt.date(2026, 11, 19))
    assert r.var_1d == pytest.approx(12.0)
    assert r.unidade_var == "bps"


def test_resumo_de_serie_vazia_e_defasado():
    r = painel.resumo_serie(pd.Series(dtype=float), "pct", dt.date(2026, 11, 23))
    assert r.defasado and r.data is None


def test_prazo_constante_reaproveita_a_curva_e_nao_extrapola():
    vertices = pd.DataFrame({
        "data_ref": [dt.date(2026, 11, 19)] * 3,
        "prazo_anos": [0.5, 2.0, 4.0],
        "taxa": [14.0, 13.5, 13.0],
    })
    serie = painel.serie_prazo_constante(vertices, [1.0, 5.0])
    assert 13.5 < serie.iloc[0][1.0] < 14.0
    assert np.isnan(serie.iloc[0][5.0])


def test_escolha_da_fonte_da_curva():
    assert painel.escolher_fonte_curva({"tesouro": 2900, "anbima": 86}, ["b3", "anbima", "tesouro"], 30) == "anbima"
    assert painel.escolher_fonte_curva({"tesouro": 2900, "b3": 5}, ["b3", "anbima", "tesouro"], 30) == "tesouro"
    assert painel.escolher_fonte_curva({}, ["b3"], 30) is None


# --------------------------------------------------------------- parsers


def test_parser_do_yahoo_data_no_fuso_da_bolsa():
    # 1763584200 = 19/11/2025 20:30 UTC = 19/11 14:30 em Chicago, já 20/11 em Singapura.
    payload = {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "America/Chicago"},
        "timestamp": [1763584200, 1763584200 + 86400],
        "indicators": {"quote": [{"close": [1100.5, None]}]},
    }], "error": None}}
    df = YahooSource.parse(payload, "ZS=F")
    assert df["data_ref"].tolist() == [dt.date(2025, 11, 19)]
    assert df["valor"].tolist() == [1100.5]


def test_parser_do_yahoo_propaga_erro_da_api():
    with pytest.raises(ValueError):
        YahooSource.parse({"chart": {"result": None, "error": {"code": "Not Found"}}}, "XXX")


def test_parser_do_comex_por_sh4():
    payload = {"data": {"list": [
        {"headingCode": "201", "year": "2026", "monthNumber": "3", "metricFOB": "100"},
        {"headingCode": "201", "year": "2026", "monthNumber": "3", "metricFOB": "50"},
        {"headingCode": "2709", "year": "2026", "monthNumber": "3", "metricFOB": "999"},
    ]}}
    df = ComexPesosSource.parse(payload)
    linha = df[df["serie_id"] == "comex_sh4_0201"].iloc[0]
    assert linha["valor"] == 150 and linha["data_ref"] == dt.date(2026, 3, 1)


def test_parser_do_comex_recusa_formato_desconhecido():
    with pytest.raises(ValueError):
        ComexPesosSource.parse({"data": {"list": [{"foo": 1}]}})


def test_series_da_aba_estao_declaradas_com_faixa():
    from tesouraria import sources
    from tesouraria.settings import load_config

    yahoo = load_config("sources")["sources"]["yahoo"]["series"]
    assert all("faixa" in s for s in yahoo)
    declaradas = sources.series_declaradas()
    assert {"BRL=X", "DX-Y.NYB", "comex_sh4_2709", "VIXCLS"} <= declaradas
