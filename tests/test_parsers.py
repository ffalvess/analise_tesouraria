"""Testes dos parsers de cada fonte, contra as amostras de `data/fixtures/`.

Estes são os testes que substituem o acesso à rede: eles exercitam exatamente
o código que interpreta a resposta de cada servidor, sem depender de nenhum
estar no ar.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from tesouraria.sources.anbima_ettj import AnbimaEttjSource
from tesouraria.sources.b3_di import B3DiSource
from tesouraria.sources.bcb_sgs import parse_sgs
from tesouraria.sources.comex import ComexSource
from tesouraria.sources.focus import FocusSource
from tesouraria.sources.fx_flow import FxFlowSource
from tesouraria.sources.ibge_sidra import IbgeSidraSource
from tesouraria.sources.speeches import parse_bcb_json, parse_rss
from tesouraria.sources.tesouro_direto import TesouroDiretoSource
from tesouraria.sources.us_macro import UsMacroSource
from tesouraria.sources.us_treasury import USTreasurySource


def ler(fixtures_dir, nome: str) -> bytes:
    return (fixtures_dir / nome).read_bytes()


# -------------------------------------------------------------- Tesouro Direto


def test_tesouro_direto(fixtures_dir):
    df = TesouroDiretoSource().parse(ler(fixtures_dir, "tesouro_direto.csv"))

    assert not df.empty
    assert set(df.columns) >= {
        "data_ref", "vencimento", "instrumento", "taxa", "preco",
        "tipo", "prazo_anos", "prazo_du", "fonte",
    }
    assert (df["fonte"] == "tesouro").all()
    # Só as curvas a termo entram; o Tesouro Selic é pós-fixado e fica de fora.
    assert set(df["tipo"].unique()) <= {"pre", "ipca"}
    assert (df["prazo_anos"] > 0).all()
    assert (df["vencimento"] > df["data_ref"]).all()
    assert df["taxa"].notna().all()


def test_tesouro_direto_ordenado_por_prazo(fixtures_dir):
    df = TesouroDiretoSource().parse(ler(fixtures_dir, "tesouro_direto.csv"))
    ultimo_dia = df[(df["data_ref"] == df["data_ref"].max()) & (df["tipo"] == "pre")]
    assert list(ultimo_dia["prazo_anos"]) == sorted(ultimo_dia["prazo_anos"])


def test_tesouro_direto_chave_primaria_unica(fixtures_dir):
    """A gravação depende de a chave não repetir dentro do próprio lote."""
    df = TesouroDiretoSource().parse(ler(fixtures_dir, "tesouro_direto.csv"))
    chave = ["data_ref", "fonte", "tipo", "instrumento", "vencimento"]
    assert not df.duplicated(subset=chave).any()


# ---------------------------------------------------------------- US Treasury


@pytest.mark.parametrize(
    ("arquivo", "tipo", "minimo_vertices"),
    [("us_treasury_nominal.csv", "nominal", 13), ("us_treasury_real.csv", "real", 5)],
)
def test_us_treasury(fixtures_dir, arquivo, tipo, minimo_vertices):
    df = USTreasurySource().parse(ler(fixtures_dir, arquivo), tipo=tipo)

    assert not df.empty
    assert (df["tipo"] == tipo).all()
    assert df["prazo_anos"].notna().all()
    assert df.groupby("data_ref")["tenor"].nunique().max() >= minimo_vertices


def test_us_treasury_normaliza_rotulos(fixtures_dir):
    """O arquivo real usa '5 YR' e o nominal, '5 Yr'; os dois têm de casar."""
    nominal = USTreasurySource().parse(ler(fixtures_dir, "us_treasury_nominal.csv"), "nominal")
    real = USTreasurySource().parse(ler(fixtures_dir, "us_treasury_real.csv"), "real")

    assert "5 YR" in set(nominal["tenor"])
    assert "5 YR" in set(real["tenor"])


def test_us_treasury_descarta_rotulo_desconhecido():
    """Um vértice novo no arquivo é ignorado, não vira prazo nulo."""
    csv = b'Date,"3 Mo","42 Fortnights"\n08/28/2026,4.30,9.99\n'
    df = USTreasurySource().parse(csv, tipo="nominal")
    assert list(df["tenor"]) == ["3 MO"]


# ------------------------------------------------------------------ BCB SGS


def test_parse_sgs(fixtures_dir):
    payload = json.loads(ler(fixtures_dir, "bcb_sgs.json"))
    df = parse_sgs(payload["432"], "432")

    assert not df.empty
    assert (df["serie_id"] == "432").all()
    assert isinstance(df["data_ref"].iloc[0], dt.date)
    assert df["valor"].notna().all()


def test_parse_sgs_vazio():
    assert parse_sgs([], "999").empty


def test_parse_sgs_formato_invalido():
    with pytest.raises(ValueError, match="formato inesperado"):
        parse_sgs([{"quando": "01/01/2026", "quanto": "1"}], "999")


# -------------------------------------------------------------- fluxo cambial


def test_fx_flow_pivot():
    """A lógica de pivô continua correta, mesmo com a fonte desativada.

    O teste monta a entrada longa diretamente em vez de ler a lista de séries
    da configuração: os códigos do SGS foram removidos de lá por estarem
    errados, e a mecânica do pivô não deveria depender disso para ser testada.
    """
    datas = [dt.date(2026, 8, 21), dt.date(2026, 8, 28)]
    partes = []
    for segmento, base in (("comercial", 5_000.0), ("financeiro", 9_000.0), ("total", 14_000.0)):
        for medida, ajuste in (("compras", 0.0), ("vendas", -1_200.0), ("saldo", 1_200.0)):
            partes.append(
                pd.DataFrame(
                    {
                        "serie_id": f"{segmento}-{medida}",
                        "data_ref": datas,
                        "valor": [base + ajuste, base + ajuste + 100],
                        "segmento": segmento,
                        "medida": medida,
                    }
                )
            )

    largo = FxFlowSource.pivot(pd.concat(partes, ignore_index=True))

    assert set(largo.columns) == {
        "data_ref", "periodicidade", "segmento", "compras", "vendas", "saldo"
    }
    assert set(largo["segmento"].unique()) == {"comercial", "financeiro", "total"}
    assert not largo.duplicated(subset=["data_ref", "periodicidade", "segmento"]).any()


def test_fx_flow_deduz_saldo_ausente():
    """Quando o BCB publica só as pernas, o saldo tem de sair delas."""
    longo = pd.DataFrame(
        {
            "data_ref": [dt.date(2026, 8, 21)] * 2,
            "segmento": ["comercial"] * 2,
            "medida": ["compras", "vendas"],
            "valor": [5000.0, 3800.0],
        }
    )
    largo = FxFlowSource.pivot(longo)
    assert largo["saldo"].iloc[0] == pytest.approx(1200.0)


# ------------------------------------------------------------------- Focus


@pytest.mark.parametrize(("arquivo", "tipo"), [("focus_geral.json", "geral"), ("focus_top5.json", "top5")])
def test_focus(fixtures_dir, arquivo, tipo):
    payload = json.loads(ler(fixtures_dir, arquivo))
    df = FocusSource.parse(payload, tipo=tipo)

    assert not df.empty
    assert (df["tipo"] == tipo).all()
    assert df["mediana"].notna().all()
    # A chave primária tem de ser única dentro do lote.
    assert not df.duplicated(
        subset=["data_coleta", "tipo", "indicador", "data_referencia"]
    ).any()


def test_focus_vazio():
    assert FocusSource.parse({"value": []}).empty


# ------------------------------------------------------------------- ANBIMA


def test_anbima_ettj(fixtures_dir):
    df = AnbimaEttjSource().parse(ler(fixtures_dir, "anbima_ettj.txt"))

    assert not df.empty
    assert (df["fonte"] == "anbima").all()
    assert set(df["tipo"].unique()) == {"ipca", "pre", "implicita"}
    assert (df["prazo_du"] > 0).all()
    # O prazo em anos vem do vértice em dias úteis, base 252.
    assert df["prazo_anos"].max() == pytest.approx(df["prazo_du"].max() / 252)


def test_anbima_deduz_data_do_cabecalho(fixtures_dir):
    df = AnbimaEttjSource().parse(ler(fixtures_dir, "anbima_ettj.txt"))
    assert isinstance(df["data_ref"].iloc[0], dt.date)


def test_anbima_sem_cabecalho_reconhecivel():
    with pytest.raises(ValueError, match="vértices"):
        AnbimaEttjSource().parse(b"arquivo qualquer\nsem tabela\n")


# ----------------------------------------------------------------------- B3


def test_b3_di(fixtures_dir):
    data_ref = dt.date(2026, 8, 28)
    df = B3DiSource().parse(ler(fixtures_dir, "b3_di.html"), data_ref=data_ref)

    assert not df.empty
    assert (df["fonte"] == "b3").all()
    assert (df["tipo"] == "pre").all()
    assert (df["vencimento"] > data_ref).all()
    # Taxas de DI plausíveis: entre 1% e 40% ao ano.
    assert df["taxa"].between(1, 40).all()
    assert list(df["prazo_anos"]) == sorted(df["prazo_anos"])


def test_b3_converte_pu_em_taxa():
    """PU de 100.000 em 252 dias úteis equivale a taxa zero."""
    fonte = B3DiSource()
    html = (
        "<table><tr><th>VENCTO</th><th>AJUSTE</th></tr>"
        "<tr><td>F28</td><td>100000,00</td></tr></table>"
    ).encode("latin-1")
    df = fonte.parse(html, data_ref=dt.date(2027, 1, 4))
    assert df["taxa"].iloc[0] == pytest.approx(0.0, abs=1e-9)


def test_b3_codigo_de_vencimento():
    fonte = B3DiSource()
    assert fonte._vencimento("F28", dt.date(2026, 8, 28)) == dt.date(2028, 1, 3)  # 1/1/28 é sábado
    assert fonte._vencimento("N27", dt.date(2026, 8, 28)) == dt.date(2027, 7, 1)
    assert fonte._vencimento("XX", dt.date(2026, 8, 28)) is None


# -------------------------------------------------------------------- SIDRA


def test_ibge_sidra(fixtures_dir):
    payload = json.loads(ler(fixtures_dir, "ibge_sidra.json"))
    df = IbgeSidraSource.parse(payload["1737-63"], "1737-63")

    assert not df.empty
    assert (df["serie_id"] == "1737-63").all()
    assert isinstance(df["data_ref"].iloc[0], dt.date)


def test_ibge_sidra_periodo_anual(fixtures_dir):
    payload = json.loads(ler(fixtures_dir, "ibge_sidra.json"))
    df = IbgeSidraSource.parse(payload["5932-6564"], "5932-6564")
    assert df["data_ref"].iloc[0].month == 1


def test_ibge_sidra_sem_coluna_de_periodo():
    payload = [{"NC": "Nível Territorial (Código)", "V": "Valor"}, {"NC": "1", "V": "1"}]
    with pytest.raises(ValueError, match="período"):
        IbgeSidraSource.parse(payload, "x")


# --------------------------------------------------------------------- FRED


def test_us_macro(fixtures_dir):
    payload = json.loads(ler(fixtures_dir, "us_macro.json"))
    df = UsMacroSource.parse(payload["UNRATE"], "UNRATE")

    assert not df.empty
    assert (df["serie_id"] == "UNRATE").all()
    assert df["valor"].notna().all()


def test_us_macro_valor_ausente():
    """O FRED marca dado indisponível com '.'; tem de virar NaN, não erro."""
    payload = {"observations": [{"date": "2026-01-01", "value": "."}]}
    df = UsMacroSource.parse(payload, "X")
    assert df["valor"].isna().all()


# ---------------------------------------------------------------- Comex Stat


def test_comex(fixtures_dir):
    payload = json.loads(ler(fixtures_dir, "comex.json"))
    df = ComexSource.parse(payload["export"], "export")

    assert not df.empty
    assert (df["serie_id"] == "comex_export").all()
    assert list(df["data_ref"]) == sorted(df["data_ref"])


def test_comex_colunas_faltando():
    with pytest.raises(ValueError, match="colunas"):
        ComexSource.parse({"data": {"list": [{"year": 2026}]}}, "export")


# ---------------------------------------------------------------- documentos


def test_parse_rss():
    rss = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Speech on monetary policy</title>
        <link>https://exemplo.invalido/discurso/1</link>
        <description>The Committee remains &lt;b&gt;restrictive&lt;/b&gt;.</description>
        <pubDate>Fri, 28 Aug 2026 14:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    itens = parse_rss(rss, {"nome": "Fed", "instituicao": "Fed", "idioma": "en"})

    assert len(itens) == 1
    assert itens[0]["data_pub"] == dt.date(2026, 8, 28)
    # As tags HTML da descrição têm de sair do texto.
    assert "<b>" not in itens[0]["texto"]
    assert "restrictive" in itens[0]["texto"]


def test_parse_bcb_json_resolve_url_relativa():
    payload = json.dumps(
        {
            "conteudo": [
                {
                    "titulo": "Discurso do presidente",
                    "Url": "/detalhenoticia/123",
                    "DataPublicacao": "2026-08-20T10:00:00",
                    "textoInformacao": "O Copom mantém vigilância.",
                }
            ]
        }
    ).encode("utf-8")
    itens = parse_bcb_json(payload, {"nome": "BCB", "instituicao": "BCB", "idioma": "pt"})

    assert itens[0]["url"].startswith("https://www.bcb.gov.br/")
    assert itens[0]["data_pub"] == dt.date(2026, 8, 20)


def test_documentos_recebem_id_estavel():
    """O mesmo URL tem de gerar sempre o mesmo id, para não duplicar na base."""
    from tesouraria.sources.speeches import doc_id

    assert doc_id("https://a.invalido/x") == doc_id("https://a.invalido/x")
    assert doc_id("https://a.invalido/x") != doc_id("https://a.invalido/y")


def test_b3_sem_tabela_descreve_o_que_veio():
    """`No tables found` sozinho não diz se é manutenção, login ou JavaScript.

    A B3 só responde de dentro do workflow, então a mensagem do log é a única
    evidência disponível para a próxima correção.
    """
    from tesouraria.sources.b3_di import B3DiSource

    pagina = (
        "<html><head><title>Sistema indisponível</title></head>"
        "<body><p>Estamos em manutenção. Tente mais tarde.</p></body></html>"
    ).encode("latin-1")

    with pytest.raises(ValueError) as erro:
        B3DiSource().parse(pagina, data_ref=dt.date(2026, 8, 28))

    mensagem = str(erro.value)
    assert "Sistema indisponível" in mensagem
    assert "manutenção" in mensagem


def test_b3_sem_tabela_e_resposta_vazia():
    from tesouraria.sources.b3_di import B3DiSource

    with pytest.raises(ValueError, match="resposta vazia"):
        B3DiSource().parse(b"   ", data_ref=dt.date(2026, 8, 28))


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("22704-sgs", "22704"),
        ("1-taxa-de-cambio---livre---dolar-americano-venda---diario", "1"),
        ("20542-saldo-da-carteira-de-credito-com-recursos-livres---total", "20542"),
        ("sgs-sem-codigo", None),
        ("", None),
    ],
)
def test_codigo_sai_do_identificador_do_conjunto(nome, esperado):
    """O portal nomeia os conjuntos de duas formas; as duas abrem pelo código."""
    from tesouraria.cli import extrair_codigo

    assert extrair_codigo(nome) == esperado
