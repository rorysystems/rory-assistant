# Arquitetura (documentação técnica)

Rory Assistant = janela pywebview (HTML/JS) + backend Python que usa o **Claude Code** via **Claude Agent SDK**. O Claude Code do PATH é usado (`cli_path=shutil.which("claude")`), com a assinatura do usuário — sem chave de API.

## Mapa de arquivos

| Caminho | O que é |
|---|---|
| `app/config.py` | nome do app, versão, caminhos de dados (`%APPDATA%\Rory Assistant`). Trocar o nome = aqui + `packaging/installer.iss` + título no `claude-tray.ps1` |
| `app/main.py` | janela pywebview e a classe `Api` (métodos chamados pelo JS como `window.pywebview.api.*`, cada chamada numa thread) |
| `app/agent.py` | `AgentHub` (loop asyncio Proactor numa thread) e `Session`: um `ClaudeSDKClient` por conversa, com streaming, permissões, retomada e compactação |
| `app/store.py` | `state.json` (conversas, conversa ativa, tema, prefs), `history/<id>.json` (mensagens para redesenhar a tela), `attachments/` |
| `app/convert.py` | anexo → blocos da API: imagem (até 1568 px), PDF por página (texto ou imagem), Office/CSV via MarkItDown, texto. `to_markdown` para o "Converter para .md" e o visualizador |
| `app/media.py` | transcrição (faster-whisper) e OCR (Windows.Media.Ocr) — tudo no PC |
| `app/usage.py` | tokens da conversa, limites de 5 h / semana, tabela de custo |
| `app/shellcomplete.py` | sugestões do `!comando` (PowerShell `TabExpansion2` persistente + histórico + git) |
| `ui/` | `index.html`, `app.js`, `style.css`, `vendor/` (marked, DOMPurify, highlight.js) |
| `assets/` | ícones gerados por `scripts/make_icons.py` |
| `packaging/` | PyInstaller (`rory-assistant.spec`), Inno Setup (`installer.iss`), scripts de build por SO |
| `.github/workflows/build.yml` | gera os 3 instaladores (manual ou tag `v*`) e publica na Release |
| `scripts/smoke_agent.py` | testa o backend sem janela (custa uma chamada) |
| `scripts/import_session.py` | importa uma sessão do Claude Code do terminal como conversa do app (feche o app antes) |
| `scripts/check_js.py` | checa a sintaxe do `ui/*.js` (tree-sitter; não há Node neste PC) |

## Fluxo de uma mensagem

- **Envio**: o JS lê os anexos como data URL → `Api.send` salva o arquivo e converte em blocos (`convert.to_blocks`) → `Session.send` manda uma mensagem `user` com `content=[blocos]` por `client.query(async_iterable)`. A imagem vai **dentro da mensagem**.
- **Streaming**: `include_partial_messages=True`. `text_delta` → evento `text`; `ToolUseBlock` → `tool`; `ToolResultBlock` → `tool_end`. Python → JS por `window.evaluate_js("window.onAgentEvent(...)")`.
- **Leitura contínua**: cada sessão tem `_read_loop` sobre `client.receive_messages()`, que não para no fim do turno. `submit` escreve na hora com `client.query`, mesmo com o Claude trabalhando (mensagem no meio do turno, como no terminal).
- **Ocupado**: vem do `session_state_changed` (running / requires_action / idle), que só chega com `env CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`. Sem ele, o `ResultMessage` encerra o "ocupado".
- Ao mandar no meio, o que já foi respondido é gravado como mensagem do assistente antes da nova; na tela, `freezeLive()` fixa a resposta e abre outra bolha. Turno que começa sem a tela esperar → evento `turn_start`.
- **`/comandos` e skills**: mensagem só de texto começando com `/` vai como **string** (`content="/x ..."`), formato em que o Claude Code expande comandos. A lista vem de `client.get_server_info()["commands"]` e do `SystemMessage` init.

## Sessões e conversas

- Cada conversa fica presa a uma pasta (cwd do Claude) e tem seu `session_id`, salvo do `ResultMessage` e usado em `resume=`. Sessão sumida → conecta sem resume. Processo morto → `CLIConnectionError`, reconecta e reenvia uma vez.
- O índice de todas as conversas fica em `state.json`; a barra lateral agrupa por pasta. Grupos do usuário: `state.json → groups` (`id`, `name`, `collapsed`, `parent` opcional: aninhamento sem ciclos, checado em `Store.set_group_parent`) e `group` (id) em cada conversa; `Store.add_group/update_group/delete_group/set_conv_group` e `Api.create_group/move_group/rename_group/collapse_group/delete_group/set_conversation_group` (todas devolvem `groups` + `conversations`). No JS, `convGroups()` monta `sections` (árvore: grupo → subgrupos + pastas → conversas; `renderNode` recursivo). Arrastar e soltar: `makeDraggable` + `bindGroupDrop` (delegado no `#conv-list`) e `visibleConvIds()` é a ordem real do DOM (grupos recolhidos não têm linhas). Conversa nova numa pasta herda o modo de permissão da última conversa daquela pasta. Conversas vazias são descartadas ao iniciar (menos a ativa). Apagar move o histórico para `history/apagadas/`.
- `/clear` (`Api.clear_conversation`): `hub.forget`, zera `session_id`/`ctx_floor`, grava um `divider` (`kind: "clear"`).
- Várias conversas ao mesmo tempo: a tela só desenha eventos da conversa aberta. Ao voltar para uma conversa ocupada, `set_active` devolve `live_parts` (cópia do `session.parts`) e `permissions` pendentes.

## Permissões

- `can_use_tool` cria um Future, emite `permission` e espera `Api.answer_permission`. "Sempre" devolve `ctx.suggestions` como `updated_permissions`.
- Modos por conversa: default / acceptEdits / plan / bypassPermissions (`set_permission_mode` ao vivo).
- Pedidos pendentes ficam em `Session.pending_perms`. O cartão mostra `decision_reason`/`blocked_path`; em `bypassPermissions` o Claude Code ainda tem checagens de segurança que pedem confirmação.
- Fim do turno (status busy=false): cartões que sobraram são desativados.

## Status ao vivo

- `.live-line` na bolha em andamento (`updateLiveStatus`, a cada 1 s, sem tokens): "Pensando…", "Rodando X", "Escrevendo…", total do turno.
- Pedido sem resposta → "⏸ Parado esperando sua permissão", `#wait-bar` acima da caixa, ⏸ na barra lateral (`waiting[conv]`). Na conversa aberta, "esperando" = `.perm:not(.answered)` no DOM.
- `liveCarry` passa relógio/ferramenta para a bolha nova quando chega mensagem no meio.

## Contexto e compactação

- **Barra de contexto** (`usage.conversation_usage`): lê `~/.claude/projects/*/<sessão>.jsonl` e os subagentes. Contexto = entrada + cache da última resposta sobre a janela (1M; 200 mil nos modelos antigos).
- **Compactar sem perder a tela**: `/compact` resume a sessão do Claude; o `history/<id>.json` não muda. O `compact_boundary` grava um `divider` com `pre_tokens` e `trigger`.
- **Automática** (pref `auto_compact_at`, padrão 60 mil além do piso; 0 desliga):
  - **Piso** (`ctx_floor`, estimado em 50 mil até medir) = contexto da 1ª resposta após compactar (instruções, ferramentas, CLAUDE.md, skills). Não some ao compactar (~40–80 mil). Gatilho: `context - piso >= limite`.
  - **Em segundo plano** (`Session._maybe_compact`): no fim do turno (idle), manda `COMPACT_COMMAND` sem aparecer na conversa; tela mostra "compactando em segundo plano…" (evento `compacting`). Mensagem enviada nesse meio-tempo fica em `held`. Uma tentativa por mensagem (`compact_tried`).
  - **Garantia nativa**: `env CLAUDE_CODE_AUTO_COMPACT_WINDOW = limite + 150 mil` (o Claude Code dispara ~33 mil abaixo do valor). Mudar a pref chama `disconnect_all()`.
  - **Por quê**: com o cache quente, logo após a resposta, compactar custa ~10%; compactar ao abrir (cache frio) era o momento mais caro.
- **Conectores do claude.ai**: pref `claudeai_connectors` (padrão desligado → `ENABLE_CLAUDEAI_MCP_SERVERS=false`), menos carga fixa.

## Subagentes

- Pref `use_subagents` (padrão ligado): `build_guide(prefs)` acrescenta `SUBAGENTS_GUIDE` ao prompt de sistema. Mudar chama `disconnect_all()`.
- Na tela: a chamada `Agent` vira "🤖 Subagente: <tarefa>"; mensagens com `parent_tool_use_id` viram eventos `subtool` ("↳ N ações · última ação"), contagem em `part.sub`.
- Ferramentas repetidas seguidas com o mesmo alvo viram uma linha `×N` (`addTool`, chave = nome + resumo; `toolIndex` liga `tool_end` à linha).

## Mídia (no PC, sem tokens)

- **Transcrição**: faster-whisper, CPU int8, modelo em memória, uma por vez; baixado do Hugging Face no 1º uso. **PyAV fixado em `<17`**.
- Áudio → texto com duração e tempo de transcrição. Vídeo → transcrição + OCR de até 6 quadros (`video_frames`).
- Imagem: pref `image_mode` = `image` (padrão) / `ocr` / `both`. OCR via `Windows.Media.Ocr` (pywinrt), pt-BR se instalado.
- Binário desconhecido vai só como uma linha dizendo que não foi lido.
- **Microfone**: `getUserMedia` + `MediaRecorder` → `Api.transcribe_voice`. `_allow_microphone` libera o microfone no `CoreWebView2.PermissionRequested`.

## `!comando`

- `Api.run_shell`: roda na pasta da conversa, sem o Claude (PowerShell 5.1 com `-EncodedCommand` no Windows, `bash -lc` fora; limite 10 min). `_clean_powershell` converte erros CLIXML.
- Histórico grava `role: "shell"`. A saída (últimos 4 mil caracteres, até 5 comandos) fica em `conv.shell_ctx` e vai como `<bash-input>/<bash-stdout>` junto da próxima mensagem (não junto de `/comando`).
- **Sugestões** (`shellcomplete.py`, `Api.shell_complete`, `updateShellSuggest`): `powershell.exe -NoProfile` persistente (aberto no 1º `!` via `shell_warm`) respondendo ao `TabExpansion2` (~0,2 s; 1ª vez ~1,3 s) + histórico do PSReadLine + `!` da conversa + subcomandos/branches do git. Fora do Windows: `compgen`.

## Explorador de arquivos

- Painel `#files` à direita do chat (Ctrl+E): visualizador e, na ponta direita, a árvore (ordem via CSS `order`). Abaixo de 900 px cobre a tela. Prefs: `files_width`, `files_tree_width`, `files_open` (abre no `init()` se a janela tem ≥ 900 px).
- `Api.list_dir` (uma pasta por vez, sob demanda; esconde `.git`), `Api.read_file` (kind `md` / `text` / `image` / `doc` convertido via `convert.to_markdown` / `binary`), `Api.open_file` (programa padrão ou VS Code). `_inside` impede sair da pasta da conversa.
- Atualiza sozinho no fim de cada turno (`refreshFiles`). Painel estreito (<560 px): árvore e visualizador se alternam (`.compact`).

## Comandos de tela do terminal

- `/login`, `/logout`, `/usage`, `/cost`, `/status`, `/clear`, `/new`, `/resume`, `/permissions`, `/model`, `/terminal`, `/config`, `/mcp` são interceptados por `LOCAL_COMMANDS` (app.js) e abrem o recurso equivalente do app. O `/mcp` usa `Api.mcp_servers`/`mcp_action` → `Session.mcp_servers`/`mcp_action`: `get_mcp_status`, `reconnect_mcp_server` e `toggle_mcp_server` do SDK e, para o login OAuth, o pedido de controle `mcp_authenticate` enviado por `client._query._send_control_request` (API interna do SDK, não documentada: reconferir ao atualizar o `claude-agent-sdk`). O CLI sobe o servidor local que recebe o retorno do login; o app só abre a `authUrl` e consulta o estado a cada 3 s.
- Comando "só terminal" não mapeado abre o terminal com `claude --resume <sessão> "/comando"` (`Api.open_terminal`). Comandos novos do Claude Code funcionam sem mexer no app.

## Configurações e uso

- Painel `#settings`, uma `<section class="set-group">` por assunto (acrescente uma seção no `index.html` e a lógica no `app.js`). Prefs em `state.json → prefs` (`Api.PREF_DEFAULTS`).
- **Conta**: `claude auth status`; Entrar abre `claude auth login` num terminal visível; Sair roda `claude auth logout` (vale para o PC inteiro). Ambos chamam `hub.disconnect_all()`.
- **Uso**: limites de 5 h / semana pelo endpoint do `/usage` (`api.anthropic.com/api/oauth/usage`, token de `~/.claude/.credentials.json`, `anthropic-beta: oauth-2025-04-20`; não documentado). O app **nunca renova o token**. Tabela de tokens soma os `.jsonl` (uma vez por `message.id + requestId`) × `PRICES` — **modelo novo: inclua o preço em `PRICES`**.

## Interface

- Barra lateral fixa ≥ 760 px; abaixo vira gaveta (☰ / Ctrl+B).
- Instância única (mutex `Local\RoryAssistant-<hash da pasta de dados>`).
- Todo markdown passa pelo **DOMPurify** (a janela tem acesso à API Python). Links abrem no navegador (`api.open_url`).
- `setting_sources` padrão carrega `~/.claude` e CLAUDE.md/.mcp.json do projeto. O app **não escreve nada na pasta do projeto**.

## Revisão técnica (lotes 0–5)
- **Plano (`ExitPlanMode`)**: hook `PreToolUse` força a pergunta em qualquer modo; evento `plan` com o texto inteiro; respostas `accept_edits`/`default`/`deny`+mensagem; ao aprovar, grava o modo e emite `mode_changed`. Sem "sempre permitir".
- **Envio**: `AgentHub.send` não bloqueia; erros vão no evento `done`. `_connect` só zera `session_id` se a sessão não existir. `mark_all_stale()` faz as sessões reconectarem (retomando) na próxima mensagem; sessões ociosas há mais de 10 min são desconectadas.
- **Subagentes**: ligados por padrão, um degrau abaixo do modelo principal (fable→opus→sonnet→haiku→haiku) via `CLAUDE_CODE_SUBAGENT_MODEL` e `agents=` (`worker`, `explorer`). Pref `subagent_model` ("auto" ou modelo explícito) e `effort`. Desligados: `disallowed_tools=["Agent","Task"]`. Em modo plano, subagentes só leem.
- **Store**: histórico em JSONL (migração automática), `history_page`, `fsync` + `.bak` + `.corrupt`, `cleanup_old(30)`. `usage` lê o transcript de forma incremental.
- **Main**: `_emit` com fila e thread emissora (junta `text` a cada ~40 ms); `run_shell` com `Popen` + `taskkill /T`, `stop_shell`, contexto `!` por conversa (início+fim, 30 min); `open_terminal` valida o comando; arquivos executáveis só são mostrados no Explorer; `read_file` com `known_mtime/known_size`; `reveal_path`, `copy_text`, `get_abs_path`; `get_models`.
- **Anexos**: acima de ~20 mil caracteres/10 páginas vai prévia + caminho salvo (`add_dirs` libera o Read); orçamento de 60 mil caracteres por mensagem; `.csv` cru; PDF escaneado respeita `image_mode`; OCR de vídeo deduplica quadros.
