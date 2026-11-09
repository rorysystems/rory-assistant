"""Ponte entre a janela e o Claude Code (Claude Agent SDK).

- Um loop asyncio numa thread própria; a janela chama métodos síncronos e recebe
  eventos por `emit(dict)` (main.py repassa para o JS).
- Uma sessão por projeto (`ClaudeSDKClient`, cwd = pasta do projeto), conectada sob
  demanda e retomada pelo `session_id` salvo, então a conversa sobrevive a reinícios.
- Texto chega em pedaços (`include_partial_messages`), ferramentas aparecem na tela,
  e pedidos de permissão viram um cartão na conversa (`can_use_tool`).

Eventos emitidos (campo `type`), todos com `conv` (id da conversa):
  status   {busy}
  text     {text}                         pedaço de texto da resposta
  tool     {id, name, summary}            Claude começou a usar uma ferramenta
  tool_end {id, error, output}            resultado da ferramenta
  permission {id, tool, title, summary}   pede Permitir / Negar / Sempre
  permission_done {id}
  plan     {id, plan, mode}               ExitPlanMode: plano inteiro para aprovar
  question {id, questions}                AskUserQuestion: perguntas com opções (resposta = JSON em `message`)
                                          (resposta: accept_edits | default | deny + texto)
  mode_changed {mode}                     modo de permissão mudou (aprovação do plano ou CLI)
  done     {text, cost, error}            fim do turno
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    CLIConnectionError,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    UserMessage,
)
from claude_agent_sdk.types import (
    PermissionUpdate,
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
)

from . import config
from .store import Store, now_iso

Emit = Callable[[dict[str, Any]], None]


def _hide_child_consoles() -> None:
    """No Windows, o app não tem console; sem isto o claude.exe (programa de console)
    ganha uma janela de terminal vazia, e fechá-la mata o Claude no meio da conversa
    ("Cannot write to terminated process"). O SDK não expõe creationflags, então o
    anyio.open_process ganha CREATE_NO_WINDOW por padrão."""
    if sys.platform != "win32":
        return
    import subprocess

    import anyio

    original = anyio.open_process
    if getattr(original, "_rory_patched", False):
        return

    async def open_process(*args: Any, **kwargs: Any) -> Any:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return await original(*args, **kwargs)

    open_process._rory_patched = True  # type: ignore[attr-defined]
    anyio.open_process = open_process  # type: ignore[assignment]


_hide_child_consoles()

GUIDE = f"""
# Ambiente: {config.APP_NAME}
Você está rodando dentro do {config.APP_NAME}, um app de desktop (janela de chat) que
usa o Claude Code por baixo. O usuário é desenvolvedor e conversa com você por essa
janela, não por um terminal:
- Imagens e arquivos que ele cola/anexa chegam como blocos na própria mensagem
  (PDF vem página a página; Office/CSV convertido para markdown).
- Slash commands interativos do terminal (/login, /config etc.) não funcionam
  aqui; se ele pedir, explique o equivalente ou sugira abrir no terminal. (O /mcp tem
  tela própria no app: lista os servidores e permite autenticar, reconectar e ativar/desativar.)
- Respostas são renderizadas em markdown com destaque de código.
- Ferramentas que alteram coisas pedem permissão na tela, a não ser que o modo do
  projeto libere.
- Áudio e vídeo anexados chegam já transcritos no PC (texto), e a fala do microfone
  chega como texto digitado.
""".strip()

SUBAGENTS_GUIDE = """
# Trabalho com subagentes (preferência do usuário, ligada no app)
Subagentes (ferramenta Agent) rodam num modelo mais barato que o seu. Além dos padrões do
Claude Code há dois: "worker" (executa uma parte bem delimitada) e "explorer" (só lê e
pesquisa). Em tarefas grandes, aja como coordenador: divida o trabalho e delegue as partes,
em paralelo quando forem independentes; você planeja, distribui, confere o que voltou e
junta o resultado. Exemplos: revisão de código (um subagente por módulo/área ou por tipo de
problema), investigação em várias frentes, mudanças em áreas separadas do código, pesquisa
em muitos arquivos.
Quem decide é você: avalie se vale delegar e se o modelo menor dá conta da tarefa. Se não
der (raciocínio difícil, decisão delicada, edição que exige o contexto todo) ou se a tarefa
for rápida ou pequena (uma pergunta, um ajuste em poucos arquivos, edições encadeadas no
mesmo arquivo), faça você mesmo: ali subagentes só custam tempo e tokens.
Em modo plano, subagentes só leem; nada é alterado até o plano ser aprovado.
Ao começar a delegar, diga em uma linha ao usuário como dividiu o trabalho; cada
subagente aparece na tela com a tarefa e o que está fazendo.
""".strip()


def build_guide(prefs: dict[str, Any]) -> str:
    guide = GUIDE
    if prefs.get("use_subagents", True):
        guide += "\n\n" + SUBAGENTS_GUIDE
    return guide


# Modelos escolhíveis (id aceito por /model e pelo SDK + rótulo na tela).
MODEL_CHOICES: list[dict[str, str]] = [
    {"id": "default", "label": "Padrão do Claude Code"},
    {"id": "fable", "label": "Fable"},
    {"id": "opus", "label": "Opus"},
    {"id": "sonnet", "label": "Sonnet"},
    {"id": "haiku", "label": "Haiku"},
]
# Subagentes: "auto" = um degrau abaixo do principal, ou um modelo fixo.
SUBAGENT_MODEL_CHOICES: list[dict[str, str]] = [
    {"id": "auto", "label": "Automático (um degrau abaixo do principal)"},
    *[m for m in MODEL_CHOICES if m["id"] != "default"],
]
# Escada: principal -> subagente
SUBAGENT_LADDER = {"fable": "opus", "mythos": "opus", "opus": "sonnet", "sonnet": "haiku", "haiku": "haiku"}
EFFORTS = ("low", "medium", "high")
FLOOR_CAP = 100000  # teto do piso medido de contexto
IDLE_DISCONNECT_S = 600  # parada há mais que isto: fecha o claude.exe (o cache já expirou)


def subagent_model(main_model: str | None, pref: Any = "auto") -> str:
    """Modelo dos subagentes. pref (Configurações) 'auto' = um degrau abaixo do principal
    (padrão/desconhecido conta como opus -> sonnet); senão o alias/id escolhido."""
    pref = str(pref or "auto").strip()
    if pref and pref not in ("auto", "default"):
        return pref
    name = (main_model or "").lower()
    for key, sub in SUBAGENT_LADDER.items():
        if key in name:
            return sub
    return "sonnet"


def compact_limit(prefs: dict[str, Any]) -> int:
    """Pref auto_compact_at (Configurações): quanto a conversa acumula além do piso antes
    de compactar; 0 = desligado."""
    try:
        return max(0, int(prefs.get("auto_compact_at", 60000) or 0))
    except (TypeError, ValueError):
        return 60000


# Piso = contexto logo depois de compactar: instruções do Claude Code, ferramentas,
# CLAUDE.md, skills e o que ele reanexa (não some ao compactar). Até medir o da
# conversa (1ª resposta depois de uma compactação), usa esta estimativa.
FLOOR_ESTIMATE = 50000

# Instrução do /compact: resumo curto (o resumo padrão repete bastante coisa)
COMPACT_COMMAND = (
    "/compact Resumo curto e objetivo: o objetivo da conversa, decisões tomadas, estado "
    "atual dos arquivos mexidos e próximos passos. Não repita código, diffs nem saídas "
    "de ferramentas; cite só caminhos de arquivo e nomes."
)


def _session_missing(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(
        key in text
        for key in ("no conversation found", "session not found", "no such session", "could not find session")
    )


async def _quiet_disconnect(client: ClaudeSDKClient) -> None:
    try:
        await client.disconnect()
    except BaseException:  # noqa: BLE001
        pass


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tool_summary(name: str, data: dict[str, Any]) -> str:
    if name in ("Agent", "Task"):  # subagente: a tarefa e o tipo
        desc = _short(data.get("description") or data.get("prompt") or "", 120)
        kind = data.get("subagent_type")
        return f"{desc} ({kind})" if kind and kind != "general-purpose" else desc
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if key in data and data[key]:
            return _short(data[key])
    return _short(data) if data else ""


def _result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


class Session:
    def __init__(self, hub: "AgentHub", project_id: str) -> None:
        self.hub = hub
        self.project_id = project_id
        self.client: ClaudeSDKClient | None = None
        self.busy = False
        self.permissions: dict[str, asyncio.Future[str]] = {}
        # pedidos esperando resposta (o evento inteiro), para redesenhar o cartão
        # quando o usuário volta a esta conversa
        self.pending_perms: dict[str, dict[str, Any]] = {}
        self.parts: list[dict[str, Any]] = []
        self.streamed_text = False
        self.turn_open = False  # há uma resposta em andamento na tela
        self.tool_parts: dict[str, dict[str, Any]] = {}  # ferramentas esperando resultado
        self.state_events = False  # o Claude Code manda session_state_changed?
        self.reader: asyncio.Task[None] | None = None
        self.closing = False
        # /comandos e skills disponíveis nesta pasta: {name: {description, hint, terminal}}
        self.commands: dict[str, dict[str, Any]] = {}
        self.terminal_commands: set[str] = set()
        # compactação em segundo plano (ver _maybe_compact)
        self.context = 0  # tokens de contexto da última resposta (conversa principal)
        self.measure_floor = False  # a próxima resposta mede o piso (logo depois de compactar)
        self.compacting = False
        self.compact_tried = False  # uma tentativa por mensagem do usuário
        self.held: list[tuple[Any, dict[str, Any]]] = []  # mensagens esperando a compactação
        self.stale = False  # configuração mudou: reconecta (retoma) na próxima mensagem
        self.sig: tuple[Any, ...] = ()  # opções com que o cliente atual foi aberto
        self.gen = 0  # número da conexão atual (o leitor antigo não mexe na nova)
        self.last_active = time.monotonic()
        self.perm_messages: dict[str, str] = {}  # texto digitado ao negar um plano
        self.mode_set_at = 0.0  # quando o app mudou o modo (ignora relatos atrasados do CLI)

    @property
    def project(self) -> dict[str, Any]:
        project = self.hub.store.get(self.project_id)
        if not project:
            raise RuntimeError("Conversa não encontrada")
        return project

    def emit(self, **event: Any) -> None:
        self.hub.emit({"conv": self.project_id, **event})

    # --- permissões -----------------------------------------------------------
    async def _force_ask(self, input_data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
        """Hook PreToolUse do ExitPlanMode: manda a decisão para _can_use_tool mesmo com
        regra "sempre permitir" salva (ex.: clicada num cartão antigo) ou modo que libera."""
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "O plano precisa da sua aprovação.",
            }
        }

    async def _can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        self.last_active = time.monotonic()
        if tool_name == "ExitPlanMode":
            return await self._ask_plan(tool_input)
        if tool_name == "AskUserQuestion":
            return await self._ask_question(tool_input)
        request_id = uuid.uuid4().hex[:10]
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.permissions[request_id] = future
        event = {
            "type": "permission",
            "id": request_id,
            "tool": tool_name,
            "title": ctx.title or f"Claude quer usar {tool_name}",
            "summary": tool_summary(tool_name, tool_input),
            "detail": _short(tool_input, 4000),
            "can_always": bool(ctx.suggestions),
            # por que perguntou (inclusive com "liberar tudo": checagens de segurança
            # do Claude Code e hooks continuam pedindo)
            "reason": ctx.decision_reason or "",
            "blocked_path": ctx.blocked_path or "",
            "mode": self.project.get("permission_mode") or "default",
        }
        self.pending_perms[request_id] = event
        self.emit(**event)
        try:
            answer = await future
        finally:
            self.permissions.pop(request_id, None)
            self.pending_perms.pop(request_id, None)
            self.emit(type="permission_done", id=request_id)
        if answer == "always":
            return PermissionResultAllow(updated_permissions=ctx.suggestions or None)
        if answer == "allow":
            return PermissionResultAllow()
        return PermissionResultDeny(message="O usuário negou esta ação na tela.")

    async def _ask_plan(self, tool_input: dict[str, Any]) -> PermissionResultAllow | PermissionResultDeny:
        """ExitPlanMode: sempre passa pelo usuário, com o plano inteiro. Sem "sempre permitir"."""
        request_id = uuid.uuid4().hex[:10]
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.permissions[request_id] = future
        event = {
            "type": "plan",
            "id": request_id,
            "plan": str(tool_input.get("plan") or ""),
            "mode": self.project.get("permission_mode") or "default",
        }
        self.pending_perms[request_id] = event
        self.emit(**event)
        try:
            answer = await future
        finally:
            self.permissions.pop(request_id, None)
            self.pending_perms.pop(request_id, None)
            self.emit(type="permission_done", id=request_id)
        text = self.perm_messages.pop(request_id, "").strip()
        if answer in ("accept_edits", "default"):
            mode = "acceptEdits" if answer == "accept_edits" else "default"
            self._set_mode(mode)
            asyncio.get_running_loop().create_task(self._apply_mode(mode))
            return PermissionResultAllow(
                updated_permissions=[PermissionUpdate(type="setMode", mode=mode, destination="session")]  # type: ignore[arg-type]
            )
        return PermissionResultDeny(
            message=text or "O usuário não aprovou o plano: continue no modo plano e pergunte o que ajustar.",
            interrupt=False,
        )

    async def _ask_question(self, tool_input: dict[str, Any]) -> PermissionResultAllow | PermissionResultDeny:
        """AskUserQuestion: cartão com as perguntas e opções; a resposta volta ao Claude em
        `answers` (texto da pergunta -> rótulo escolhido), como no terminal."""
        questions = tool_input.get("questions") if isinstance(tool_input.get("questions"), list) else []
        request_id = uuid.uuid4().hex[:10]
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.permissions[request_id] = future
        event = {"type": "question", "id": request_id, "questions": questions}
        self.pending_perms[request_id] = event
        self.emit(**event)
        try:
            answer = await future
        finally:
            self.permissions.pop(request_id, None)
            self.pending_perms.pop(request_id, None)
            self.emit(type="permission_done", id=request_id)
        raw = self.perm_messages.pop(request_id, "")
        if answer != "answer":
            return PermissionResultDeny(message="O usuário recusou responder as perguntas.", interrupt=False)
        try:
            answers = json.loads(raw)
        except ValueError:
            answers = {}
        if not isinstance(answers, dict):
            answers = {}
        return PermissionResultAllow(updated_input={**tool_input, "answers": answers})

    def _set_mode(self, mode: str) -> None:
        """Grava o modo no store e avisa a tela."""
        self.mode_set_at = time.monotonic()
        self.hub.store.update(self.project_id, permission_mode=mode)
        self.emit(type="mode_changed", mode=mode)

    async def _apply_mode(self, mode: str) -> None:
        # depois da resposta do cartão, para não pedir ao CLI no meio do callback dele
        await asyncio.sleep(0.2)
        if self.client is not None:
            try:
                await self.client.set_permission_mode(mode)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                pass

    def _follow_cli_mode(self, mode: Any) -> None:
        """O CLI informou o modo real (ex.: saiu do plano sozinho): a tela segue."""
        if not isinstance(mode, str) or not mode:
            return
        if time.monotonic() - self.mode_set_at < 3:
            return  # acabamos de mudar; o relato pode ser anterior
        if (self.project.get("permission_mode") or "default") != mode:
            self._set_mode(mode)

    def answer_permission(self, request_id: str, answer: str, message: str = "") -> None:
        future = self.permissions.get(request_id)
        if future and not future.done():
            if message:
                self.perm_messages[request_id] = message
            future.set_result(answer)

    # --- conexão --------------------------------------------------------------
    def _main_model(self) -> str | None:
        return self.project.get("model") or config.DEFAULT_MODEL

    def _sub_model(self, prefs: dict[str, Any]) -> str:
        return subagent_model(self._main_model(), prefs.get("subagent_model"))

    def _signature(self) -> tuple[Any, ...]:
        """O que vale só ao (re)conectar; se mudar, a sessão fica desatualizada."""
        prefs = self.hub.store.state.get("prefs") or {}
        return (
            self._main_model(),
            bool(prefs.get("use_subagents", True)),
            self._sub_model(prefs),
            self._effort(prefs),
            bool(prefs.get("claudeai_connectors", False)),
            compact_limit(prefs),
        )

    @staticmethod
    def _effort(prefs: dict[str, Any]) -> str | None:
        value = prefs.get("effort")
        return value if value in EFFORTS else None

    @staticmethod
    def _agents(model: str) -> dict[str, AgentDefinition]:
        return {
            "worker": AgentDefinition(
                description="Executa uma parte bem delimitada de uma tarefa (editar arquivos, rodar comandos, corrigir). Passe o contexto necessário no prompt.",
                prompt="Você executa uma parte delimitada de uma tarefa maior, exatamente como pedido. Seja direto e devolva um resumo curto do que fez, com os caminhos dos arquivos mexidos.",
                model=model,
            ),
            "explorer": AgentDefinition(
                description="Só lê e pesquisa no código e na web, sem alterar nada. Devolve um resumo com caminhos e trechos relevantes.",
                prompt="Você só lê e pesquisa; nunca altera arquivos nem roda comandos que mudem algo. Devolva um resumo curto e objetivo, citando caminhos de arquivo e linhas.",
                tools=["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
                model=model,
            ),
        }

    def _options(self) -> ClaudeAgentOptions:
        project = self.project
        prefs = self.hub.store.state.get("prefs") or {}
        extra: dict[str, Any] = {}
        if prefs.get("use_subagents", True):
            extra["agents"] = self._agents(self._sub_model(prefs))
        else:
            extra["disallowed_tools"] = ["Agent", "Task"]
        effort = self._effort(prefs)
        if effort:
            extra["effort"] = effort
        return ClaudeAgentOptions(
            cwd=project["path"],
            resume=project.get("session_id") or None,
            include_partial_messages=True,
            can_use_tool=self._can_use_tool,
            permission_mode=project.get("permission_mode") or "default",
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": build_guide(self.hub.store.state.get("prefs") or {}),
            },
            cli_path=self.hub.cli_path,
            model=self._main_model(),
            add_dirs=[str(config.ATTACH_DIR)],  # anexos grandes: o Read não pede permissão
            hooks={"PreToolUse": [HookMatcher(matcher="ExitPlanMode", hooks=[self._force_ask])]},
            stderr=lambda line: print(f"[claude] {line}"),
            env=self._env(),
            # o padrão do SDK é 1 MB por mensagem JSON; um Read de imagem/PDF grande
            # (vem em base64) passa disso e derruba a sessão com CLIJSONDecodeError
            max_buffer_size=64 * 1024 * 1024,
            **extra,
        )

    def _env(self) -> dict[str, str]:
        # session_state_changed (running/idle) chega até nós: é o que diz se o
        # Claude ainda está trabalhando depois de uma mensagem mandada no meio
        env = {"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"}
        prefs = self.hub.store.state.get("prefs") or {}
        limit = compact_limit(prefs)
        if limit:
            # garantia: o próprio Claude Code compacta no meio de uma tarefa longa que
            # estoure muito. Ele dispara ~33 mil abaixo deste valor, então a folga é
            # grande para a compactação em segundo plano vir sempre antes.
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(limit + 150000)
        if prefs.get("use_subagents", True):
            # subagentes embutidos (Explore etc.) no modelo escolhido / um degrau abaixo
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = self._sub_model(prefs)
        if not prefs.get("claudeai_connectors", False):
            # conectores do claude.ai (Gmail, Drive, Agenda...) fora: ~3–5 mil tokens a
            # menos em toda mensagem. Liga nas Configurações quando precisar.
            env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
        return env

    @property
    def floor(self) -> int:
        return int(self.project.get("ctx_floor") or FLOOR_ESTIMATE)

    async def _connect(self) -> ClaudeSDKClient:
        if self.client is None:
            client = ClaudeSDKClient(options=self._options())
            try:
                await client.connect()
            except Exception as exc:  # noqa: BLE001
                await _quiet_disconnect(client)
                # só uma sessão realmente sumida (apagada/expirada) recomeça do zero;
                # timeout, MCP lento ou CLI ausente mantêm o session_id (a memória da conversa)
                if not (self.project.get("session_id") and _session_missing(exc)):
                    raise
                self.hub.store.update(self.project_id, session_id=None)
                client = ClaudeSDKClient(options=self._options())
                try:
                    await client.connect()
                except Exception:
                    await _quiet_disconnect(client)
                    raise
            self.client = client
            self.closing = False
            self.stale = False
            self.sig = self._signature()
            self.gen += 1
            self.reader = asyncio.get_running_loop().create_task(self._read_loop(client, self.gen))
            try:
                info = await client.get_server_info() or {}
                self._add_commands(info.get("commands") or [])
            except Exception:
                pass
        return self.client

    def _add_commands(self, items: list[Any], kind: str = "comando") -> None:
        """Aceita a lista do get_server_info (dicts) ou a do init (nomes)."""
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").lstrip("/")
                desc = item.get("description") or ""
                hint = item.get("argumentHint") or item.get("argument_hint") or ""
            else:
                name, desc, hint = str(item).lstrip("/"), "", ""
            if not name:
                continue
            current = self.commands.get(name, {})
            self.commands[name] = {
                "description": desc or current.get("description", ""),
                "hint": hint or current.get("hint", ""),
                "kind": current.get("kind", kind),
            }

    async def list_commands(self) -> list[dict[str, Any]]:
        await self._connect()
        return sorted(
            (
                {"name": name, **info, "terminal": name in self.terminal_commands}
                for name, info in self.commands.items()
            ),
            key=lambda c: c["name"],
        )

    async def mcp_servers(self) -> list[dict[str, Any]]:
        """Servidores MCP da pasta da conversa, com estado, URL e ferramentas (para o /mcp)."""
        client = await self._connect()
        data = await client.get_mcp_status() or {}
        out = []
        for item in data.get("mcpServers") or []:
            config = item.get("config") or {}
            out.append(
                {
                    "name": str(item.get("name") or ""),
                    "status": str(item.get("status") or "pending"),
                    "error": str(item.get("error") or ""),
                    "scope": str(item.get("scope") or ""),
                    "type": str(config.get("type") or "stdio"),
                    "url": str(config.get("url") or config.get("command") or ""),
                    "tools": [str(t.get("name") or "") for t in item.get("tools") or [] if isinstance(t, dict)],
                    "server": (item.get("serverInfo") or {}).get("name", ""),
                }
            )
        return out

    async def mcp_action(self, name: str, action: str) -> dict[str, Any]:
        """reconnect | enable | disable | auth. No `auth` o CLI sobe o servidor local que
        recebe o retorno do login e devolve a URL para abrir no navegador (quem abre é o app)."""
        client = await self._connect()
        if action == "reconnect":
            await client.reconnect_mcp_server(name)
        elif action in ("enable", "disable"):
            await client.toggle_mcp_server(name, action == "enable")
        elif action == "auth":
            query = getattr(client, "_query", None)
            if query is None:
                raise RuntimeError("A conexão com o Claude Code não está pronta.")
            reply = await query._send_control_request({"subtype": "mcp_authenticate", "serverName": name})
            reply = reply or {}
            return {"auth_url": str(reply.get("authUrl") or ""), "waiting": bool(reply.get("callbackExpected"))}
        else:
            raise ValueError(f"ação desconhecida: {action}")
        return {}

    async def disconnect(self) -> None:
        for future in self.permissions.values():
            if not future.done():
                future.set_result("deny")
        if self.client is not None:
            self.closing = True
            client, self.client = self.client, None
            try:
                await client.disconnect()
            except Exception:
                pass

    # --- envio ----------------------------------------------------------------
    async def submit(self, blocks: list[dict[str, Any]], entry: dict[str, Any]) -> str:
        """Manda na hora, mesmo com o Claude trabalhando, como no terminal: o Claude Code
        absorve a mensagem no turno em andamento ou responde logo em seguida.

        Devolve 'sent', 'mid_turn' (chegou com o Claude trabalhando) ou 'error'."""
        mid_turn = self.busy
        if mid_turn and self.turn_open and self.parts:
            # o que já foi respondido fica antes da mensagem nova no histórico
            self._write_assistant(self.parts, None, None)
            self.parts = []
            self.streamed_text = False
        self.hub.store.append_history(self.project_id, entry)
        # "/comando ..." sozinho vai como texto puro: é assim que o Claude Code reconhece
        # slash commands e skills (em lista de blocos, viraria só uma mensagem comum)
        content: Any = blocks
        if len(blocks) == 1 and blocks[0].get("type") == "text" and blocks[0]["text"].startswith("/"):
            content = blocks[0]["text"]
        self.compact_tried = False
        if self.compacting:
            # mandar no meio cancelaria a compactação: espera ela terminar (_end_compact)
            self.held.append((content, entry))
            return "held"
        return await self._query(content, mid_turn)

    async def _query(self, content: Any, mid_turn: bool = False) -> str:
        async def message() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

        self.last_active = time.monotonic()
        self._set_busy(True)
        try:
            if self.client is not None and not mid_turn and (self.stale or self.sig != self._signature()):
                await self.disconnect()  # reconecta (retoma a sessão) com a configuração nova
            client = await self._connect()
            # o processo do Claude pode ter morrido desde a última mensagem (PC
            # suspenso, processo encerrado): reconecta e tenta mais uma vez
            for attempt in range(2):
                try:
                    await client.query(message())
                    break
                except CLIConnectionError:
                    await self.disconnect()
                    if attempt:
                        raise
                    client = await self._connect()
        except Exception as exc:  # noqa: BLE001
            self.turn_open = True
            self._finish_turn(None, f"{type(exc).__name__}: {exc}", "")
            self._set_busy(False)
            return "error"
        return "mid_turn" if mid_turn else "sent"

    # --- compactação em segundo plano ---------------------------------------
    def _maybe_compact(self) -> None:
        """Logo depois de uma resposta (cache quente: reler sai ~10% do preço), se o
        contexto passou do limite, manda /compact sem aparecer na conversa. Compactar só
        ao reabrir sairia caro: com o cache frio, o Claude relê tudo pelo preço cheio."""
        limit = compact_limit(self.hub.store.state.get("prefs") or {})
        if (
            not limit
            or self.compacting
            or self.compact_tried
            or self.busy
            or self.turn_open
            or self.held
            or self.client is None
            or self.context - self.floor < limit
        ):
            return
        self.compact_tried = True
        self.compacting = True
        self.emit(type="compacting", on=True, context=self.context)

        async def run() -> None:
            async def message() -> AsyncIterator[dict[str, Any]]:
                yield {"type": "user", "message": {"role": "user", "content": COMPACT_COMMAND}, "parent_tool_use_id": None}

            try:
                assert self.client is not None
                await self.client.query(message())
            except Exception:  # noqa: BLE001
                self._end_compact()

        asyncio.get_running_loop().create_task(run())

    def _end_compact(self) -> None:
        if not self.compacting:
            return
        self.compacting = False
        self.emit(type="compacting", on=False)
        held, self.held = self.held, []
        if held:

            async def flush() -> None:
                for content, _entry in held:
                    await self._query(content, mid_turn=self.busy)

            asyncio.get_running_loop().create_task(flush())

    def _set_busy(self, busy: bool) -> None:
        if self.compacting:
            return  # a compactação em segundo plano não deixa a conversa "ocupada"
        if busy != self.busy:
            self.busy = busy
            self.emit(type="status", busy=busy)

    def _ensure_turn(self) -> None:
        if not self.turn_open:
            self.turn_open = True
            self.parts = []
            self.streamed_text = False
            self.emit(type="turn_start")

    def _write_assistant(self, parts: list[dict[str, Any]], cost: float | None, error: str | None) -> None:
        if parts or error:
            self.hub.store.append_history(
                self.project_id,
                {"role": "assistant", "parts": parts, "cost": cost, "error": error, "time": now_iso()},
            )

    def _finish_turn(self, cost: float | None, error: str | None, result_text: str) -> None:
        if not self.turn_open:
            return
        if not any(p["type"] == "text" for p in self.parts) and result_text and not error:
            self._add_text(result_text, emit=False)
        self._write_assistant(self.parts, cost, error)
        self.emit(type="done", parts=self.parts, cost=cost, error=error)
        self.turn_open = False
        self.parts = []

    async def _read_loop(self, client: ClaudeSDKClient, gen: int) -> None:
        """Lê tudo o que o Claude Code manda, turno após turno, até a conexão fechar."""
        error: str | None = None
        try:
            async for msg in client.receive_messages():
                try:
                    self._handle(msg)
                except Exception:  # noqa: BLE001
                    # uma mensagem estranha não derruba a leitura
                    print("[rory] erro ao tratar mensagem:\n" + traceback.format_exc())
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await _quiet_disconnect(client)  # não deixa o claude.exe órfão
            if self.gen == gen:  # conexão mais nova no lugar: o leitor antigo não mexe nela
                if self.client is client:
                    self.client = None  # o processo acabou: a próxima mensagem reconecta
                if self.turn_open:
                    self._finish_turn(None, error or ("Conexão com o Claude encerrada." if not self.closing else None), "")
                self._set_busy(False)
                self._end_compact()  # mensagens que esperavam a compactação vão agora (reconecta)

    def _handle(self, msg: Any) -> None:
        self.last_active = time.monotonic()
        if isinstance(msg, SystemMessage):
            data = msg.data or {}
            self._follow_cli_mode(data.get("permissionMode") or data.get("permission_mode"))
        if self.compacting:
            # o turno do /compact em segundo plano não aparece na conversa; só a linha
            # "contexto compactado" (compact_boundary) entra no histórico
            if isinstance(msg, SystemMessage) and msg.subtype in ("init", "compact_boundary"):
                pass
            elif isinstance(msg, ResultMessage):
                self.hub.store.update(self.project_id, session_id=msg.session_id)
                self._end_compact()
                return
            else:
                return
        if isinstance(msg, StreamEvent):
            if not msg.parent_tool_use_id:
                self._ensure_turn()
                self._on_stream(msg.event, msg.parent_tool_use_id)
        elif isinstance(msg, AssistantMessage):
            if msg.parent_tool_use_id:
                # subagente trabalhando: não vira texto na conversa, mas a linha do
                # subagente mostra quantas ações ele fez e qual a última
                parent = self.tool_parts.get(msg.parent_tool_use_id)
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        last = f"{block.name} {tool_summary(block.name, block.input)}".strip()
                        count = 1
                        if parent is not None:
                            sub = parent.setdefault("sub", {"count": 0, "last": ""})
                            sub["count"] += 1
                            sub["last"] = last
                            count = sub["count"]
                        self.emit(type="subtool", parent=msg.parent_tool_use_id, last=last, count=count)
                return
            if msg.usage:
                u = msg.usage
                self.context = sum(
                    int(u.get(k) or 0)
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                )
                if self.measure_floor and self.context:
                    # 1ª resposta depois de compactar: piso desta conversa. Inclui a mensagem
                    # nova (anexo grande inflaria o piso e desligaria a compactação), então
                    # vale o menor entre o anterior e o medido, com teto
                    self.measure_floor = False
                    prev = int(self.project.get("ctx_floor") or 0)
                    measured = min(self.context, FLOOR_CAP)
                    self.hub.store.update(self.project_id, ctx_floor=min(prev, measured) if prev else measured)
            self._ensure_turn()
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    summary = tool_summary(block.name, block.input)
                    part = {"type": "tool", "id": block.id, "name": block.name, "summary": summary}
                    self.tool_parts[block.id] = part
                    self.parts.append(part)
                    self.emit(type="tool", id=block.id, name=block.name, summary=summary)
                elif isinstance(block, TextBlock) and not self.streamed_text:
                    self._add_text(block.text)
            self.streamed_text = False
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list) and not msg.parent_tool_use_id:
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    output = _short(_result_text(block.content), 600)
                    part = self.tool_parts.pop(block.tool_use_id, None)
                    if part is not None:
                        part["error"] = bool(block.is_error)
                    self.emit(type="tool_end", id=block.tool_use_id, error=bool(block.is_error), output=output)
        elif isinstance(msg, SystemMessage):
            data = msg.data or {}
            if msg.subtype == "init":
                sid = data.get("session_id")
                if sid:
                    self.hub.store.update(self.project_id, session_id=sid)
                self._add_commands(data.get("slash_commands") or [])
                self._add_commands(data.get("skills") or [], kind="skill")
                self.terminal_commands = {str(c).lstrip("/") for c in data.get("terminal_slash_commands") or []}
            elif msg.subtype == "compact_boundary":
                # o Claude passa a ler só um resumo daqui pra trás; a tela continua
                # mostrando tudo (o histórico de exibição do app não é tocado)
                meta = data.get("compact_metadata") or {}
                entry = {
                    "role": "divider",
                    "text": "Contexto compactado: daqui para cima o Claude lembra só de um resumo",
                    "pre_tokens": meta.get("pre_tokens"),
                    "trigger": meta.get("trigger"),
                    "time": now_iso(),
                }
                self.hub.store.append_history(self.project_id, entry)
                self.emit(type="divider", **{k: v for k, v in entry.items() if k != "role"})
                self.context = 0  # a próxima resposta diz o tamanho novo
                self.measure_floor = True
            elif msg.subtype == "session_state_changed":
                # running / requires_action / idle: "ocupado" de verdade, inclusive
                # quando uma mensagem mandada no meio gera mais um turno
                self.state_events = True
                self._set_busy(data.get("state") != "idle")
                if data.get("state") == "idle":
                    self._maybe_compact()
        elif isinstance(msg, ResultMessage):
            self.hub.store.update(self.project_id, session_id=msg.session_id)
            error = None
            if msg.is_error and msg.terminal_reason not in ("aborted_streaming", "aborted_tools"):
                error = "; ".join(msg.errors or []) or msg.result or "Erro no Claude Code"
            self._ensure_turn()
            self._finish_turn(msg.total_cost_usd, error, msg.result or "")
            if not self.state_events:
                self._set_busy(False)  # Claude Code antigo, sem eventos de estado
                self._maybe_compact()

    def _add_text(self, text: str, emit: bool = True) -> None:
        if self.parts and self.parts[-1]["type"] == "text":
            self.parts[-1]["text"] += text
        else:
            self.parts.append({"type": "text", "text": text})
        if emit:
            self.emit(type="text", text=text)

    def _on_stream(self, event: dict[str, Any], parent: str | None) -> None:
        if parent:
            return
        if event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                self.streamed_text = True
                self._add_text(delta["text"])
        elif event.get("type") == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "text" and self.parts and self.parts[-1]["type"] == "text":
                # novo bloco de texto depois de outro: separa com parágrafo
                self._add_text("\n\n")

    async def interrupt(self) -> None:
        for future in self.permissions.values():
            if not future.done():
                future.set_result("deny")
        if self.client is not None and self.busy:
            try:
                await self.client.interrupt()
            except Exception:
                pass

    async def set_model(self, model: str | None) -> None:
        self.hub.store.update(self.project_id, model=model)
        self.stale = True  # o modelo dos subagentes acompanha: reconecta na próxima mensagem
        if self.client is not None:
            try:
                await self.client.set_model(model)  # vale já para a resposta em andamento
            except Exception:
                pass

    async def set_permission_mode(self, mode: str) -> None:
        self.mode_set_at = time.monotonic()
        self.hub.store.update(self.project_id, permission_mode=mode)
        if self.client is not None:
            try:
                await self.client.set_permission_mode(mode)  # type: ignore[arg-type]
            except Exception:
                await self.disconnect()


class AgentHub:
    def __init__(self, store: Store, emit: Emit) -> None:
        self.store = store
        self.emit = emit
        self.cli_path = config.find_claude()  # None = usa o CLI que vem no SDK
        self.sessions: dict[str, Session] = {}
        self.loop = asyncio.new_event_loop()
        if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            # subprocesso no Windows precisa do Proactor
            self.loop = asyncio.ProactorEventLoop()  # type: ignore[attr-defined]
        threading.Thread(target=self.loop.run_forever, daemon=True, name="agent-loop").start()
        self._run(self._idle_watch())
        threading.Thread(target=self._cleanup, daemon=True, name="store-cleanup").start()

    def _cleanup(self) -> None:
        try:
            self.store.cleanup_old(30)
        except Exception:  # noqa: BLE001
            pass

    async def _idle_watch(self) -> None:
        """Fecha o claude.exe das conversas paradas há mais de 10 min (o cache de 5 min já
        expirou, então não custa tokens); a próxima mensagem reconecta e retoma."""
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                if (
                    session.client is not None
                    and not session.busy
                    and not session.compacting
                    and not session.turn_open
                    and not session.permissions
                    and not session.held
                    and now - session.last_active > IDLE_DISCONNECT_S
                ):
                    try:
                        await session.disconnect()
                    except Exception:  # noqa: BLE001
                        pass

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def session(self, project_id: str) -> Session:
        if project_id not in self.sessions:
            self.sessions[project_id] = Session(self, project_id)
        return self.sessions[project_id]

    def send(self, project_id: str, blocks: list[dict[str, Any]], entry: dict[str, Any]) -> str:
        """Não bloqueia: agenda o envio e devolve já 'sent', 'mid_turn' (o Claude estava
        trabalhando: absorve na hora) ou 'held' (espera a compactação em segundo plano).
        Falhas (conexão etc.) chegam depois pelo evento `done` com `error`."""
        session = self.session(project_id)
        status = "held" if session.compacting else ("mid_turn" if session.busy else "sent")

        async def run() -> None:
            try:
                await session.submit(blocks, entry)
            except Exception as exc:  # noqa: BLE001
                session.turn_open = True
                session._finish_turn(None, f"{type(exc).__name__}: {exc}", "")
                session._set_busy(False)

        self._run(run())
        return status

    def set_model(self, project_id: str, model: str | None) -> None:
        self._run(self.session(project_id).set_model(model)).result(timeout=20)

    def list_commands(self, project_id: str) -> list[dict[str, Any]]:
        return self._run(self.session(project_id).list_commands()).result(timeout=60)

    def mcp_servers(self, project_id: str) -> list[dict[str, Any]]:
        return self._run(self.session(project_id).mcp_servers()).result(timeout=60)

    def mcp_action(self, project_id: str, name: str, action: str) -> dict[str, Any]:
        return self._run(self.session(project_id).mcp_action(name, action)).result(timeout=90)

    def interrupt(self, project_id: str) -> None:
        self._run(self.session(project_id).interrupt())

    def answer_permission(self, project_id: str, request_id: str, answer: str, message: str = "") -> None:
        """answer: allow | always | deny; para um cartão `plan`: accept_edits | default |
        deny (message = o que o usuário quer mudar)."""
        session = self.session(project_id)
        self.loop.call_soon_threadsafe(session.answer_permission, request_id, answer, message)

    def set_permission_mode(self, project_id: str, mode: str) -> None:
        self._run(self.session(project_id).set_permission_mode(mode)).result(timeout=15)

    def reset(self, project_id: str) -> None:
        self._run(self.session(project_id).disconnect()).result(timeout=15)
        self.store.update(project_id, session_id=None, ctx_floor=None)

    def disconnect_all(self) -> None:
        """Fecha os processos do Claude (troca de conta). Os session_id ficam salvos, então
        cada conversa reconecta e retoma na próxima mensagem."""

        async def close_idle() -> None:
            await asyncio.gather(
                *(s.disconnect() for s in list(self.sessions.values()) if not s.busy), return_exceptions=True
            )

        try:
            self._run(close_idle()).result(timeout=20)
        except Exception:
            pass

    def mark_all_stale(self) -> None:
        """Configuração global mudou (subagentes, esforço, modelo dos subagentes...): cada
        sessão reconecta (retomando) só na próxima mensagem dela, sem derrubar nada agora."""
        for session in list(self.sessions.values()):
            session.stale = True

    def forget(self, project_id: str) -> None:
        """Encerra e descarta a sessão (conversa apagada)."""
        session = self.sessions.pop(project_id, None)
        if session:
            self._run(session.disconnect()).result(timeout=15)

    def live_parts(self, project_id: str) -> list[dict[str, Any]]:
        """O que já foi respondido no turno em andamento (para quem volta à conversa no
        meio da resposta: os eventos chegaram enquanto a tela mostrava outra conversa)."""
        session = self.sessions.get(project_id)
        if not session or not session.busy or not session.turn_open:
            return []
        return json.loads(json.dumps(session.parts))

    def pending_permissions(self, project_id: str) -> list[dict[str, Any]]:
        """Pedidos de permissão ainda sem resposta (o Claude está parado esperando)."""
        session = self.sessions.get(project_id)
        return list(session.pending_perms.values()) if session else []

    def context_floor(self, project_id: str) -> int:
        conv = self.store.get(project_id) or {}
        return int(conv.get("ctx_floor") or FLOOR_ESTIMATE)

    def is_compacting(self, project_id: str) -> bool:
        session = self.sessions.get(project_id)
        return bool(session and session.compacting)

    def is_busy(self, project_id: str) -> bool:
        session = self.sessions.get(project_id)
        return bool(session and session.busy and not session.compacting)

    def shutdown(self) -> None:
        async def close_all() -> None:
            await asyncio.gather(*(s.disconnect() for s in list(self.sessions.values())), return_exceptions=True)

        try:
            self._run(close_all()).result(timeout=10)
        except Exception:
            pass
