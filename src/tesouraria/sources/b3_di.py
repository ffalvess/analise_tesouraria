"""Curva prefixada a partir dos futuros de DI de um dia (DI1) da B3.

É a curva que o mercado efetivamente negocia e a mais líquida do país — por
isso costuma reagir antes da curva de títulos a discursos do Copom e a
surpresas de inflação.

O boletim da B3 publica o preço de ajuste (PU) de cada vencimento. A taxa sai
da relação entre o PU e o valor de face de 100.000, capitalizada em dias
úteis:

    taxa = (100000 / PU) ** (252 / du) - 1
"""

from __future__ import annotations

import datetime as dt
import io
import logging

import pandas as pd

from tesouraria.sources.base import Source, business_days

logger = logging.getLogger(__name__)

VALOR_FACE = 100_000.0


class B3DiSource(Source):
    name = "b3_di"
    table = "curve_br"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        quadros: list[pd.DataFrame] = []

        for data_ref in self._datas(since):
            try:
                raw = self.get(
                    cfg["url"],
                    fixture=cfg.get("fixture"),
                    params={**cfg.get("params", {}), "Data": data_ref.strftime("%d/%m/%Y")},
                )
                quadros.append(self.parse(raw, data_ref=data_ref))
            except Exception as exc:  # noqa: BLE001 — feriado e dia sem pregão são esperados
                logger.info("B3 DI1 sem dados para %s: %s", data_ref, exc)

        quadros = [q for q in quadros if not q.empty]
        if not quadros:
            return pd.DataFrame()
        return pd.concat(quadros, ignore_index=True)

    def parse(self, raw: bytes, data_ref: dt.date) -> pd.DataFrame:
        texto = raw.decode(self.config.get("encoding", "latin-1"), errors="replace")
        tabelas = pd.read_html(io.StringIO(texto), decimal=",", thousands=".")

        df = self._tabela_de_ajustes(tabelas)
        if df is None:
            raise ValueError("boletim da B3 sem tabela de ajustes reconhecível")

        col_vencto = self._coluna(df, ["VENCTO", "VENCIMENTO"])
        col_ajuste = self._coluna(df, ["AJUSTE"])
        if col_vencto is None or col_ajuste is None:
            raise ValueError("boletim da B3 sem colunas de vencimento e ajuste")

        codigos = df[col_vencto].astype(str).str.strip().str.upper()
        vencimentos = codigos.map(lambda c: self._vencimento(c, data_ref))
        pu = pd.to_numeric(df[col_ajuste], errors="coerce")

        out = pd.DataFrame(
            {
                "data_ref": data_ref,
                "fonte": "b3",
                "tipo": "pre",
                "instrumento": "DI1" + codigos,
                "vencimento": vencimentos,
                "preco": pu,
            }
        ).dropna(subset=["vencimento", "preco"])
        out = out[out["preco"] > 0]

        out["prazo_du"] = business_days(
            pd.Series([data_ref] * len(out), index=out.index), out["vencimento"]
        )
        out = out[out["prazo_du"] > 0]
        out["prazo_anos"] = out["prazo_du"].astype(float) / 252.0
        out["taxa"] = ((VALOR_FACE / out["preco"]) ** (252.0 / out["prazo_du"]) - 1) * 100

        return out.sort_values("prazo_anos").reset_index(drop=True)

    # ---------------------------------------------------------------- apoio

    @staticmethod
    def _tabela_de_ajustes(tabelas: list[pd.DataFrame]) -> pd.DataFrame | None:
        """A página traz várias tabelas; vale a que tem vencimento e ajuste."""
        for tabela in tabelas:
            colunas = {str(c).upper() for c in tabela.columns}
            if any("AJUSTE" in c for c in colunas) and any(
                "VENC" in c for c in colunas
            ):
                return tabela
        return None

    @staticmethod
    def _coluna(df: pd.DataFrame, chaves: list[str]) -> str | None:
        for coluna in df.columns:
            alvo = str(coluna).upper()
            if any(chave in alvo for chave in chaves):
                return coluna
        return None

    def _vencimento(self, codigo: str, data_ref: dt.date) -> dt.date | None:
        """Converte 'F27' no primeiro dia útil de janeiro de 2027.

        Contratos de DI vencem no primeiro dia útil do mês de referência.
        """
        meses = self.config.get("meses", {})
        if len(codigo) < 2 or codigo[0] not in meses:
            return None
        try:
            ano_curto = int(codigo[1:])
        except ValueError:
            return None

        # Código de dois dígitos: resolve o século pela data de referência.
        seculo = (data_ref.year // 100) * 100
        ano = seculo + ano_curto if ano_curto < 100 else ano_curto
        if ano < data_ref.year:
            ano += 100

        primeiro = pd.Timestamp(year=ano, month=meses[codigo[0]], day=1)
        if primeiro.weekday() >= 5:  # sábado ou domingo
            primeiro = primeiro + pd.offsets.BDay(1)
        return primeiro.date()

    def _datas(self, since: dt.date | None) -> list[dt.date]:
        """Dias úteis a coletar, limitados a `max_dias_por_execucao`.

        Um boletim por pregão: mesma limitação da ANBIMA. O histórico longo da
        curva brasileira vem do Tesouro Direto, num CSV único.
        """
        hoje = dt.date.today()
        if since is None:
            return [d.date() for d in pd.bdate_range(end=hoje, periods=1)]

        datas = [d.date() for d in pd.bdate_range(start=since, end=hoje)]
        teto = int(self.config.get("max_dias_por_execucao", 30))
        return datas[-teto:] if teto > 0 else datas
