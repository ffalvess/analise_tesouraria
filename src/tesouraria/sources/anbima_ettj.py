"""Estrutura a termo da taxa de juros (ETTJ) da ANBIMA.

É a curva de referência do mercado brasileiro: já vem suavizada e por vértice
em dias úteis, nas três leituras — prefixada, IPCA e inflação implícita. Serve
de contraponto à curva construída a partir dos títulos do Tesouro.

O arquivo diário da ANBIMA não tem esquema publicado e já mudou de layout ao
longo dos anos, então o parser localiza o cabeçalho pelo conteúdo em vez de
assumir uma posição fixa de linha.
"""

from __future__ import annotations

import datetime as dt
import io
import logging

import pandas as pd

from tesouraria.sources.base import Source

logger = logging.getLogger(__name__)

# Palavra-chave no nome da coluna -> tipo de curva na tabela `curve_br`.
MAPA_TIPOS = {
    "IPCA": "ipca",
    "PREF": "pre",
    "IMPLIC": "implicita",
    "INFLA": "implicita",
}


class AnbimaEttjSource(Source):
    name = "anbima_ettj"
    table = "curve_br"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        datas = self._datas(since)
        quadros: list[pd.DataFrame] = []

        for data_ref in datas:
            try:
                raw = self.get(
                    cfg["url"],
                    fixture=cfg.get("fixture"),
                    method=cfg.get("metodo", "POST"),
                    data={
                        "Dt_Ref": data_ref.strftime("%d/%m/%Y"),
                        "escolha": "2",
                        "Idioma": "PT",
                        "saida": "csv",
                    },
                )
                quadros.append(self.parse(raw, data_ref=data_ref))
            except Exception as exc:  # noqa: BLE001 — dia sem pregão é comum
                logger.info("ANBIMA ETTJ sem dados para %s: %s", data_ref, exc)

        quadros = [q for q in quadros if not q.empty]
        if not quadros:
            return pd.DataFrame()
        return pd.concat(quadros, ignore_index=True)

    def parse(self, raw: bytes, data_ref: dt.date | None = None) -> pd.DataFrame:
        texto = raw.decode(self.config.get("encoding", "latin-1"), errors="replace")
        linhas = texto.splitlines()

        cabecalho = next(
            (i for i, linha in enumerate(linhas) if "vert" in linha.lower()), None
        )
        if cabecalho is None:
            raise ValueError("arquivo da ANBIMA sem linha de vértices reconhecível")

        if data_ref is None:
            data_ref = self._data_do_cabecalho(linhas[:cabecalho])

        tabela = "\n".join(linhas[cabecalho:])
        df = pd.read_csv(io.StringIO(tabela), sep=";", decimal=",", engine="python")
        df.columns = [str(c).strip() for c in df.columns]

        coluna_vertice = df.columns[0]
        df[coluna_vertice] = pd.to_numeric(df[coluna_vertice], errors="coerce")
        df = df.dropna(subset=[coluna_vertice])

        registros: list[pd.DataFrame] = []
        for coluna in df.columns[1:]:
            tipo = self._tipo_da_coluna(coluna)
            if tipo is None:
                continue
            taxa = pd.to_numeric(df[coluna], errors="coerce")
            parcial = pd.DataFrame(
                {
                    "data_ref": data_ref,
                    "fonte": "anbima",
                    "tipo": tipo,
                    "instrumento": f"ETTJ {tipo}",
                    "vencimento": pd.NaT,
                    "prazo_du": df[coluna_vertice].astype(int),
                    "prazo_anos": df[coluna_vertice] / 252.0,
                    "taxa": taxa,
                    "preco": pd.NA,
                }
            ).dropna(subset=["taxa"])
            registros.append(parcial)

        if not registros:
            return pd.DataFrame()

        out = pd.concat(registros, ignore_index=True)
        # A ETTJ não tem vencimento de título; o vértice é a identidade da linha,
        # e a chave primária o incorpora pelo instrumento.
        out["instrumento"] = out["instrumento"] + " " + out["prazo_du"].astype(str) + "du"
        out["vencimento"] = None
        return out.sort_values(["data_ref", "tipo", "prazo_anos"]).reset_index(drop=True)

    @staticmethod
    def _tipo_da_coluna(coluna: str) -> str | None:
        alvo = coluna.upper()
        for chave, tipo in MAPA_TIPOS.items():
            if chave in alvo:
                return tipo
        return None

    @staticmethod
    def _data_do_cabecalho(linhas: list[str]) -> dt.date:
        for linha in linhas:
            achado = pd.Series([linha]).str.extract(r"(\d{2}/\d{2}/\d{4})")[0].iloc[0]
            if isinstance(achado, str):
                return dt.datetime.strptime(achado, "%d/%m/%Y").date()
        raise ValueError("não foi possível deduzir a data de referência do arquivo")

    @staticmethod
    def _datas(since: dt.date | None) -> list[dt.date]:
        """Dias úteis entre `since` e hoje; sem `since`, apenas o último dia útil."""
        hoje = dt.date.today()
        if since is None:
            return [d.date() for d in pd.bdate_range(end=hoje, periods=1)]
        return [d.date() for d in pd.bdate_range(start=since, end=hoje)]
