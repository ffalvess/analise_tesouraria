"""Linha de comando: `tesouraria ingest`, `status` e `serve`."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import subprocess
import sys
import time
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
    sonda.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="segundos por código antes de desistir (padrão: 8; o SGS pendura em código inexistente)",
    )

    busca = sub.add_parser(
        "sgs-buscar",
        help="procura séries do SGS pelo nome, no catálogo de dados abertos do BCB",
    )
    busca.add_argument("termo", help='texto a procurar, ex.: "fluxo cambial"')
    busca.add_argument("--limite", type=int, default=50, help="resultados (padrão: 50)")

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
                limpas = db.limpar_tabela(
                    con, "fx_flow", fontes=sources.fontes_da_tabela("fx_flow")
                )
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

    Complementa `sgs-buscar`, que descobre o código pelo nome: aqui se confirma
    o que o código **devolve**, antes de ele entrar em config/sources.yaml. A
    consulta ao SGS não valida nada — um código errado não dá erro,
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
    total = args.ate - args.de + 1
    inicio = time.monotonic()

    for posicao, codigo in enumerate(range(args.de, args.ate + 1), start=1):
        # Progresso a cada 25 códigos: sem isto, uma varredura interrompida não
        # deixa registro de onde parou, e o log fica indistinguível de travado.
        if posicao % 25 == 0:
            decorrido = time.monotonic() - inicio
            restante = decorrido / posicao * (total - posicao)
            print(
                f"... {posicao}/{total} códigos ({encontrados} com dados) · "
                f"{decorrido / 60:.1f} min decorridos, ~{restante / 60:.1f} min restantes",
                flush=True,
            )
        try:
            bruto = fetch(
                modelo.format(codigo=codigo) + f"/ultimos/{args.amostra}",
                params={"formato": "json"},
                use_cache=False,
                # Numa varredura, silêncio é resposta: o código não existe.
                # Insistir com a política de coleta custava 2min18s por código.
                timeout=args.timeout,
                tentativas=1,
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


def extrair_codigo(nome: str) -> str | None:
    """O código do SGS é o número que abre o identificador do conjunto.

    O portal nomeia os conjuntos de duas formas — `22704-sgs` e
    `1-taxa-de-cambio---livre---dolar-americano-venda---diario` —, e as duas
    começam pelo código.
    """
    achado = re.match(r"(\d+)-", nome or "")
    return achado.group(1) if achado else None


def comando_sgs_buscar(args: argparse.Namespace) -> int:
    """Procura séries do SGS pelo nome, em vez de adivinhar o número.

    Este projeto carregava a nota de que "o SGS não tem API de metadados", e
    foi ela que justificou chutar os códigos do fluxo cambial — que se
    revelaram outra coisa, meses de dados depois. A nota é falsa: o portal de
    dados abertos do BCB indexa cada série num catálogo CKAN, com o código no
    identificador do conjunto.

    A varredura numérica (`sgs-probe`) continua útil para confirmar o que um
    código devolve. Esta busca é o passo anterior, e o certo: descobrir qual é
    o código.
    """
    from tesouraria.http import fetch_json

    url = source_config("bcb_sgs")["catalogo_url"]
    resposta = fetch_json(
        url, params={"q": args.termo, "rows": args.limite}, use_cache=False, timeout=30.0
    )
    conjuntos = (resposta or {}).get("result", {}).get("results", [])

    if not conjuntos:
        print(f"Nada encontrado para {args.termo!r}.")
        return 0

    print(f"{'código':>8}  título")
    print("-" * 100)
    com_codigo = 0
    for conjunto in conjuntos:
        nome = conjunto.get("name", "")
        codigo = extrair_codigo(nome)
        titulo = (conjunto.get("title") or conjunto.get("notes") or "").strip()

        if codigo is None:
            # Conjunto sem código no identificador ainda é resultado: pode ser o
            # agregado que aponta para as séries certas. Descartar em silêncio
            # já produziu um "1 encontrada, 0 exibidas" que não explicava nada.
            print(f"{'—':>8}  {titulo[:70]}  [{nome[:40]}]")
            continue

        print(f"{codigo:>8}  {titulo[:90]}")
        com_codigo += 1

    total = (resposta or {}).get("result", {}).get("count", len(conjuntos))
    print(
        f"\n{len(conjuntos)} conjuntos exibidos de {total} encontrados para "
        f"{args.termo!r} ({com_codigo} com código de série)."
    )
    print("Confirme o conteúdo com: tesouraria sgs-probe --de CÓDIGO --ate CÓDIGO")
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
        "sgs-buscar": comando_sgs_buscar,
        "serve": comando_serve,
    }
    return comandos[args.comando](args)


if __name__ == "__main__":
    raise SystemExit(main())
