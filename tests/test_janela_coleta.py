"""Janela de coleta das séries de baixa frequência.

A coleta diária pede "os últimos sete dias". Para uma série mensal ou
trimestral, isso significa nunca: o CPI de agosto é datado de 2026-08-01 e sai
em meados de setembro, quando "sete dias atrás" já está em setembro. Foi assim
que o CPI e o IPCA pararam em julho no aplicativo publicado — sem erro nenhum,
porque a coleta "funcionava" e apenas não trazia nada.
"""

from __future__ import annotations

import datetime as dt
import json

from tesouraria import db
from tesouraria.settings import Settings
from tesouraria.sources.base import inicio_da_coleta

PADRAO = dt.date(2010, 1, 1)
HOJE = dt.date(2026, 10, 9)
SETE_DIAS = HOJE - dt.timedelta(days=7)


def test_sem_since_coleta_o_historico_inteiro():
    assert inicio_da_coleta(None, dt.date(2026, 7, 1), PADRAO, 400) == PADRAO


def test_serie_nova_busca_o_historico_mesmo_com_since():
    """Acrescentar uma série ao YAML basta: a próxima coleta diária a preenche."""
    assert inicio_da_coleta(SETE_DIAS, None, PADRAO, 400) == PADRAO


def test_serie_mensal_atrasada_entra_na_coleta_diaria():
    """O caso que motivou a regra: CPI gravado até julho, coleta em outubro."""
    inicio = inicio_da_coleta(SETE_DIAS, dt.date(2026, 7, 1), PADRAO, revisao_dias=0)
    assert inicio <= dt.date(2026, 8, 1), "a observação de agosto ficaria de fora"


def test_folga_de_revisao_recua_a_partir_da_ultima_observacao():
    inicio = inicio_da_coleta(SETE_DIAS, dt.date(2026, 4, 1), PADRAO, revisao_dias=400)
    assert inicio == dt.date(2026, 4, 1) - dt.timedelta(days=400)


def test_since_mais_antigo_prevalece():
    """Um backfill pedido explicitamente nunca é encurtado pela regra."""
    pedido = dt.date(2015, 1, 1)
    assert inicio_da_coleta(pedido, dt.date(2026, 7, 1), PADRAO, 400) == pedido


def test_ultimas_datas_por_serie(ambiente_ingerido):
    with db.connection(read_only=True) as con:
        fred = db.ultimas_datas(con, "fred")
        sgs = db.ultimas_datas(con, "bcb_sgs")

    assert fred["CPIAUCSL"] == dt.date(2026, 8, 1)
    assert fred["GDPC1"] == dt.date(2026, 4, 1)
    assert "433" in sgs and "CPIAUCSL" not in sgs


def _rede_simulada(monkeypatch):
    """Tira a fonte do modo offline sem tocar a rede."""
    simulado = lambda: Settings(offline=False, fred_api_key="chave-de-teste")  # noqa: E731
    monkeypatch.setattr("tesouraria.sources.base.get_settings", simulado)
    monkeypatch.setattr("tesouraria.sources.us_macro.get_settings", simulado)


def test_fred_pede_cada_serie_a_partir_do_que_ja_tem(ambiente_ingerido, monkeypatch):
    from tesouraria.sources.us_macro import INICIO_PADRAO, UsMacroSource

    _rede_simulada(monkeypatch)
    fonte = UsMacroSource()
    with db.connection(read_only=True) as con:
        fonte.prepare(con)
    # Simula uma série recém-declarada, ainda sem nada no banco.
    fonte._ultimas.pop("GDPC1")

    pedidos: dict[str, str] = {}

    def falso_get(url, **kwargs):
        params = kwargs["params"]
        pedidos[params["series_id"]] = params["observation_start"]
        return json.dumps({"observations": []}).encode()

    fonte.get = falso_get
    fonte.collect(since=SETE_DIAS)

    revisao = dt.timedelta(days=int(fonte.config["revisao_dias"]))
    assert pedidos["CPIAUCSL"] == (dt.date(2026, 8, 1) - revisao).isoformat()
    assert pedidos["GDPC1"] == INICIO_PADRAO.isoformat()


def test_sgs_pede_cada_serie_a_partir_do_que_ja_tem(ambiente_ingerido, monkeypatch):
    from tesouraria.sources.bcb_sgs import BcbSgsSource

    _rede_simulada(monkeypatch)
    fonte = BcbSgsSource()
    with db.connection(read_only=True) as con:
        fonte.prepare(con)

    inicios: dict[str, dt.date] = {}

    def falso_fetch(cfg, codigo, inicio, fim):
        inicios[codigo] = inicio
        return []

    fonte._fetch_serie = falso_fetch
    fonte.collect(since=SETE_DIAS)

    ultima_ipca = fonte._ultimas["433"]
    assert inicios["433"] <= ultima_ipca, "o IPCA seguinte ficaria fora da janela"
    assert inicios["433"] < SETE_DIAS
