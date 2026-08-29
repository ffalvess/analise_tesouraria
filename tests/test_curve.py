"""Testes da matemática de curva.

O mais importante deles é o de convenção de taxa: um erro ali propaga em
silêncio para todos os diferenciais do aplicativo, sem que nenhum gráfico
pareça errado.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from tesouraria.analytics import curve

# --------------------------------------------------------------- convenções


def test_base_anual_e_identidade():
    """Taxa brasileira já é efetiva anual; converter não pode mudá-la."""
    assert curve.to_effective_annual(13.75, "anual") == pytest.approx(13.75)


def test_par_yield_semestral_vira_efetiva_anual():
    """(1 + 0,045/2)^2 - 1 = 0,04550625 -> 4,550625%. Conta feita à mão."""
    assert curve.to_effective_annual(4.5, "semestral") == pytest.approx(4.550625, abs=1e-9)


def test_conversao_semestral_cresce_com_o_nivel():
    """O erro de ignorar a convenção não é constante: cresce com a taxa."""
    erro_baixo = curve.to_effective_annual(2.0, "semestral") - 2.0
    erro_alto = curve.to_effective_annual(8.0, "semestral") - 8.0
    assert erro_alto > erro_baixo > 0
    # Em 4,5% o erro já passa de 5 pontos-base, o que justifica a conversão.
    assert (curve.to_effective_annual(4.5, "semestral") - 4.5) * 100 > 5


def test_conversao_desconhecida_falha():
    with pytest.raises(ValueError, match="capitalização"):
        curve.to_effective_annual(5.0, "trimestral")


def test_conversao_aceita_vetor():
    resultado = curve.to_effective_annual(np.array([2.0, 4.5]), "semestral")
    assert resultado.shape == (2,)
    assert resultado[1] == pytest.approx(4.550625, abs=1e-9)


# -------------------------------------------------------------------- curva


def curva_exemplo() -> curve.Curva:
    return curve.Curva(
        data_ref=dt.date(2026, 8, 28),
        rotulo="teste",
        prazos=np.array([0.5, 1.0, 2.0, 3.0, 5.0, 10.0]),
        taxas=np.array([14.0, 13.8, 13.5, 13.3, 13.1, 13.0]),
    )


def test_build_curve_consolida_vertices_repetidos():
    import pandas as pd

    df = pd.DataFrame(
        {
            "prazo_anos": [1.0, 1.0, 1.0, 5.0],
            # A mediana ignora o outlier; a média seria puxada por ele.
            "taxa": [13.0, 13.2, 40.0, 12.0],
        }
    )
    resultado = curve.build_curve(df, dt.date(2026, 8, 28), "x")
    assert list(resultado.prazos) == [1.0, 5.0]
    assert resultado.taxas[0] == pytest.approx(13.2)


def test_build_curve_descarta_prazos_nao_positivos():
    import pandas as pd

    df = pd.DataFrame({"prazo_anos": [-1.0, 0.0, 2.0], "taxa": [10.0, 11.0, 12.0]})
    resultado = curve.build_curve(df, dt.date(2026, 8, 28), "x")
    assert list(resultado.prazos) == [2.0]


def test_build_curve_com_quadro_vazio():
    import pandas as pd

    resultado = curve.build_curve(pd.DataFrame(), dt.date(2026, 8, 28), "x")
    assert resultado.vazia


# ------------------------------------------------------------- interpolação


@pytest.mark.parametrize("metodo", ["pchip", "cubic", "linear"])
def test_interpolacao_reproduz_os_vertices(metodo):
    """Nos próprios vértices, qualquer método deve devolver a taxa observada."""
    c = curva_exemplo()
    obtido = curve.interpolate(c, c.prazos, metodo=metodo)
    np.testing.assert_allclose(obtido, c.taxas, rtol=1e-6)


def test_pchip_nao_extrapola():
    """Extrapolar a ponta longa seria inventar informação que a curva não tem."""
    c = curva_exemplo()
    obtido = curve.interpolate(c, [0.1, 30.0], metodo="pchip")
    assert np.isnan(obtido).all()


def test_pchip_e_monotonica_em_curva_monotonica():
    """A spline cúbica pode oscilar entre vértices; a pchip, não."""
    c = curva_exemplo()  # taxas estritamente decrescentes
    prazos = np.linspace(0.5, 10.0, 200)
    valores = curve.interpolate(c, prazos, metodo="pchip")
    assert np.all(np.diff(valores) <= 1e-9)


def test_nss_ajusta_dentro_da_tolerancia():
    c = curva_exemplo()
    parametros = curve.ajustar_nss(c)
    assert len(parametros) == 6
    ajustado = curve.nss(c.prazos, *parametros)
    assert np.max(np.abs(ajustado - c.taxas)) < 0.35


def test_nss_exige_quatro_vertices():
    curta = curve.Curva(dt.date(2026, 8, 28), "x", np.array([1.0, 2.0]), np.array([10.0, 11.0]))
    with pytest.raises(ValueError, match="quatro"):
        curve.ajustar_nss(curta)


def test_curva_vazia_devolve_nan():
    vazia = curve.Curva(dt.date(2026, 8, 28), "x", np.array([]), np.array([]))
    obtido = curve.interpolate(vazia, [1.0, 5.0])
    assert np.isnan(obtido).all()


def test_curva_de_um_ponto_e_plana():
    unica = curve.Curva(dt.date(2026, 8, 28), "x", np.array([2.0]), np.array([11.5]))
    np.testing.assert_allclose(curve.interpolate(unica, [1.0, 5.0]), [11.5, 11.5])


# -------------------------------------------------------------------- grade


def test_to_grid_devolve_todos_os_prazos_pedidos():
    grade = curve.to_grid(curva_exemplo(), [1.0, 2.0, 5.0])
    assert list(grade["prazo_anos"]) == [1.0, 2.0, 5.0]
    assert grade["taxa"].notna().all()


def test_to_grid_usa_a_grade_padrao_do_yaml():
    grade = curve.to_grid(curva_exemplo())
    assert list(grade["prazo_anos"]) == curve.grade_padrao()


# ----------------------------------------------------------------- métricas


def test_metricas_de_curva_invertida():
    m = curve.metricas(curva_exemplo())
    # Curva decrescente: a inclinação 10a-2a tem de ser negativa.
    assert m["inclinacao_10a_2a"] < 0
    assert m["curto_1a"] == pytest.approx(13.8)
    assert m["longo_10a"] == pytest.approx(13.0)


def test_forward_entre_dois_vertices():
    """Curva plana em 10%: qualquer taxa a termo também tem de ser 10%."""
    plana = curve.Curva(
        dt.date(2026, 8, 28), "plana", np.array([1.0, 2.0, 5.0, 10.0]), np.full(4, 10.0)
    )
    assert curve.forward(plana, 2.0, 5.0) == pytest.approx(10.0, abs=1e-9)


def test_forward_de_curva_ascendente_supera_a_taxa_a_vista():
    c = curve.Curva(
        dt.date(2026, 8, 28), "x", np.array([1.0, 2.0, 5.0]), np.array([10.0, 11.0, 12.0])
    )
    assert curve.forward(c, 2.0, 5.0) > 12.0


def test_forward_rejeita_intervalo_invertido():
    with pytest.raises(ValueError, match="maior"):
        curve.forward(curva_exemplo(), 5.0, 2.0)


def test_variacao_bps_mede_o_deslocamento():
    base = curve.Curva(
        dt.date(2026, 1, 2), "antes", np.array([1.0, 2.0, 5.0]), np.array([10.0, 10.0, 10.0])
    )
    depois = curve.Curva(
        dt.date(2026, 8, 28), "depois", np.array([1.0, 2.0, 5.0]), np.array([10.5, 10.5, 10.5])
    )
    variacao = curve.variacao_bps(base, depois, [1.0, 2.0, 5.0])
    np.testing.assert_allclose(variacao["variacao_bps"], [50.0, 50.0, 50.0])
