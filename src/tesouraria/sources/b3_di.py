"""Curvas negociadas na B3: futuro de DI de um dia (DI1) e de cupom de IPCA (DAP).

São as curvas que o mercado efetivamente negocia e as mais líquidas do país —
por isso costumam reagir antes da curva de títulos a discursos do Copom e a
surpresas de inflação.

O boletim publica o preço de ajuste (PU) de cada vencimento, e os dois
contratos têm a mesma mecânica: valor de face de 100.000 descontado pela taxa
em dias úteis. A taxa sai da relação entre os dois:

    taxa = (100000 / PU) ** (252 / du) - 1

O que muda de um para o outro é o **vencimento** — DI1 vence no primeiro dia
útil do mês de referência, DAP no dia 15 (ou no dia útil seguinte) — e o que a
taxa significa: o DI1 é juro nominal, o DAP é o cupom de IPCA. A razão entre os
fatores acumulados das duas é a inflação implícita negociada, que é como o
mercado precifica um swap CDI × IPCA (ver `analytics/swap.py`).

O prazo aqui é contado pelo calendário de feriados (`analytics/calendario.py`),
e não pela aproximação de `sources/base.py`: como a taxa é *derivada* do PU,
um dia útil a mais ou a menos entraria direto no número gravado no banco.

O nome da fonte continua `b3_di` por compatibilidade com o histórico de
`ingest_log` e com os snapshots já versionados.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re

import pandas as pd

from tesouraria.analytics import calendario as cal
from tesouraria.sources.base import Source

logger = logging.getLogger(__name__)

VALOR_FACE = 100_000.0


def _amostra(texto: str, limite: int = 300) -> str:
    """Título e começo do conteúdo, para o log dizer o que a B3 devolveu."""
    if not texto.strip():
        return "resposta vazia"

    titulo = re.search(r"<title[^>]*>(.*?)</title>", texto, re.IGNORECASE | re.DOTALL)
    visivel = re.sub(r"<[^>]+>", " ", texto)
    visivel = " ".join(visivel.split())[:limite]

    partes = [f"{len(texto)} caracteres"]
    if titulo:
        partes.append(f"título {titulo.group(1).strip()!r}")
    partes.append(f"texto {visivel!r}" if visivel else "sem texto visível")
    return "; ".join(partes)


class B3DiSource(Source):
    name = "b3_di"
    table = "curve_br"

    def collect(self, since: dt.date | None = None) -> pd.DataFrame:
        cfg = self.config
        quadros: list[pd.DataFrame] = []
        datas = self._datas(since)
        ultimo_erro: str | None = None
        consultas = 0

        for data_ref in datas:
            for mercadoria in self.mercadorias():
                consultas += 1
                try:
                    raw = self.get(
                        cfg["url"],
                        fixture=mercadoria.get("fixture"),
                        params={
                            **cfg.get("params", {}),
                            "Mercadoria": mercadoria["codigo"],
                            "Data": data_ref.strftime("%d/%m/%Y"),
                        },
                    )
                    quadros.append(self.parse(raw, data_ref=data_ref, mercadoria=mercadoria))
                except Exception as exc:  # noqa: BLE001 — feriado e dia sem pregão são esperados
                    ultimo_erro = f"{mercadoria['codigo']}/{data_ref}: {type(exc).__name__}: {exc}"
                    logger.warning(
                        "B3 %s sem dados para %s: %s", mercadoria["codigo"], data_ref, exc
                    )

        quadros = [q for q in quadros if not q.empty]
        if not quadros:
            # Nenhum pregão rendeu dado. Levantar, em vez de devolver vazio, é o
            # que distingue "a fonte está quebrada" de "não houve pregão" — as
            # duas apareciam como `vazio` no rodapé, e foi assim que a falta do
            # html5lib passou despercebida na primeira coleta real.
            raise RuntimeError(
                f"nenhuma das {consultas} consultas devolveu dados; último erro: {ultimo_erro}"
            )
        return pd.concat(quadros, ignore_index=True)

    def mercadorias(self) -> list[dict]:
        """Contratos a coletar, do arquivo de configuração.

        O fallback cobre uma configuração antiga, de quando a fonte só conhecia
        o DI1: sem isso, um `sources.yaml` desatualizado deixaria de coletar em
        silêncio, que é o modo de falhar que este projeto mais evita.
        """
        cfg = self.config
        if cfg.get("mercadorias"):
            return list(cfg["mercadorias"])
        return [
            {
                "codigo": cfg.get("params", {}).get("Mercadoria", "DI1"),
                "tipo": "pre",
                "vencimento": "primeiro_dia_util",
                "fixture": cfg.get("fixture"),
            }
        ]

    def parse(
        self, raw: bytes, data_ref: dt.date, mercadoria: dict | None = None
    ) -> pd.DataFrame:
        mercadoria = mercadoria or self.mercadorias()[0]
        texto = raw.decode(self.config.get("encoding", "latin-1"), errors="replace")
        # flavor explicito: o padrao do pandas cai em bs4+html5lib, que pode nao
        # existir num ambiente limpo. lxml ja e dependencia declarada.
        try:
            tabelas = pd.read_html(io.StringIO(texto), decimal=",", thousands=".", flavor="lxml")
        except Exception as exc:  # noqa: BLE001 — lxml também levanta XMLSyntaxError
            # `No tables found` diz que a página não é o boletim, e nada sobre o
            # que ela é: aviso de manutenção, redirecionamento para login, casca
            # de JavaScript. Sem a amostra, a correção vira tentativa e erro num
            # host que só responde de dentro do workflow.
            raise ValueError(f"{exc} — recebido: {_amostra(texto)}") from exc

        df = self._tabela_de_ajustes(tabelas)
        if df is None:
            raise ValueError("boletim da B3 sem tabela de ajustes reconhecível")

        col_vencto = self._coluna(df, ["VENCTO", "VENCIMENTO"])
        col_ajuste = self._coluna(df, ["AJUSTE"])
        if col_vencto is None or col_ajuste is None:
            raise ValueError("boletim da B3 sem colunas de vencimento e ajuste")

        regra = mercadoria.get("vencimento", "primeiro_dia_util")
        codigos = df[col_vencto].astype(str).str.strip().str.upper()
        vencimentos = codigos.map(lambda c: self._vencimento(c, data_ref, regra))
        pu = pd.to_numeric(df[col_ajuste], errors="coerce")

        out = pd.DataFrame(
            {
                "data_ref": data_ref,
                "fonte": "b3",
                "tipo": mercadoria.get("tipo", "pre"),
                "instrumento": mercadoria["codigo"] + codigos,
                "vencimento": vencimentos,
                "preco": pu,
            }
        ).dropna(subset=["vencimento", "preco"])
        out = out[out["preco"] > 0]

        out["prazo_du"] = [cal.dias_uteis(data_ref, venc) for venc in out["vencimento"]]
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

    def _vencimento(
        self, codigo: str, data_ref: dt.date, regra: str = "primeiro_dia_util"
    ) -> dt.date | None:
        """Converte 'F27' no vencimento do contrato de janeiro de 2027.

        O DI1 vence no primeiro dia útil do mês de referência; o DAP, no dia 15.
        Em qualquer dos dois, cair em fim de semana ou feriado empurra para o
        próximo pregão — é o que faz o DAP de novembro de 2026 vencer em 16/11.
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

        dia = 15 if regra == "dia_15" else 1
        return cal.proximo_dia_util(dt.date(ano, meses[codigo[0]], dia))

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
