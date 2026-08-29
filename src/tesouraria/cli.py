"""Linha de comando: `tesouraria ingest`, `status` e `serve`."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from tesouraria import db, snapshots, sources
from tesouraria.settings import get_settings

logger = logging.getLogger(__name__)


def _data(texto: str) -> dt.date:
    try:
        return dt.date.fromisoformat(texto)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"data inválida: {texto} (use AAAA-MM-DD)") from exc


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesouraria",
        description="Análise da curva de juros Brasil x EUA, fluxo cambial e macro.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    sub = parser.add_subparsers(dest="comando", required=True)

    ingest = sub.add_parser("ingest", help="coleta dados e grava no banco")
    ingest.add_argument(
        "--source",
        "-s",
        action="append",
        dest="fontes",
        metavar="FONTE",
        help=f"fonte a coletar (repetível). Disponíveis: {', '.join(sources.REGISTRO)}",
    )
    ingest.add_argument("--all", "-a", action="store_true", help="coleta todas as fontes")
    ingest.add_argument(
        "--since",
        type=_data,
        metavar="AAAA-MM-DD",
        help="data inicial da coleta; sem ela, cada fonte usa o seu padrão",
    )
    ingest.add_argument(
        "--offline",
        action="store_true",
        help="lê as amostras de data/fixtures em vez da rede",
    )

    sub.add_parser("status", help="mostra o frescor dos dados por fonte")

    snapshot = sub.add_parser(
        "snapshot",
        help="exporta o banco para Parquet versionado, ou o reconstrói a partir dele",
    )
    snapshot.add_argument(
        "acao",
        choices=["export", "import"],
        help="export: banco -> data/snapshots/. import: data/snapshots/ -> banco.",
    )
    snapshot.add_argument(
        "--dir",
        dest="diretorio",
        type=Path,
        help="diretório dos snapshots (padrão: data/snapshots)",
    )

    serve = sub.add_parser("serve", help="abre a interface Streamlit")
    serve.add_argument("--port", type=int, default=8501)
    serve.add_argument("--offline", action="store_true", help="roda a interface em modo offline")

    return parser


def comando_ingest(args: argparse.Namespace) -> int:
    if args.offline:
        os.environ["TESOURARIA_OFFLINE"] = "1"
        get_settings.cache_clear()

    nomes = list(sources.REGISTRO) if args.all or not args.fontes else args.fontes
    desconhecidas = [n for n in nomes if n not in sources.REGISTRO]
    if desconhecidas:
        print(f"fonte desconhecida: {', '.join(desconhecidas)}", file=sys.stderr)
        print(f"disponíveis: {', '.join(sources.REGISTRO)}", file=sys.stderr)
        return 2

    resultados = []
    with db.connection() as con:
        for nome in nomes:
            fonte = sources.criar(nome)
            print(f"→ {nome} ...", end=" ", flush=True)
            resultado = fonte.run(con, since=args.since)
            resultados.append(resultado)
            detalhe = f"{resultado.linhas} linhas" if resultado.status == "ok" else (
                resultado.erro or ""
            )
            print(f"{resultado.status} {detalhe}".strip())

    falhas = [r for r in resultados if r.status == "erro"]
    print(
        f"\n{len(resultados)} fontes processadas · "
        f"{sum(r.linhas for r in resultados)} linhas gravadas · {len(falhas)} com erro"
    )
    if falhas:
        print("Rode `tesouraria status` para ver os detalhes das falhas.")
    # Falha parcial não é falha do comando: o objetivo é maximizar o que entra.
    return 0


def comando_status(_: argparse.Namespace) -> int:
    with pd.option_context("display.width", 160, "display.max_colwidth", 60):
        log = db.status_report()
        print("Última execução por fonte")
        print("-" * 78)
        print(log.to_string(index=False) if not log.empty else "(nenhuma ingestão registrada)")

        print("\nCobertura das tabelas")
        print("-" * 78)
        print(db.table_coverage().to_string(index=False))
    return 0


def comando_snapshot(args: argparse.Namespace) -> int:
    destino = args.diretorio or snapshots.diretorio_padrao()

    with db.connection() as con:
        if args.acao == "export":
            resumo = snapshots.exportar(con, destino)
            verbo = "exportadas"
        else:
            resumo = snapshots.importar(con, destino)
            verbo = "importadas"

    if not resumo:
        print(f"Nada a fazer: nenhum snapshot em {destino}")
        return 0

    for tabela, linhas in resumo.items():
        print(f"  {tabela:14s} {linhas:>9,} linhas".replace(",", "."))

    total = sum(resumo.values())
    print(f"\n{total:,} linhas {verbo} · {destino}".replace(",", "."))
    if args.acao == "export":
        print(f"Tamanho em disco: {snapshots.tamanho(destino) / 1e6:.1f} MB")
    return 0


def comando_serve(args: argparse.Namespace) -> int:
    if args.offline:
        os.environ["TESOURARIA_OFFLINE"] = "1"

    app = Path(__file__).resolve().parent / "ui" / "app.py"
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    )


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Bibliotecas de rede são ruidosas em INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    comandos = {
        "ingest": comando_ingest,
        "status": comando_status,
        "snapshot": comando_snapshot,
        "serve": comando_serve,
    }
    return comandos[args.comando](args)


if __name__ == "__main__":
    raise SystemExit(main())
