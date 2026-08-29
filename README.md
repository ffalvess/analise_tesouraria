# analise_tesouraria

Aplicativo de análise da curva de juros do Brasil e dos Estados Unidos, do
fluxo cambial e do contexto macroeconômico que move as duas curvas.

A pergunta que organiza tudo: **como a diferença entre as duas curvas,
combinada ao fluxo de entrada e saída de moeda estrangeira, explica o
comportamento do dólar.** Em torno dela ficam o Focus, a balança comercial, a
inflação, o emprego, a atividade e a comunicação do Banco Central e do Fed.

---

## Instalação

```bash
git clone https://github.com/ffalvess/analise_tesouraria.git
cd analise_tesouraria

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env    # opcional: só a chave do FRED precisa ser preenchida
```

Requer Python 3.11 ou superior.

## Uso

```bash
# 1. Carga histórica inicial (leva alguns minutos)
tesouraria ingest --all --since 2015-01-01

# 2. Conferir o que entrou e o que falhou
tesouraria status

# 3. Abrir a interface
tesouraria serve            # ou: streamlit run src/tesouraria/ui/app.py
```

Depois disso, a atualização do dia a dia é incremental:

```bash
tesouraria ingest --source tesouro_direto --source us_treasury --since 2026-08-01
tesouraria ingest --all --since 2026-08-01
```

A ingestão é **idempotente**: rodar o mesmo período de novo substitui as linhas
em vez de duplicá-las, então repetir um comando nunca estraga o histórico.

### Modo offline

O repositório traz amostras **sintéticas** em `data/fixtures/`, que permitem
abrir o aplicativo inteiro sem conexão:

```bash
TESOURARIA_OFFLINE=1 tesouraria ingest --all
TESOURARIA_OFFLINE=1 tesouraria serve
```

Nesse modo o aplicativo exibe um aviso em todas as telas. **Os números não são
dados reais de mercado** e não servem para decisão — existem para desenvolver,
testar e demonstrar. Regere-os com `python scripts/gerar_fixtures.py`.

---

## As páginas

| Página | O que responde |
|---|---|
| **Painel** | Como estão as duas curvas hoje, quanto vale o carrego e o que o fluxo fez na semana |
| **Curva Brasil** | Curva do vencimento mais curto ao mais longo, em pré, IPCA+ ou implícita, por fonte; taxas a termo |
| **Curva Estados Unidos** | Curva nominal e real (TIPS), e a inflação implícita entre elas |
| **Comparação de datas** | A mesma curva em N datas sobrepostas, com a variação em bps por vértice e a leitura do movimento |
| **Diferencial BR × EUA** | O prêmio por vértice, o diferencial real, a série histórica e a correlação com o dólar |
| **Fluxo cambial** | Fluxo semanal contra a cotação, regressão, beta móvel e acumulados |
| **Balança comercial** | Exportação, importação, saldo, e o descasamento entre saldo registrado e dólares internalizados |
| **Focus** | A trajetória das revisões, a dispersão entre analistas, Focus contra Top 5 e contra a curva |
| **Inflação, emprego e atividade** | Os dois países lado a lado, no mesmo recorte |
| **Comunicação e research** | Discursos do BCB e do Fed com score de tom, feeds públicos e os seus PDFs locais |

### Duas decisões metodológicas que valem explicar

**Convenção de taxa.** A taxa brasileira é efetiva anual em base 252 dias
úteis; o *par yield* americano é *bond-equivalent*, com capitalização
semestral. Subtrair uma da outra sem converter erra o diferencial em mais de 5
pontos-base já num juro de 4,5% — e o erro cresce com o nível da taxa. Por isso
`analytics/curve.py::to_effective_annual` roda antes de qualquer comparação, e
é o comportamento mais testado do projeto.

**Não extrapolamos.** Se a curva brasileira acaba em 8 anos, o vértice de 10
anos fica vazio, não estimado. Um diferencial ausente é informação diferente de
um diferencial nulo.

---

## Fontes de dados

| Fonte | Origem | Chave |
|---|---|---|
| `tesouro_direto` | Tesouro Transparente (CSV) — **curva BR primária** | não |
| `anbima_ettj` | ANBIMA — estrutura a termo oficial | não |
| `b3_di` | B3 — ajustes do futuro de DI (DI1) | não |
| `us_treasury` | US Treasury — par yield nominal e real | não |
| `bcb_sgs` | Banco Central — Selic, CDI, PTAX, IPCA, IBC-Br, balança | não |
| `fx_flow` | Banco Central — movimento de câmbio contratado | não |
| `focus` | Banco Central (Olinda) — Focus e Top 5 | não |
| `comex` | Comex Stat / MDIC — balança detalhada | não |
| `ibge_sidra` | IBGE — IPCA, PNAD, PIB, PMC | não |
| `us_macro` | FRED — CPI, desemprego, payroll, Fed Funds, DXY | **sim** |
| `speeches` | Fed (RSS) e BCB (feed JSON) | não |
| `research` | Feeds públicos + `data/research_pdfs/` | não |

Sem `TESOURARIA_FRED_API_KEY`, a fonte `us_macro` é registrada como `pulado` e
o resto segue normalmente. A chave é gratuita:
<https://fred.stlouisfed.org/docs/api/api_key.html>

### Relatórios de casas de análise

Pesquisa sell-side (Itaú, BTG, XP, Goldman, Santander e afins) é conteúdo
licenciado e **não é coletada** por este aplicativo. Os feeds em
`config/feeds.yaml` cobrem apenas fontes abertas — FMI, BIS, Banco Mundial,
agências de rating, IPEA, FGV.

Para incluir os relatórios que você já recebe por direito, coloque os PDFs em
`data/research_pdfs/` e rode `tesouraria ingest --source research`. O texto é
extraído localmente, deduplicado por hash e recebe o mesmo score de tom.
Subpastas viram o nome da instituição:

```
data/research_pdfs/
├── BTG/relatorio-macro-agosto.pdf     → instituição "BTG"
└── nota-avulsa.pdf                    → instituição "PDF local"
```

A pasta está no `.gitignore` — o conteúdo é seu e não vai para o repositório.

---

## Publicação no Streamlit Community Cloud

O disco desses serviços é efêmero: a cada redeploy o container é recriado e o
DuckDB some. Por isso os dados viajam como **Parquet versionados** em
`data/snapshots/`, atualizados pelo GitHub Actions. Ao subir, o aplicativo
detecta o banco vazio e se hidrata a partir deles em segundos.

Os arquivos são **particionados por mês**: só o do mês corrente muda a cada
coleta, e os anteriores ficam byte a byte idênticos. É o que impede o histórico
do repositório de inchar — um arquivo único por tabela, reescrito diariamente,
custaria mais de um gigabyte por ano.

### Passo 1 — carga inicial

Guarde a chave do FRED em **Settings → Secrets and variables → Actions**, com o
nome `FRED_API_KEY`. Depois vá em **Actions → Coleta de dados → Run workflow** e
informe `since = 2015-01-01`.

É a execução longa (a diária leva poucos minutos). Ao terminar, confira no log
o resultado de `tesouraria status` e verifique que houve um commit em
`data/snapshots/`. A partir daí o workflow roda sozinho todo dia útil às 20h
(23h UTC), depois do fechamento no Brasil e nos Estados Unidos.

Duas fontes se comportam diferente no backfill: **ANBIMA e B3 publicam um
arquivo por pregão**, então coletá-las desde 2015 seriam milhares de
requisições. Elas têm um teto de `max_dias_por_execucao` em
`config/sources.yaml` e vão acumulando histórico dia a dia. O histórico longo da
curva brasileira vem do Tesouro Direto, que entrega tudo num CSV só.

### Passo 2 — criar o app

Em <https://share.streamlit.io> → **New app**:

| Campo | Valor |
|---|---|
| Repositório | `ffalvess/analise_tesouraria` |
| Branch | `main` |
| Main file path | `src/tesouraria/ui/app.py` |
| Python version (*Advanced settings*) | **3.11** |

Em **Advanced settings → Secrets**, cole:

```toml
TESOURARIA_FRED_API_KEY = "sua-chave-do-fred"
```

O aplicativo copia os secrets para variáveis de ambiente no boot, então não
importa se a plataforma os expõe dessa forma ou não.

### Duas ressalvas

- **O repositório é privado.** O Community Cloud precisa de autorização do
  GitHub com acesso a repositórios privados, e vale conferir o limite de apps
  privados do seu plano antes de contar com ele. Se preferir tornar o
  repositório público, não há segredo no código — tudo vem de `.env` e dos
  secrets — mas os dados de mercado passariam a ser públicos também.
- **Memória.** O plano gratuito oferece cerca de 1 GB, folgado para o volume
  atual. `documentos` é a tabela que mais cresce com o tempo, porque guarda o
  texto integral dos discursos (necessário para a busca da página de
  comunicação). Se um dia apertar, é o primeiro lugar a olhar.

### Snapshots pela linha de comando

```bash
tesouraria snapshot export     # banco -> data/snapshots/
tesouraria snapshot import     # data/snapshots/ -> banco
```

**Nunca commite snapshots gerados em modo offline.** Se acontecer, o aplicativo
avisa: `ingest_log.modo` viaja junto no snapshot, e um banner de *dados
sintéticos* aparece em todas as telas, nomeando as fontes afetadas.

---

## ⚠️ Checklist de validação dos endpoints

**Leia isto antes da primeira execução com rede aberta.**

O projeto foi desenvolvido num ambiente cujo proxy bloqueia por política todos
os hosts de dados financeiros. Os parsers estão testados contra as amostras de
`data/fixtures/`, mas **as URLs e os códigos de série nunca foram exercitados
contra os servidores reais**. É provável que uma ou outra fonte precise de
ajuste na primeira coleta.

Isso foi previsto no desenho: **URLs e códigos vivem em `config/sources.yaml`,
não no código.** Corrigir um endpoint é editar YAML.

Rode a ingestão e depois:

```bash
tesouraria status
```

A saída lista cada fonte com o status da última execução, a contagem de linhas
e o erro registrado. Confira nesta ordem:

1. **`tesouro_direto` e `us_treasury`** — são as curvas primárias; se estas duas
   entrarem, o núcleo do aplicativo funciona.
2. **`fx_flow`** — os códigos de série do movimento de câmbio (22707–22715)
   estão marcados com `verificar: true`. Confirme-os no buscador do SGS
   (<https://www3.bcb.gov.br/sgspub/>) e ajuste no YAML se necessário.
3. **`anbima_ettj`, `b3_di`, `comex`** — também marcados para verificação; o
   formato dos arquivos diários já mudou algumas vezes.
4. **Feeds do BCB** em `config/feeds.yaml` — os caminhos `/api/feed/sitebcb/...`
   estão marcados com `verificar: true`.

Uma fonte que falha não derruba as outras nem o aplicativo: o erro fica em
`ingest_log` e a página correspondente mostra o que fazer.

---

## Desenvolvimento

```bash
ruff check src tests scripts     # lint
pytest -q                        # 144 testes, todos offline
pytest --cov=tesouraria          # com cobertura
```

Nenhum teste toca a rede nem o banco do usuário. Eles cobrem os parsers de cada
fonte contra as fixtures, a conversão de convenção de taxa com valores
calculados à mão, a interpolação, os diferenciais, o tom, a idempotência da
gravação, a ida e volta dos snapshots (com verificação de determinismo por
hash) e — via `streamlit.testing` — a renderização real das dez telas, incluindo
a hidratação automática a partir dos Parquet.

O GitHub Actions roda lint e testes a cada push, no mesmo `requirements.txt`
que o Community Cloud usa: se a resolução de dependências quebrar, quebra na CI
antes de chegar ao app publicado.

### Estrutura

```
.github/workflows/  ci.yml (lint e testes) e dados.yml (coleta agendada)
config/           sources.yaml, feeds.yaml e os léxicos de tom
requirements.txt  o que o Streamlit Community Cloud instala
scripts/          gerador das amostras sintéticas
src/tesouraria/
  settings.py     configuração e caminhos
  http.py         cliente HTTP com retry e cache em disco
  db.py           DuckDB: esquema e upsert idempotente
  snapshots.py    Parquet particionado por mês, para publicar
  queries.py      camada única de leitura
  cli.py          tesouraria ingest | status | snapshot | serve
  sources/        uma fonte por arquivo (fetch e parse separados)
  analytics/      curve, differentials, fxflow, tone
  ui/             app.py, charts.py e as nove páginas
data/
  fixtures/       amostras sintéticas (versionadas)
  snapshots/      dados reais em Parquet, atualizados pelo Actions
  research_pdfs/  seus PDFs (ignorado pelo git)
  tesouraria.duckdb   banco local (ignorado pelo git)
```

### Acrescentar uma série

Inclua o código em `config/sources.yaml` e rode a ingestão. Nenhuma alteração
de código é necessária — a nova série aparece na aba *Todas as séries* da
página de macro.

```yaml
bcb_sgs:
  series:
    - { codigo: 4390, nome: "Selic acumulada no mês", unidade: "% a.m.", pais: BR }
```

### Acrescentar uma fonte

Crie um módulo em `src/tesouraria/sources/` com uma classe que herde de
`Source`, implemente `collect()` (e um `parse()` puro, para o teste), e
registre-a em `sources/__init__.py`. Acrescente uma fixture em
`data/fixtures/` e um teste em `tests/test_parsers.py`.

---

## Licença

MIT. Os dados coletados pertencem às respectivas fontes e estão sujeitos aos
termos de cada uma.
