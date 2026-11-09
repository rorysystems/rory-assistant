# Desenvolvimento, testes e pegadinhas

## Rodar e testar

```powershell
cd V:\projetos\rory-assistant
.\.venv\Scripts\pythonw.exe -m app.main          # janela (RORY_DEBUG=1 abre o DevTools)
.\.venv\Scripts\python.exe scripts\check_js.py    # sintaxe do ui/*.js (obrigatório depois de mexer no JS)
.\.venv\Scripts\python.exe scripts\smoke_agent.py # backend sem janela (custa uma chamada)
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1   # instalador -> dist\installer
```

- **`RORY_DATA_DIR=<pasta>`** usa outra pasta de dados (instância única é por pasta de dados): teste sem mexer nas conversas reais.
- Testar a interface por script: `SendKeys` só chega ao WebView2 depois de um **clique de mouse** na página. Ache a janela de teste pelo PID, não pelo título.
- Um erro de sintaxe no JS faz o app abrir "travado" (tela desenhada, nenhum botão funciona).

## Instaladores

- Windows local: `build-windows.ps1` (PyInstaller + Inno Setup). Linux: só pelo GitHub Actions (o job do macOS foi removido: sem runner disponível e o macOS usa só o `install.sh`) (`.github/workflows/build.yml`), manual ou com tag `v*`, que publica na Release.
- O `.spec` **tira o `claude.exe` embutido** do SDK (~345 MB); nos instaladores o app usa o Claude Code do PATH (`config.find_claude()`); na instalação por comando o embutido fica.
- `installer.iss` precisa estar em **UTF-8 com BOM**. O AppId fixo `{6E4F2B1C-...}` também é usado pelo botão flutuante para achar `InstallLocation`.

## Pegadinhas

- **Smart App Control ligado neste PC**: bloqueia o .exe sem assinatura instalado no `C:`. No `V:` (Dev Drive) roda. Saídas: instalar no V: ou assinar o código. Com SAC desligado, só o aviso do SmartScreen.
- **SAC também bloqueia `.pyd` novos do venv** (mesmo no V:): em 09/11/2026 bloqueou `_pydantic_core` 2.50.4 e o app não abria (`ImportError: DLL load failed ... política de Controle de Aplicativo`). Por isso `pydantic`/`pydantic-core` estão fixados no `requirements.txt`. Antes de subir versão, testar `python -c "import app.main"`. Bloqueios aparecem em `Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational'`.
- **WinForms/WebView2 só na thread da janela**: todo acesso vai dentro de `form.BeginInvoke(Action(...))`. Ler `CoreWebView2` numa thread de fundo causou deadlock no GIL (app travava ao abrir).
- **Diagnóstico de travamento**: `.venv\Scripts\py-spy.exe dump --pid <pid do pythonw filho> --native`. Quedas gravam a pilha em `%APPDATA%\Rory Assistant\crash.log`.
- **Buffer do SDK**: 1 MB por mensagem JSON por padrão; `Read` de imagem grande estoura (`CLIJSONDecodeError`). `_options()` usa `max_buffer_size=64 MB`.
- **Loop asyncio Proactor** no Windows (subprocesso); o `AgentHub` cria um explicitamente.
- **Terminal vazio / "Cannot write to terminated process"**: o `claude.exe` ganhava janela de console; fechá-la matava o Claude. `agent._hide_child_consoles()` envolve `anyio.open_process` com `CREATE_NO_WINDOW`.
- **Porta HTTP do pywebview**: a padrão (42001) é fixa e outra instância serviria a interface velha. Usa `http_port=_free_port()`; por isso o estado não pode depender do localStorage.
- **Título da janela** (`Rory Assistant`) é como o botão flutuante e o GlazeWM acham a janela.
- O custo mostrado (`total_cost_usd`) é estimativa; com assinatura não é cobrado por mensagem.
- PS 5.1: edite `.md`/`.py` com a ferramenta Edit ou `[IO.File]` em UTF-8, nunca com `Set-Content`.

## Estado

- Testado no Windows (dev): chat, anexos, permissões, conversas por pasta, memória após reabrir, mídia, `!comando`. Instalador 0.1.0 testado pelo usuário antes das últimas mudanças.
- **Não testado**: builds de macOS e Linux.

## Próximas etapas

1. **Google / Microsoft** (agenda, e-mail) via servidores MCP (`mcp_servers` em `ClaudeAgentOptions`); falta uma tela de "Conexões".

## Revisão técnica (lotes 0–5)
Implementada, mas **sem teste visual**. Dependências fixadas em `requirements.txt` (`requirements.lock` é só `pip freeze`, sem hashes). CI passou para Python 3.14 (conferir wheels no macOS/Linux) e as actions estão pinadas por SHA. Testar na tela: card do plano, `/model`, menu de caminho, histórico paginado, `!ping -t` + parar.

## Instalação por comando (`install.ps1` / `install.sh`)

Alternativa aos instaladores (que não são assinados): o app roda do código-fonte, instalado por um comando (ver README).

- **Como funciona**: instala o **uv** se faltar (sem admin), baixa o tar/zip da última Release (`api.github.com/.../releases/latest`; se não houver, branch `main`), copia `app ui assets scripts requirements*.txt` para `<destino>/src`, cria `<destino>/venv` com o Python pedido (3.14; Mac Intel 3.13) e roda `uv pip install -r requirements.txt` (+ `requirements-linux.txt` no Linux). Depois confere `import app.main`, cria o atalho e checa o login do Claude Code.
- **Destinos**: Windows `%LOCALAPPDATA%\Programs\Rory Assistant`; macOS `~/Library/Application Support/Rory Assistant App` + `~/Applications/Rory Assistant.app` (bash launcher, ícone `.icns` gerado com `sips`/`iconutil`); Linux `~/.local/share/rory-assistant` + `.desktop` + `~/.local/bin/rory-assistant` (launcher com `PYWEBVIEW_GUI=qt`). Os **dados ficam fora** (`config.DATA_DIR`), então reinstalar/atualizar não os toca; a desinstalação pergunta antes.
- **Idempotente**: rodar de novo troca só `src/` e sincroniza o venv (~4 s sem novidades). Se o app estiver aberto (processo dentro do destino), recusa com mensagem clara (no Windows o `.pyd` em uso não pode ser trocado).
- **Variáveis de teste**: `RORY_DEST`, `RORY_LOCAL_SRC` (usa uma pasta local em vez de baixar), `RORY_NO_SHORTCUTS=1`, `RORY_REF`, `RORY_PYTHON`, `RORY_UNINSTALL=1`/`RORY_PURGE` (ps1) ou `--uninstall --purge/--keep-data` (sh). Use sempre com `RORY_DATA_DIR=<temp>`.
- **Testar no Windows**: `$env:RORY_DEST="$env:TEMP\rt"; $env:RORY_LOCAL_SRC='V:\projetos\rory-assistant'; $env:RORY_NO_SHORTCUTS=1; Get-Content -Raw -Encoding UTF8 install.ps1 | iex`. O `install.sh` dá para checar no Git Bash com um `uname` e um `uv` falsos no PATH (`dash install.sh`), mas só macOS/Linux de verdade validam o restante.
t"; $env:RORY_LOCAL_SRC='V:\projetos
ory-assistant'; $env:RORY_NO_SHORTCUTS=1; Get-Content -Raw -Encoding UTF8 install.ps1 | iex`. O `install.sh` dá para checar no Git Bash com um `uname` e um `uv` falsos no PATH (`dash install.sh`), mas só macOS/Linux de verdade validam o restante.
- **Pegadinhas**:
  - `install.ps1` tem de ser **UTF-8 sem BOM** (com BOM, o `irm | iex` do PS 5.1 quebra na primeira linha); localmente leia com `-Encoding UTF8`. Sem `exit`: o `iex` roda na sessão do usuário. Sem `&&`/`??`.
  - Parâmetros não existem em `irm | iex`; por isso as opções são variáveis de ambiente (ps1) e `sh -s -- --opção` (sh).
  - **Wheels (conferido com `uv pip compile --python-platform ... --only-binary :all:`)**: Linux x86_64/aarch64 e macOS Apple Silicon (14+) têm wheel de tudo no 3.14; `onnxruntime` (via `markitdown`→`magika`) **não tem wheel de Mac Intel no 3.14**, por isso o Mac Intel usa 3.13. `proxy-tools` é só sdist (puro Python; o uv compila sozinho). Ao subir versões, refaça essa conferência.
  - O **Claude Code vem no wheel do SDK** (`claude_agent_sdk/_bundled`); `config.find_claude()` usa o do PATH, depois pastas comuns e por fim o embutido. Assim o pré-requisito é só o login.
  - Linux: o Qt vem do pip (`requirements-linux.txt`), mas precisa de libs do sistema (`libxcb-cursor0`, `libegl1`, `libnss3`...). O `install.sh` roda `ldd` nos plugins do Qt e imprime o `sudo apt install` exato (tabela soname→pacote dentro do script; `libasound2`/`libglib2.0-0` viram `t64` quando existir).
  - macOS: o `.app` criado localmente não ganha quarentena, então o Gatekeeper não reclama. O processo é o `python` do venv (o Dock mostra o ícone dele). Permissão de microfone pode precisar ser dada ao Terminal.
- **Não testado**: execução real em macOS e Linux (só a lógica do script, em shell falso), tela do Qt/WebKit, microfone fora do Windows.
