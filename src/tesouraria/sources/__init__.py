"""Registro das fontes de dados.

A ordem importa: as curvas e as séries base entram antes dos documentos, para
que uma execução interrompida no meio ainda deixe o aplicativo utilizável.
"""

from __future__ import annotations

from tesouraria.sources.anbima_ettj import AnbimaEttjSource
from tesouraria.sources.b3_di import B3DiSource
from tesouraria.sources.base import IngestResult, Source
from tesouraria.sources.bcb_sgs import BcbSgsSource
from tesouraria.sources.comex import ComexSource
from tesouraria.sources.focus import FocusSource
from tesouraria.sources.fx_flow import FxFlowSource
from tesouraria.sources.ibge_sidra import IbgeSidraSource
from tesouraria.sources.research import ResearchSource
from tesouraria.sources.speeches import SpeechesSource
from tesouraria.sources.tesouro_direto import TesouroDiretoSource
from tesouraria.sources.us_macro import UsMacroSource
from tesouraria.sources.us_treasury import USTreasurySource

REGISTRO: dict[str, type[Source]] = {
    "tesouro_direto": TesouroDiretoSource,
    "us_treasury": USTreasurySource,
    "anbima_ettj": AnbimaEttjSource,
    "b3_di": B3DiSource,
    "bcb_sgs": BcbSgsSource,
    "fx_flow": FxFlowSource,
    "focus": FocusSource,
    "comex": ComexSource,
    "ibge_sidra": IbgeSidraSource,
    "us_macro": UsMacroSource,
    "speeches": SpeechesSource,
    "research": ResearchSource,
}


def criar(nome: str) -> Source:
    if nome not in REGISTRO:
        disponiveis = ", ".join(REGISTRO)
        raise KeyError(f"fonte desconhecida: {nome}. Disponíveis: {disponiveis}")
    return REGISTRO[nome]()


def todas() -> list[Source]:
    return [classe() for classe in REGISTRO.values()]


__all__ = ["REGISTRO", "IngestResult", "Source", "criar", "todas"]
