"""Balança comercial: exportações, importações e a origem do fluxo comercial.

O saldo comercial é a fonte econômica do fluxo cambial comercial — mas os dois
não coincidem. Exportação registrada não é dólar internalizado: o exportador
escolhe quando fechar o câmbio. O descompasso entre as duas séries é
informativo, e é o que a última aba mostra.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesouraria.ui import charts, common

common.configurar("Balança comercial", "🚢")

if not common.exigir_dados():
    st.stop()

# Preferência pelo Comex Stat, que é a fonte primária; o SGS entra como reserva.
export = common.cache_serie("comex_export")
import_ = common.cache_serie("comex_import")
saldo = common.cache_serie("comex_saldo")
escala = 1e-6  # o Comex Stat vem em US$; as séries do SGS, em US$ milhões
origem = "Comex Stat (MDIC)"

if export.empty or import_.empty:
    export = common.cache_serie("2255")
    import_ = common.cache_serie("2256")
    saldo = common.cache_serie("2257")
    escala = 1.0
    origem = "SGS/Banco Central"

if export.empty or import_.empty:
    st.warning(
        "Sem dados de balança comercial. Rode `tesouraria ingest --source comex` ou "
        "`--source bcb_sgs` (séries 2255, 2256 e 2257)."
    )
    st.stop()

for quadro in (export, import_, saldo):
    if not quadro.empty:
        quadro["valor"] = pd.to_numeric(quadro["valor"], errors="coerce") * escala

st.caption(f"Fonte em uso: **{origem}**. Valores em US$ milhões (FOB).")

colunas = st.columns(4)
colunas[0].metric(
    "Exportações (último mês)", f"US$ {export['valor'].iloc[-1]:,.0f} mi".replace(",", ".")
)
colunas[1].metric(
    "Importações (último mês)", f"US$ {import_['valor'].iloc[-1]:,.0f} mi".replace(",", ".")
)
if not saldo.empty:
    colunas[2].metric("Saldo", f"US$ {saldo['valor'].iloc[-1]:,.0f} mi".replace(",", "."))
    doze = saldo.tail(12)["valor"].sum()
    colunas[3].metric("Saldo acumulado 12 meses", f"US$ {doze:,.0f} mi".replace(",", "."))

aba_fluxos, aba_saldo, aba_relacao = st.tabs(
    ["Exportação e importação", "Saldo", "Saldo × fluxo cambial comercial"]
)

with aba_fluxos:
    st.plotly_chart(
        charts.grafico_series(
            [("Exportações", export), ("Importações", import_)],
            titulo="Comércio exterior mensal",
            eixo_y="US$ milhões",
            cores=[charts.VERDE, charts.US],
        ),
        width="stretch",
    )

    corrente = pd.merge(
        export[["data_ref", "valor"]].rename(columns={"valor": "export"}),
        import_[["data_ref", "valor"]].rename(columns={"valor": "import"}),
        on="data_ref",
        how="inner",
    )
    corrente["valor"] = corrente["export"] + corrente["import"]
    st.plotly_chart(
        charts.grafico_series(
            [("Corrente de comércio", corrente)],
            titulo="Corrente de comércio (exportação + importação)",
            eixo_y="US$ milhões",
            cores=[charts.BR],
        ),
        width="stretch",
    )

with aba_saldo:
    if saldo.empty:
        st.info("Série de saldo indisponível.")
    else:
        mensal = saldo[["data_ref", "valor"]].copy()
        mensal["valor"] = pd.to_numeric(mensal["valor"], errors="coerce")
        acumulado = pd.DataFrame(
            {"data_ref": mensal["data_ref"], "valor": mensal["valor"].rolling(12).sum()}
        )

        st.plotly_chart(
            charts.grafico_series(
                [("Saldo mensal", mensal)],
                titulo="Saldo comercial mensal",
                eixo_y="US$ milhões",
                cores=[charts.VERDE],
            ),
            width="stretch",
        )
        st.plotly_chart(
            charts.grafico_series(
                [("Acumulado 12 meses", acumulado)],
                titulo="Saldo acumulado em 12 meses",
                eixo_y="US$ milhões",
                cores=[charts.BR],
            ),
            width="stretch",
        )
        st.caption(
            "O acumulado em 12 meses remove a sazonalidade da safra e é a leitura que "
            "importa para a conta corrente."
        )

with aba_relacao:
    fluxo = common.cache_fluxo()
    comercial = fluxo[fluxo["segmento"] == "comercial"] if not fluxo.empty else pd.DataFrame()

    if comercial.empty or saldo.empty:
        st.info(
            "Esta comparação precisa do saldo comercial e do fluxo cambial comercial. "
            "Rode `tesouraria ingest --source fx_flow`."
        )
    else:
        mensal_fluxo = comercial.copy()
        mensal_fluxo["data_ref"] = pd.to_datetime(mensal_fluxo["data_ref"])
        mensal_fluxo = (
            mensal_fluxo.set_index("data_ref")["saldo"]
            .apply(pd.to_numeric, errors="coerce")
            .resample("MS")
            .sum(min_count=1)
            .reset_index()
            .rename(columns={"saldo": "valor"})
        )

        mensal_saldo = saldo[["data_ref", "valor"]].copy()
        mensal_saldo["data_ref"] = pd.to_datetime(mensal_saldo["data_ref"])

        st.plotly_chart(
            charts.grafico_series(
                [
                    ("Saldo comercial (balança)", mensal_saldo),
                    ("Fluxo cambial comercial", mensal_fluxo),
                ],
                titulo="Saldo registrado × dólares efetivamente internalizados",
                eixo_y="US$ milhões",
                cores=[charts.VERDE, charts.BR],
            ),
            width="stretch",
        )

        juntos = pd.merge(
            mensal_saldo.rename(columns={"valor": "saldo_balanca"}),
            mensal_fluxo.rename(columns={"valor": "fluxo_comercial"}),
            on="data_ref",
            how="inner",
        ).dropna()

        if len(juntos) > 6:
            juntos["descasamento"] = juntos["saldo_balanca"] - juntos["fluxo_comercial"]
            correlacao = juntos["saldo_balanca"].corr(juntos["fluxo_comercial"])
            st.metric("Correlação no período", f"{correlacao:.3f}")
            st.plotly_chart(
                charts.grafico_series(
                    [("Descasamento", juntos.rename(columns={"descasamento": "valor"}))],
                    titulo="Saldo da balança menos fluxo internalizado",
                    eixo_y="US$ milhões",
                    cores=[charts.AMBAR],
                ),
                width="stretch",
            )
            st.caption(
                "Descasamento positivo e persistente indica exportador segurando dólar no "
                "exterior — receita registrada que ainda não virou oferta de moeda no "
                "mercado local, e que pode entrar depois."
            )

common.rodape()
