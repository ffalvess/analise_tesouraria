"""Contas da página Macro EUA: variações, regra de Sahm e decomposição do PIB."""

from __future__ import annotations

import pandas as pd
import pytest

from tesouraria.analytics import macro_eua as m


def quadro(datas, valores) -> pd.DataFrame:
    return pd.DataFrame({"data_ref": pd.to_datetime(datas), "valor": valores})


def mensal(valores, inicio="2024-01-01") -> pd.DataFrame:
    return quadro(pd.date_range(inicio, periods=len(valores), freq="MS"), valores)


def trimestral(valores, inicio="2024-01-01") -> pd.DataFrame:
    return quadro(pd.date_range(inicio, periods=len(valores), freq="QS"), valores)


# ---------------------------------------------------------------- variações


def test_variacao_anual_mensal():
    indice = mensal([100.0] * 12 + [110.0])
    out = m.variacao_anual(indice)
    assert len(out) == 1
    assert out["valor"].iloc[0] == pytest.approx(10.0)
    assert out["data_ref"].iloc[0] == pd.Timestamp("2025-01-01")


def test_mes_sem_divulgacao_nao_desalinha_a_comparacao():
    """O shutdown de out/2025: sem a linha, contar 12 posições erraria o mês."""
    datas = [
        d
        for d in pd.date_range("2024-09-01", "2025-11-01", freq="MS")
        if d != pd.Timestamp("2024-10-01")
    ]
    valores = [100.0 + i for i in range(len(datas))]
    out = m.variacao_anual(quadro(datas, valores)).set_index("data_ref")["valor"]

    # Outubro de 2025 não tem com quem se comparar: fica vazio, não errado.
    assert pd.Timestamp("2025-10-01") not in out.index
    serie = dict(zip(datas, valores, strict=True))
    esperado = (serie[pd.Timestamp("2025-11-01")] / serie[pd.Timestamp("2024-11-01")] - 1) * 100
    assert out[pd.Timestamp("2025-11-01")] == pytest.approx(esperado)


def test_variacao_anual_semanal_usa_52_semanas():
    datas = pd.date_range("2024-01-06", periods=60, freq="W-SAT")
    out = m.variacao_anual(quadro(datas, [200_000.0] * 52 + [220_000.0] * 8))
    assert out["valor"].iloc[0] == pytest.approx(10.0)
    assert out["data_ref"].iloc[0] == datas[52]


def test_variacao_trimestral_anualizada():
    """1% no trimestre é 4,06% em ritmo anual, como o BEA divulga."""
    out = m.variacao_anualizada(trimestral([100.0, 101.0]), meses=3)
    assert out["valor"].iloc[0] == pytest.approx((1.01**4 - 1) * 100)


def test_inflacao_de_tres_meses_anualizada():
    indice = mensal([100.0, 100.2, 100.4, 100.6])
    out = m.variacao_anualizada(indice, meses=3)
    assert out["valor"].iloc[0] == pytest.approx(((100.6 / 100.0) ** 4 - 1) * 100)


def test_diferenca_do_payroll():
    out = m.diferenca(mensal([158_000.0, 158_150.0, 158_100.0]))
    assert out["valor"].tolist() == [150.0, -50.0]


def test_media_movel_conta_o_mes_ausente_como_ausente():
    datas = pd.to_datetime(["2025-07-01", "2025-08-01", "2025-09-01", "2025-11-01"])
    out = m.media_movel(quadro(datas, [100.0, 200.0, 300.0, 600.0]), 3)
    serie = out.set_index("data_ref")["valor"]
    # Novembro: janela set/out/nov com outubro vazio -> média de set e nov.
    assert serie[pd.Timestamp("2025-11-01")] == pytest.approx(450.0)


def test_razao_alinha_por_data():
    vagas = mensal([8_000.0, 7_500.0])
    desempregados = mensal([6_400.0, 6_000.0])
    assert m.razao(vagas, desempregados)["valor"].tolist() == pytest.approx([1.25, 1.25])


def test_series_vazias_nao_quebram():
    vazio = pd.DataFrame(columns=["data_ref", "valor"])
    for funcao in (m.variacao_anual, m.diferenca, m.regra_de_sahm):
        assert funcao(vazio).empty
    assert m.variacao_anualizada(None, 3).empty
    assert m.ultimo(vazio) == (None, pytest.approx(float("nan"), nan_ok=True))


# ------------------------------------------------------------- regra de Sahm


def test_regra_de_sahm_dispara_com_meio_ponto():
    desemprego = mensal([4.0] * 15 + [4.2, 4.4, 4.6, 4.8])
    sahm = m.regra_de_sahm(desemprego).set_index("data_ref")["valor"]

    # Média de 3 meses no último mês: (4.4+4.6+4.8)/3 = 4.6; mínima anterior 4.0.
    assert sahm.iloc[-1] == pytest.approx(0.6)
    assert sahm.iloc[-1] >= m.LIMIAR_SAHM
    assert sahm.iloc[-3] < m.LIMIAR_SAHM


def test_regra_de_sahm_fica_negativa_em_nova_minima():
    """O mês corrente fica fora da janela da mínima, como no SAHMREALTIME."""
    desemprego = mensal([5.0] * 14 + [4.6, 4.3, 4.0])
    assert m.regra_de_sahm(desemprego)["valor"].iloc[-1] < 0


# --------------------------------------------------------------------- PIB


def _contribuicoes(valores: dict[str, list[float]]) -> dict[str, pd.DataFrame]:
    return {serie_id: trimestral(v) for serie_id, v in valores.items()}


def test_producao_remapeia_para_o_recorte_do_ibge():
    series = _contribuicoes(
        {"CPGDPAI": [3.0], "CPGDPGPI": [1.0], "CPGDPAFH": [0.1], "CPGDPU": [0.05]}
    )
    linha = m.contribuicoes_producao(series).iloc[0]

    assert linha["agropecuaria"] == pytest.approx(0.1)
    assert linha["industria"] == pytest.approx(0.95)  # bens − agro + utilities
    assert linha["servicos"] == pytest.approx(1.95)  # PIB − bens − utilities
    assert linha[["agropecuaria", "industria", "servicos"]].sum() == pytest.approx(linha["total"])


def test_detalhe_da_industria_fecha_com_a_industria():
    valores = {
        "CPGDPAI": [3.0, -1.0],
        "CPGDPGPI": [1.0, -0.6],
        "CPGDPAFH": [0.1, 0.05],
        "CPGDPU": [0.05, -0.02],
        "CPGDPC": [0.2, -0.1],
        "CPGDPMD": [0.4, -0.3],
        "CPGDPMN": [0.1, -0.05],
    }
    series = _contribuicoes(valores)
    producao = m.contribuicoes_producao(series)
    detalhe = m.detalhe_industria(series)

    partes = detalhe[["transformacao", "construcao", "utilities", "extrativa"]].sum(axis=1)
    assert partes.to_numpy() == pytest.approx(producao["industria"].to_numpy())
    assert detalhe["extrativa"].iloc[0] == pytest.approx(1.0 - 0.1 - 0.2 - 0.5)


def test_decomposicao_exige_todas_as_series():
    series = _contribuicoes({"CPGDPAI": [3.0], "CPGDPGPI": [1.0], "CPGDPAFH": [0.1]})
    assert m.contribuicoes_producao(series).empty
    assert m.contribuicoes_despesa({}).empty


def test_contribuicoes_da_despesa_somam_o_pib_nas_amostras(ambiente_ingerido):
    """C + I + G + X + M fecha com o crescimento anualizado do PIB real."""
    from tesouraria import queries

    series = {sid: queries.serie(sid) for sid in m.CONTRIBUICOES_DESPESA}
    contribuicoes = m.contribuicoes_despesa(series).set_index("data_ref")
    pib = m.variacao_anualizada(queries.serie("GDPC1"), 3).set_index("data_ref")["valor"]

    comum = contribuicoes.index.intersection(pib.index)
    assert len(comum) >= 8
    diferenca = (contribuicoes.loc[comum, "soma"] - pib.loc[comum]).abs()
    assert diferenca.max() < 0.01
