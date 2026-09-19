"""Expectativas do relatório Focus, via API Olinda do Banco Central.

Coleta as projeções anuais (IPCA, Selic, câmbio e PIB) na versão geral e na
Top 5 — as cinco instituições mais assertivas. Ter as duas permite ver quando
o consenso e os melhores previsores discordam, o que costuma anteceder
revisão da curva.

Coleta também a competência **mensal** do IPCA, que serve a outra finalidade:
é a projeção do mês corrente usada para ratear a correção do índice entre dois
aniversários, no cálculo de um VNA ou da ponta IPCA de um swap. As três
variantes convivem na mesma tabela, separadas pela coluna `tipo`.

A API expõe duas bases de cálculo (últimos 30 e últimos 5 dias úteis); apenas
a de 30 dias é coletada, para que exista uma única leitura por data e
indicador.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

from tesouraria.sources.base import Source

logger = logging.getLogger(__name__)

INICIO_PADRAO = dt.date(2015, 1, 1)

CAMPOS = [
    "Indicador",
    "Data",
    "DataReferencia",
    "Media",
    "Mediana",
    "DesvioPadrao",
    "Minimo",
    "Maximo",
    "numeroRespondentes",
]


class FocusSource(Source):
    name = "focus"
    table = "focus"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        inicio = since or INICIO_PADRAO

        fixture_payload = None
        if self.offline:
            fixture_payload = json.loads(self.fixture_bundle())

        quadros: list[pd.DataFrame] = []
        for tipo, endpoint in cfg["endpoints"].items():
            try:
                if fixture_payload is not None:
                    valores = fixture_payload.get(tipo, {}).get("value", [])
                else:
                    valores = self._fetch(cfg, endpoint, tipo, inicio)
                quadros.append(self.parse({"value": valores}, tipo=tipo))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Focus (%s) falhou: %s", tipo, exc)

        quadros = [q for q in quadros if not q.empty]
        if not quadros:
            return pd.DataFrame()
        return pd.concat(quadros, ignore_index=True)

    def fixture_bundle(self) -> str:
        """Cada variante do Focus tem seu arquivo de amostra; aqui viram um pacote só."""
        nomes = self.config.get("fixtures", {})
        pacote = {tipo: json.loads(self.fixture(nome)) for tipo, nome in nomes.items()}
        return json.dumps(pacote)

    def _fetch(self, cfg: dict, endpoint: str, tipo: str, inicio: dt.date) -> list[dict]:
        alvos = cfg.get("indicadores_por_endpoint", {}).get(tipo, cfg["indicadores"])
        indicadores = " or ".join(f"Indicador eq '{i}'" for i in alvos)
        filtro = f"Data ge '{inicio.isoformat()}' and ({indicadores})"
        # Base de cálculo 0 = últimos 30 dias úteis, a leitura padrão do Focus.
        # No Top 5, tipoCalculo 'C' é o ranking de curto prazo.
        filtro += " and tipoCalculo eq 'C'" if tipo == "top5" else " and baseCalculo eq 0"

        pagina = int(cfg.get("pagina", 10000))
        valores: list[dict] = []
        skip = 0
        while True:
            # Consulta deliberadamente mínima. A primeira coleta real devolveu
            # `400 Bad Request`, e `$orderby` e `$select` eram os suspeitos —
            # o Olinda é restritivo quanto ao que aceita neles. Ordenar e
            # selecionar colunas sai mais barato no pandas do que arriscar a
            # requisição inteira.
            bruto = self.get(
                f"{cfg['url_base']}/{endpoint}",
                params={
                    "$top": pagina,
                    "$skip": skip,
                    "$filter": filtro,
                    "$format": "json",
                },
            )
            lote = json.loads(bruto.decode("utf-8")).get("value", [])
            valores.extend(lote)
            if len(lote) < pagina:
                break
            skip += pagina
        return valores

    @staticmethod
    def parse(payload: dict, tipo: str = "geral") -> pd.DataFrame:
        valores = payload.get("value", [])
        if not valores:
            return pd.DataFrame()

        df = pd.DataFrame(valores)
        out = pd.DataFrame(
            {
                "data_coleta": pd.to_datetime(df["Data"], errors="coerce").dt.date,
                "tipo": tipo,
                "indicador": df["Indicador"].astype(str).str.strip(),
                "data_referencia": df["DataReferencia"].astype(str).str.strip(),
                "mediana": pd.to_numeric(df.get("Mediana"), errors="coerce"),
                "media": pd.to_numeric(df.get("Media"), errors="coerce"),
                "desvio": pd.to_numeric(df.get("DesvioPadrao"), errors="coerce"),
                "minimo": pd.to_numeric(df.get("Minimo"), errors="coerce"),
                "maximo": pd.to_numeric(df.get("Maximo"), errors="coerce"),
                "n_respondentes": pd.to_numeric(
                    df.get("numeroRespondentes"), errors="coerce"
                ).astype("Int64"),
            }
        )
        out = out.dropna(subset=["data_coleta"])
        # Rede de segurança: campos novos na API não podem gerar duas linhas
        # com a mesma chave primária.
        out = out.drop_duplicates(
            subset=["data_coleta", "tipo", "indicador", "data_referencia"], keep="last"
        )
        return out.sort_values(["data_coleta", "indicador"]).reset_index(drop=True)
