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
from tesouraria.settings import get_settings, source_config

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

    sonda = sub.add_parser(
        "sgs-probe",
        help="descobre o que há numa faixa de códigos do SGS, para identificar séries",
    )
    sonda.add_argument("--de", type=int, required=True, help="primeiro código da faixa")
    sonda.add_argument("--ate", type=int, required=True, help="último código da faixa")
    sonda.add_argument(
        "--amostra", type=int, default=24, help="observações a buscar por série (padrão: 24)"
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

        # Poda apenas na coleta completa: rodando uma fonte só, o resto do banco
        # não está em jogo e apagá-lo seria destrutivo sem motivo.
        if len(nomes) == len(sources.REGISTRO):
            removidas = db.podar(con, sources.series_declaradas())
            for tabela, quantas in removidas.items():
                print(f"  podadas {quantas} linhas de {tabela} (séries não declaradas)")

            if not source_config("fx_flow").get("series"):
                limpas = db.limpar_tabela(con, "fx_flow")
                if limpas:
                    print(f"  limpas {limpas} linhas de fx_flow (fonte desativada)")

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


def comando_sgs_probe(args: argparse.Namespace) -> int:
    """Varre uma faixa de códigos do SGS e descreve o que cada um devolve.

    Existe porque o SGS não tem API de metadados: um código errado não dá erro,
    devolve outra série. Foi assim que os códigos de fluxo cambial entraram
    errados e passaram meses de dados sem ninguém notar. Em vez de adivinhar de
    novo, esta sonda mostra periodicidade, ordem de grandeza e troca de sinal —
    o suficiente para reconhecer a série procurada.

    Uso típico: `tesouraria sgs-probe --de 22600 --ate 22800`, e procure no
    resultado as séries semanais, em milhares de US$ milhões, com sinal que
    alterna — o formato do fluxo cambial contratado.
    """
    import json

    from tesouraria.http import fetch
    from tesouraria.sources.bcb_sgs import parse_sgs

    modelo = source_config("bcb_sgs")["url_template"]
    print(f"{'código':>8} {'n':>4} {'período':>22} {'perio.':>8} "
          f"{'mín':>14} {'média':>14} {'máx':>14}  sinal")
    print("-" * 105)

    encontrados = 0
    for codigo in range(args.de, args.ate + 1):
        try:
            bruto = fetch(
                modelo.format(codigo=codigo) + f"/ultimos/{args.amostra}",
                params={"formato": "json"},
                use_cache=False,
            )
            dados = parse_sgs(json.loads(bruto.decode("utf-8")), str(codigo))
        except Exception as exc:  # noqa: BLE001 — faixa varrida; a maioria não existe
            logger.debug("SGS %s indisponível: %s", codigo, exc)
            continue

        if dados.empty or dados["valor"].isna().all():
            continue

        datas = pd.to_datetime(dados["data_ref"])
        intervalo = datas.diff().dt.days.median()
        periodicidade = (
            "diária" if intervalo <= 3 else
            "semanal" if intervalo <= 10 else
            "mensal" if intervalo <= 45 else
            "trimestral" if intervalo <= 120 else "anual"
        )
        valores = dados["valor"].dropna()
        sinal = "alterna" if (valores > 0).any() and (valores < 0).any() else (
            "positivo" if (valores >= 0).all() else "negativo"
        )

        print(
            f"{codigo:>8} {len(dados):>4} "
            f"{datas.min():%Y-%m-%d}..{datas.max():%Y-%m-%d} {periodicidade:>8} "
            f"{valores.min():>14,.2f} {valores.mean():>14,.2f} {valores.max():>14,.2f}  {sinal}"
        )
        encontrados += 1

    print(f"\n{encontrados} séries com dados na faixa {args.de}–{args.ate}.")
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
        "sgs-probe": comando_sgs_probe,
        "serve": comando_serve,
    }
    return comandos[args.comando](args)


if __name__ == "__main__":
    raise SystemExit(main())
