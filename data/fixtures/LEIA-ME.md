# Amostras para desenvolvimento

**Estes arquivos contêm dados sintéticos, não dados reais de mercado.**

Foram gerados por `scripts/gerar_fixtures.py` para reproduzir a *forma* de cada
fonte — colunas, separadores, codificação, formato de data e ordens de grandeza
plausíveis — de modo que os parsers e a interface possam ser exercitados sem
conexão de rede.

Servem para dois fins:

1. **Testes** (`pytest`), que chamam `parse()` diretamente com estes arquivos.
2. **Modo offline** (`TESOURARIA_OFFLINE=1`), que permite abrir o aplicativo
   inteiro e navegar por todas as páginas sem internet.

Nenhum número aqui deve ser usado para análise ou decisão. Para dados reais,
rode `tesouraria ingest --all` com rede aberta.
