"""Janela do Rory Assistant (pywebview) e a API que o JavaScript chama."""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import webview

from . import config, convert, usage
from .agent import AgentHub
from .shellcomplete import ShellCompleter
from .store import Store, now_iso

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
SHELL_TIMEOUT = 600  # s: comando "!" que passar disso é parado (a árvore de processos toda)
SHELL_HEAD_BYTES = 10_000  # início da saída guardado (o 1º erro de build costuma estar no topo)
SHELL_TAIL_BYTES = 20_000  # fim da saída guardado
SHELL_CTX_HEAD = 1_500  # caracteres do início que vão ao Claude
SHELL_CTX_TAIL = 2_500  # caracteres do fim que vão ao Claude
SHELL_CTX_TTL = 30 * 60  # s: contexto de "!" mais velho que isto é descartado
SHELL_CTX_KEEP = 5  # comandos guardados por conversa
EMIT_COALESCE_S = 0.04  # pedaços de texto do streaming são juntados a cada ~40 ms
HISTORY_PAGE = 40  # mensagens enviadas ao abrir uma conversa

# explorador de arquivos
FILES_HIDDEN = {".git"}
FILES_MAX_ENTRIES = 2000
FILES_TEXT_MAX = 1_000_000  # bytes mostrados de um arquivo de texto
FILES_IMAGE_MAX = 15_000_000
FILES_DOC_MAX = 25_000_000
FILES_BIG_TEXT = 50_000  # caracteres: acima disto o visualizador não faz highlight (campo `big`)
FILES_IMAGE_EDGE = 2048  # imagens maiores que isto são reduzidas antes de ir para a tela
FILES_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon", ".svg": "image/svg+xml",
}


def _clean_powershell(text: str) -> str:
    """O powershell.exe com saída redirecionada manda os erros dele em CLIXML: vira texto."""
    import html
    import re

    def decode(match: re.Match[str]) -> str:
        lines = re.findall(r'<S S="Error">(.*?)</S>', match.group(0), re.S)
        body = "".join(html.unescape(line) for line in lines)
        body = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), body)
        return body.replace("\r\n", "\n").rstrip()

    text = re.sub(r"#< CLIXML\r?\n?", "", text)
    return re.sub(r"<Objs [^>]*>.*?</Objs>", decode, text, flags=re.S)


def _shell_context(items: list[dict[str, Any]]) -> str:
    """Comandos que o usuário rodou com "!" desde a última mensagem (formato do terminal)."""
    parts = ["(O usuário rodou estes comandos direto no terminal desta pasta; é só contexto.)"]
    for item in items:
        parts.append(
            f"<bash-input>{item['command']}</bash-input>\n"
            f"<bash-stdout>{item['output']}</bash-stdout>\n<bash-exit-code>{item['code']}</bash-exit-code>"
        )
    return "\n".join(parts)


def _head_tail(text: str, head: int, tail: int) -> str:
    """Início e fim do texto, com o meio resumido (o topo e o fim são o que importa)."""
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n… ({len(text) - head - tail} caracteres omitidos) …\n{text[-tail:]}"


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Mata o processo e todos os filhos (um `npm run dev` segura o pipe depois que o pai morre)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=15, creationflags=NO_WINDOW,
            )
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except OSError:
        pass


# Arquivos que o sistema EXECUTA ao abrir: nunca vão pelo programa padrão, só aparecem na pasta.
DANGEROUS_EXT = {
    ".bat", ".cmd", ".exe", ".com", ".scr", ".pif", ".ps1", ".psm1", ".psd1", ".ps1xml", ".vbs", ".vbe",
    ".vb", ".wsf", ".wsh", ".wsc", ".sct", ".lnk", ".hta", ".js", ".jse", ".mjs", ".cjs", ".url", ".msi",
    ".msp", ".msc", ".reg", ".jar", ".cpl", ".inf", ".gadget", ".application", ".appref-ms", ".py", ".pyw",
    ".pyz", ".sh", ".command", ".app", ".pkg", ".dmg", ".desktop", ".run", ".appimage", ".workflow",
    ".scpt", ".action", ".terminal", ".webloc",
}


def _is_dangerous(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in DANGEROUS_EXT or p.name.lower().endswith((".exe.config",))


PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "plan"}


class Api:
    """Métodos chamados pelo JS via `window.pywebview.api.*` (cada chamada roda numa thread)."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._ready = threading.Event()
        self.store = Store()
        self.hub = AgentHub(self.store, self._emit)
        self.shell = ShellCompleter()
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._shells: dict[str, subprocess.Popen[bytes]] = {}  # "!comando" em andamento, por conversa
        self._shell_stopped: set[str] = set()
        self._shell_hist: dict[str, list[str]] = {}  # comandos "!" por conversa (sugestões)
        threading.Thread(target=self._emit_loop, daemon=True, name="emitter").start()
        # limpeza de anexos/histórico antigos fora do caminho de abertura da janela
        cleanup = getattr(self.store, "cleanup_old", None)
        if cleanup:
            threading.Thread(target=self._safe_cleanup, args=(cleanup,), daemon=True).start()

    @staticmethod
    def _safe_cleanup(cleanup: Any) -> None:
        try:
            cleanup()
        except Exception as error:  # noqa: BLE001
            print(f"[cleanup] {error}")

    def _attach(self, window: webview.Window) -> None:
        self._window = window

    # --- eventos para a tela: fila + thread emissora ---------------------------------------
    def _emit(self, event: dict[str, Any]) -> None:
        """Chamado pelo loop do agente: só enfileira (nunca espera a interface)."""
        self._events.put(event)

    def _emit_loop(self) -> None:
        while True:
            first = self._events.get()
            if first is None:
                return
            batch = [first]
            deadline = time.monotonic() + EMIT_COALESCE_S
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    item = self._events.get(timeout=left)
                except queue.Empty:
                    break
                if item is None:
                    return
                batch.append(item)
            self._dispatch(self._coalesce(batch))

    def _coalesce(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Junta pedaços de texto seguidos da mesma conversa; descarta o texto das conversas
        que não estão na tela (a tela recupera pelo set_active/live_parts)."""
        active = self.store.state.get("active")
        out: list[dict[str, Any]] = []
        for event in batch:
            if event.get("type") == "text":
                if event.get("conv") != active:
                    continue
                last = out[-1] if out else None
                if last and last.get("type") == "text" and last.get("conv") == event.get("conv"):
                    out[-1] = {**last, "text": (last.get("text") or "") + (event.get("text") or "")}
                    continue
            out.append(event)
        return out

    def _dispatch(self, events: list[dict[str, Any]]) -> None:
        if self._window is None or not events:
            return
        payload = json.dumps(events, ensure_ascii=False)
        script = (
            "(function(a){a.forEach(function(e){try{window.onAgentEvent&&window.onAgentEvent(e)}"
            f"catch(x){{console.error(x)}}}})}})({payload})"
        )
        try:
            self._window.run_js(script)
        except Exception as error:  # janela fechando
            print(f"[emit] {error}")

    # --- estado ---------------------------------------------------------------
    def get_state(self) -> dict[str, Any]:
        convs = self.store.conversations()
        if not convs:
            # primeira vez: já abre uma conversa na pasta do usuário
            self.store.create(config.HOME)
            convs = self.store.conversations()
        active = self.store.state.get("active")
        if not self.store.get(active or ""):
            active = convs[0]["id"]
            self.store.state["active"] = active
            self.store.save()
        return {
            "app": {"name": config.APP_NAME, "version": config.APP_VERSION},
            "conversations": convs,
            "active": active,
            "theme": self.store.state.get("theme", "dark"),
            "home": config.HOME,
        }

    # --- preferências (Configurações) ----------------------------------------------
    PREF_DEFAULTS: dict[str, Any] = {
        "image_mode": "image",  # image | ocr | both
        "video_ocr": True,
        "whisper_model": "small",
        "whisper_language": "pt",  # pt | auto | en ...
        "voice_autosend": False,  # microfone: envia direto ou só põe o texto na caixa
        "auto_compact_at": 60000,  # compacta quando a conversa acumula isso além do piso; 0 = desligado
        "claudeai_connectors": False,  # conectores do claude.ai (Gmail, Drive...): desligado economiza tokens
        "use_subagents": True,  # tarefas grandes: o Claude coordena subagentes
        "subagent_model": "auto",  # "auto" = um degrau abaixo do principal, ou um alias/id (opus, sonnet...)
        "effort": "",  # "" = padrão do Claude Code | low | medium | high
        "files_width": 0,  # largura do explorador de arquivos (px); 0 = metade da área
        "files_tree_width": 220,  # largura da árvore dentro do explorador (px)
        "files_open": True,  # explorador aberto ao iniciar (janela larga); lembra o último estado
    }

    def prefs(self) -> dict[str, Any]:
        return {**self.PREF_DEFAULTS, **(self.store.state.get("prefs") or {})}

    # prefs que mudam o prompt de sistema / env / opções do Claude Code: as sessões abertas
    # ficam "desatualizadas" e reconectam na próxima mensagem de cada uma
    SESSION_PREFS = {"use_subagents", "subagent_model", "effort", "auto_compact_at", "claudeai_connectors"}
    PREF_CHOICES: dict[str, tuple[str, ...]] = {
        "image_mode": ("image", "ocr", "both"),
        "effort": ("", "low", "medium", "high"),
    }

    def get_prefs(self) -> dict[str, Any]:
        from . import media
        from .agent import MODEL_CHOICES, SUBAGENT_MODEL_CHOICES

        return {
            **self.prefs(),
            "ocr_available": media.ocr_available(),
            "whisper_models": list(media.WHISPER_MODELS),
            "model_choices": MODEL_CHOICES,
            "subagent_model_choices": SUBAGENT_MODEL_CHOICES,
            "effort_choices": [c for c in self.PREF_CHOICES["effort"] if c],
        }

    @staticmethod
    def _coerce(default: Any, value: Any) -> Any:
        """Converte o valor para o tipo do padrão (a ponte JS manda tudo como veio da tela)."""
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "sim", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            return int(float(value))
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            return "" if value is None else str(value).strip()
        return value

    def set_pref(self, key: str, value: Any) -> dict[str, Any]:
        if key in self.PREF_DEFAULTS:
            try:
                value = self._coerce(self.PREF_DEFAULTS[key], value)
            except (TypeError, ValueError):
                return self.get_prefs()  # valor inválido: mantém o atual
            allowed = self.PREF_CHOICES.get(key)
            if allowed and value not in allowed:
                return self.get_prefs()
            prefs = self.store.state.setdefault("prefs", {})
            if prefs.get(key, self.PREF_DEFAULTS[key]) != value:
                prefs[key] = value
                self.store.save()
                if key in self.SESSION_PREFS:
                    stale = getattr(self.hub, "mark_all_stale", None)
                    (stale or self.hub.disconnect_all)()
        return self.get_prefs()

    def get_models(self, conv_id: str) -> dict[str, Any]:
        """Modelos para o seletor / comando /model: lista, o da conversa e o dos subagentes."""
        from .agent import MODEL_CHOICES, SUBAGENT_MODEL_CHOICES, subagent_model

        conv = self.store.get(conv_id) or {}
        main = conv.get("model") or config.DEFAULT_MODEL
        pref = self.prefs().get("subagent_model", "auto")
        return {
            "choices": MODEL_CHOICES,
            "subagent_choices": SUBAGENT_MODEL_CHOICES,
            "model": main,  # None = padrão do Claude Code
            "subagent_pref": pref,
            "subagent_model": subagent_model(main, pref),  # o efetivo
            "effort": self.prefs().get("effort", ""),
        }

    def transcribe_voice(self, data: str, name: str = "voz.webm") -> dict[str, Any]:
        """Botão de microfone: o JS grava (webm/opus) e manda aqui; volta o texto."""
        from . import media

        if "," in data[:200]:
            data = data.split(",", 1)[1]
        att_id = self.store.save_attachment(name, base64.b64decode(data))
        path = self.store.attachment_path(att_id)
        prefs = self.prefs()
        try:
            lang = prefs.get("whisper_language")
            result = media.transcribe(
                str(path), prefs.get("whisper_model") or "small", None if lang == "auto" else lang, beam_size=1
            )
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}
        finally:
            try:
                path.unlink()  # a gravação não precisa ficar guardada
            except OSError:
                pass
        return {"ok": True, **result}

    def set_theme(self, theme: str) -> None:
        self.store.state["theme"] = "light" if theme == "light" else "dark"
        self.store.save()

    def set_active(self, conv_id: str) -> dict[str, Any]:
        self.store.state["active"] = conv_id
        self.store.save()
        page = self._history_page(conv_id, None, HISTORY_PAGE)
        return {
            "history": page["items"],  # só as últimas HISTORY_PAGE entradas
            "history_start": page["start"],  # índice da 1ª entrada enviada (0 = já é o começo)
            "history_total": page["total"],
            "shell_ctx": self.get_shell_ctx(conv_id),
            "busy": self.hub.is_busy(conv_id),
            "live_parts": self.hub.live_parts(conv_id),
            "permissions": self.hub.pending_permissions(conv_id),
            "compacting": self.hub.is_compacting(conv_id),
        }

    def _history_page(self, conv_id: str, before: int | None, limit: int) -> dict[str, Any]:
        paged = getattr(self.store, "history_page", None)
        if paged:
            return paged(conv_id, before, limit)
        items = self.store.history(conv_id)  # store antigo, sem paginação
        end = len(items) if before is None else max(0, min(int(before), len(items)))
        start = max(0, end - limit)
        return {"items": items[start:end], "start": start, "total": len(items)}

    def load_history(self, conv_id: str, before: int | None = None, limit: int = HISTORY_PAGE) -> dict[str, Any]:
        """"Carregar anteriores": as `limit` entradas antes do índice `before` (= history_start
        da última resposta). Devolve {items, start, total}; start == 0 quando acabou."""
        try:
            return {"ok": True, **self._history_page(conv_id, before, max(1, min(int(limit), 200)))}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error), "items": [], "start": 0, "total": 0}

    # --- conversas ------------------------------------------------------------
    def recent_folders(self) -> list[str]:
        folders = self.store.recent_folders()
        if config.HOME not in folders:
            folders.append(config.HOME)
        return folders

    def pick_folder(self, initial: str = "") -> str | None:
        assert self._window
        start = initial if initial and os.path.isdir(initial) else config.HOME
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return result[0] if result else None

    def new_conversation(self, path: str) -> dict[str, Any]:
        path = os.path.expandvars(os.path.expanduser((path or "").strip().strip('"'))) or config.HOME
        if not os.path.isdir(path):
            return {"ok": False, "error": f"Pasta não encontrada: {path}"}
        return {"ok": True, "conversation": self.store.create(path)}

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        title = " ".join((title or "").split())[:80]
        if not title:
            return False
        self.store.update(conv_id, title=title)
        return True

    def delete_conversation(self, conv_id: str) -> dict[str, Any]:
        self.hub.forget(conv_id)
        self.store.delete(conv_id)
        return self.get_state()

    def clear_conversation(self, conv_id: str) -> dict[str, Any]:
        """/clear: mesma conversa (pasta, modo, modelo), mas com sessão nova do Claude
        (contexto zerado). A tela continua com tudo: só ganha uma linha marcando o ponto."""
        if not self.store.get(conv_id):
            return {"ok": False, "error": "Conversa não encontrada"}
        try:
            self.hub.forget(conv_id)
        except Exception:  # noqa: BLE001
            pass
        self.store.update(conv_id, session_id=None, ctx_floor=None, shell_ctx=[])
        entry = {
            "role": "divider",
            "kind": "clear",
            "text": "Contexto limpo (/clear): o Claude não lembra do que está acima",
            "time": now_iso(),
        }
        self.store.append_history(conv_id, entry)
        return {"ok": True, "conversation": self.store.get(conv_id)}

    def set_permission_mode(self, conv_id: str, mode: str) -> bool:
        if mode not in PERMISSION_MODES:
            return False
        self.hub.set_permission_mode(conv_id, mode)
        return True

    # --- "!comando": roda direto no PC, como o modo bash do terminal -------------
    def shell_warm(self) -> bool:
        """Abre o completador em segundo plano assim que o usuário digita "!"."""
        threading.Thread(target=self.shell.warm, daemon=True).start()
        return True

    def shell_complete(self, conv_id: str, text: str, col: int) -> dict[str, Any]:
        """Sugestões do PowerShell (TabExpansion2) + histórico para o "!comando"."""
        conv = self.store.get(conv_id)
        if not conv:
            return {"start": 0, "length": 0, "items": [], "history": []}
        mine = self._shell_history(conv_id)
        try:
            return self.shell.complete(conv.get("path") or config.HOME, text or "", int(col), mine)
        except Exception as error:  # noqa: BLE001
            return {"start": 0, "length": 0, "items": [], "history": [], "error": str(error)}

    def _shell_history(self, conv_id: str) -> list[str]:
        """Comandos "!" da conversa: lidos do histórico uma vez e mantidos em memória."""
        cached = self._shell_hist.get(conv_id)
        if cached is None:
            cached = [h["command"] for h in self.store.history(conv_id) if h.get("role") == "shell" and h.get("command")]
            self._shell_hist[conv_id] = cached
        return cached

    # contexto dos "!comandos" que vai junto da próxima mensagem (uma lista por conversa)
    def _fresh_shell_ctx(self, conv_id: str) -> list[dict[str, Any]]:
        conv = self.store.get(conv_id) or {}
        items = list(conv.get("shell_ctx") or [])
        now = time.time()
        fresh = [i for i in items if now - float(i.get("ts") or 0) <= SHELL_CTX_TTL]  # sem ts = antigo: descarta
        if len(fresh) != len(items):
            self.store.update(conv_id, shell_ctx=fresh)
        return fresh

    def get_shell_ctx(self, conv_id: str) -> dict[str, Any]:
        """Chip "saída do shell: N comandos, ~X tokens": {count, tokens_est}."""
        items = self._fresh_shell_ctx(conv_id)
        chars = sum(len(i.get("command", "")) + len(i.get("output", "")) + 60 for i in items)
        return {"count": len(items), "tokens_est": chars // 4}

    def clear_shell_ctx(self, conv_id: str) -> dict[str, Any]:
        """O ✕ do chip: descarta o contexto sem mandar ao Claude."""
        if self.store.get(conv_id):
            self.store.update(conv_id, shell_ctx=[])
        return self.get_shell_ctx(conv_id)

    def stop_shell(self, conv_id: str) -> bool:
        """Botão parar do "!comando": mata o processo e todos os filhos."""
        proc = self._shells.get(conv_id)
        if not proc or proc.poll() is not None:
            return False
        self._shell_stopped.add(conv_id)
        _kill_tree(proc)
        return True

    def run_shell(self, conv_id: str, command: str) -> dict[str, Any]:
        """Roda o comando na pasta da conversa (PowerShell no Windows, bash fora dele),
        sem passar pelo Claude e sem gastar tokens. A saída aparece na conversa e vai
        como contexto junto da próxima mensagem (início + fim, descartada após 30 min)."""
        conv = self.store.get(conv_id)
        command = (command or "").strip()
        if not conv or not command:
            return {"ok": False, "error": "Comando vazio."}
        if conv_id in self._shells and self._shells[conv_id].poll() is None:
            return {"ok": False, "error": "Já há um comando rodando nesta conversa."}
        cwd = conv.get("path") or config.HOME
        if sys.platform == "win32":
            script = (
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                "$OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'\n" + command
            )
            encoded = base64.b64encode(script.encode("utf-16-le")).decode()
            args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", encoded]
        else:
            args = ["bash", "-lc", command]
        head = bytearray()
        tail = bytearray()
        total = [0]
        self._shell_stopped.discard(conv_id)

        def pump(stream: Any) -> None:
            """Lê a saída em blocos guardando só o início e o fim (a RAM não cresce)."""
            try:
                while True:
                    chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                    if not chunk:
                        return
                    total[0] += len(chunk)
                    if len(head) < SHELL_HEAD_BYTES:
                        take = SHELL_HEAD_BYTES - len(head)
                        head.extend(chunk[:take])
                        chunk = chunk[take:]
                    if chunk:
                        tail.extend(chunk)
                        if len(tail) > SHELL_TAIL_BYTES:
                            del tail[: len(tail) - SHELL_TAIL_BYTES]
            except (OSError, ValueError):
                return

        note = ""
        try:
            proc = subprocess.Popen(
                args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW, start_new_session=sys.platform != "win32",
            )
            self._shells[conv_id] = proc
            reader = threading.Thread(target=pump, args=(proc.stdout,), daemon=True)
            reader.start()
            try:
                code = proc.wait(timeout=SHELL_TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.wait(timeout=10)
                code = -1
                note = f"(parado: passou de {SHELL_TIMEOUT // 60} min)"
            if conv_id in self._shell_stopped:
                code = -1
                note = "(parado pelo usuário)"
            reader.join(timeout=3)  # um neto órfão pode segurar o pipe: não espera para sempre
            if reader.is_alive():
                _kill_tree(proc)
                reader.join(timeout=2)
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        except Exception as error:  # noqa: BLE001
            code, note = -1, f"Não consegui rodar: {error}"
        finally:
            self._shells.pop(conv_id, None)
            self._shell_stopped.discard(conv_id)

        omitted = max(0, total[0] - len(head) - len(tail))
        text = bytes(head).decode("utf-8", errors="replace") + (
            f"\n… ({omitted} bytes omitidos) …\n" if omitted else ""
        ) + bytes(tail).decode("utf-8", errors="replace")
        output = text.replace("\r\n", "\n").rstrip()
        if sys.platform == "win32":
            output = _clean_powershell(output).rstrip()
        if note:
            output = (output + "\n" + note).strip()
        entry = {"role": "shell", "command": command, "output": output, "code": code, "time": now_iso()}
        self.store.append_history(conv_id, entry)
        self._shell_history(conv_id).append(command)
        pending = self._fresh_shell_ctx(conv_id)
        pending.append({
            "command": command, "output": _head_tail(output, SHELL_CTX_HEAD, SHELL_CTX_TAIL),
            "code": code, "ts": time.time(),
        })
        self.store.update(conv_id, shell_ctx=pending[-SHELL_CTX_KEEP:], updated=now_iso())
        return {"ok": True, "entry": entry, "shell_ctx": self.get_shell_ctx(conv_id)}

    # --- mensagens ------------------------------------------------------------
    def send(self, conv_id: str, text: str, attachments: list[dict[str, Any]]) -> dict[str, Any]:
        """attachments: [{name, data (data URL ou base64)}].
        Se o Claude estiver respondendo, a mensagem entra na fila (status 'queued')."""
        blocks: list[dict[str, Any]] = []
        saved: list[dict[str, Any]] = []
        budget = convert.new_budget()  # limite total de texto de anexos por mensagem
        prefs = self.prefs()
        for att in attachments or []:
            name = att.get("name") or "anexo"
            data = att.get("data") or ""
            if "," in data[:200]:
                data = data.split(",", 1)[1]
            content = base64.b64decode(data)
            att_id = self.store.save_attachment(name, content)
            path = str(self.store.attachment_path(att_id))
            att_blocks = convert.to_blocks(name, content, path, prefs, budget)
            blocks.extend(att_blocks)
            saved.append({
                "id": att_id, "name": name, "kind": convert.kind_of(name), "size": len(content),
                "tokens": convert.estimate_tokens(att_blocks),  # o que realmente foi enviado (~N tokens)
            })
        text = (text or "").strip()
        if text:
            blocks.append({"type": "text", "text": text})
        if not blocks:
            return {"ok": False, "error": "Mensagem vazia."}
        shell_items = self._fresh_shell_ctx(conv_id)
        if shell_items and not (len(blocks) == 1 and text.startswith("/")):
            # comandos "!" rodados há menos de 30 min vão junto, como no terminal
            blocks.insert(0, {"type": "text", "text": _shell_context(shell_items)})
            self.store.update(conv_id, shell_ctx=[])
        entry = {"role": "user", "text": text, "attachments": saved, "time": now_iso()}
        try:
            status = self.hub.send(conv_id, blocks, entry)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": f"Não consegui enviar: {error}"}
        return {
            "ok": True, "status": status, "entry": entry, "conversation": self.store.get(conv_id),
            "shell_ctx": self.get_shell_ctx(conv_id),
        }

    def estimate_attachment(self, name: str, data: str) -> dict[str, Any]:
        """"~N tokens" do chip do anexo, antes de enviar: {ok, kind, tokens (None = não dá para
        estimar: áudio/vídeo), big (será enviado só em prévia + caminho do arquivo)}."""
        try:
            if "," in data[:200]:
                data = data.split(",", 1)[1]
            content = base64.b64decode(data)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}
        return {"ok": True, "kind": convert.kind_of(name), **convert.estimate(name, content)}
    def set_model(self, conv_id: str, model: str) -> dict[str, Any]:
        """/model: 'opus', 'sonnet', 'haiku', 'fable', um id completo, ou 'default'."""
        model = (model or "").strip()
        value = None if model.lower() in ("", "default", "padrao", "padrão") else model
        try:
            self.hub.set_model(conv_id, value)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}
        return {"ok": True, "model": value}

    def list_commands(self, conv_id: str) -> dict[str, Any]:
        """/comandos e skills disponíveis na pasta da conversa (abre a conexão se preciso)."""
        try:
            return {"ok": True, "commands": self.hub.list_commands(conv_id)}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error), "commands": []}

    def stop(self, conv_id: str) -> None:
        self.hub.interrupt(conv_id)

    def answer_permission(self, conv_id: str, request_id: str, answer: str, message: str = "") -> None:
        """answer: allow | deny | always (cards comuns); no card do plano (ExitPlanMode):
        accept_edits | default | deny (+ `message` com o que mudar no plano)."""
        self.hub.answer_permission(conv_id, request_id, answer, message or "")

    def get_attachment(self, att_id: str) -> str | None:
        return self.store.attachment_data_url(att_id)

    def open_attachment(self, att_id: str) -> None:
        _open_path(str(self.store.attachment_path(att_id)))

    # --- arquivos da pasta da conversa ---------------------------------------
    def _path(self, conv_id: str) -> Path | None:
        conv = self.store.get(conv_id)
        return Path(conv["path"]) if conv else None

    # --- explorador de arquivos (painel ao lado do chat) -----------------------
    def _inside(self, conv_id: str, rel_path: str) -> Path | None:
        """Caminho dentro da pasta da conversa (nunca fora dela)."""
        root = self._path(conv_id)
        if not root:
            return None
        root = root.resolve()
        target = (root / (rel_path or ".")).resolve()
        return target if target == root or root in target.parents else None

    def list_dir(self, conv_id: str, rel_path: str = "") -> dict[str, Any]:
        """Uma pasta por vez (a árvore abre sob demanda, como no VS Code)."""
        target = self._inside(conv_id, rel_path)
        if not target or not target.is_dir():
            return {"ok": False, "error": "Pasta não encontrada."}
        entries: list[dict[str, Any]] = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if entry.name in FILES_HIDDEN:
                        continue
                    try:
                        is_dir = entry.is_dir()
                        size = 0 if is_dir else entry.stat().st_size
                    except OSError:
                        continue
                    entries.append({"name": entry.name, "dir": is_dir, "size": size})
        except OSError as error:
            return {"ok": False, "error": str(error)}
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return {"ok": True, "entries": entries[:FILES_MAX_ENTRIES], "more": max(0, len(entries) - FILES_MAX_ENTRIES)}

    @staticmethod
    def _downscale_image(target: Path, ext: str) -> tuple[bytes, str] | None:
        """Imagem grande demais para a tela: reduz para FILES_IMAGE_EDGE. None = manda como está."""
        if ext in (".svg", ".gif", ".ico"):  # vetor/animada: não mexe
            return None
        import io

        from PIL import Image

        with Image.open(target) as img:
            if max(img.size) <= FILES_IMAGE_EDGE:
                return None
            img.load()
            img.thumbnail((FILES_IMAGE_EDGE, FILES_IMAGE_EDGE))
            buffer = io.BytesIO()
            if ext in (".jpg", ".jpeg"):
                img.convert("RGB").save(buffer, format="JPEG", quality=88)
                return buffer.getvalue(), "image/jpeg"
            if ext == ".webp":
                img.save(buffer, format="WEBP", quality=88)
                return buffer.getvalue(), "image/webp"
            img.save(buffer, format="PNG")
            return buffer.getvalue(), "image/png"

    def read_file(
        self, conv_id: str, rel_path: str, known_mtime: int | None = None, known_size: int | None = None
    ) -> dict[str, Any]:
        """Conteúdo para o visualizador: kind = md | text | image | doc (convertido) | binary.
        Passe `known_mtime`/`known_size` (os `mtime`/`size` da leitura anterior) e, se o arquivo
        não mudou, volta só {ok, unchanged: true} (sem reler nem reconverter).
        `big`: texto grande (> 50 mil caracteres): a tela não deve fazer highlight."""
        target = self._inside(conv_id, rel_path)
        if not target or not target.is_file():
            return {"ok": False, "error": "Arquivo não encontrado."}
        name = target.name
        ext = target.suffix.lower()
        stat = target.stat()
        size = stat.st_size
        mtime = int(stat.st_mtime * 1000)
        if known_mtime is not None and known_size is not None and int(known_mtime) == mtime and int(known_size) == size:
            return {"ok": True, "unchanged": True, "path": rel_path, "mtime": mtime, "size": size}
        info = {"ok": True, "name": name, "path": rel_path, "size": size, "mtime": mtime, "ext": ext.lstrip(".")}
        try:
            if ext in FILES_IMAGE_MIME:
                if size > FILES_IMAGE_MAX:
                    return {**info, "kind": "binary", "note": "Imagem grande demais para mostrar aqui."}
                mime = FILES_IMAGE_MIME[ext]
                raw = target.read_bytes()
                try:
                    small = self._downscale_image(target, ext) if size > 200_000 else None
                except Exception:  # noqa: BLE001 - imagem estranha: manda a original
                    small = None
                if small:
                    raw, mime = small
                data = base64.b64encode(raw).decode()
                return {**info, "kind": "image", "data": f"data:{mime};base64,{data}", "downscaled": bool(small)}
            if ext == ".pdf" or ext in convert.MARKITDOWN_EXT - {".csv", ".html", ".htm", ".xml"}:
                if size > FILES_DOC_MAX:
                    return {**info, "kind": "binary", "note": "Arquivo grande demais para converter aqui."}
                content = convert.to_markdown(str(target))
                return {**info, "kind": "doc", "content": content, "big": len(content) > FILES_BIG_TEXT}
            with target.open("rb") as fh:
                head = fh.read(FILES_TEXT_MAX + 1)
            if convert.looks_binary(head):
                return {**info, "kind": "binary"}
            text = head[:FILES_TEXT_MAX].decode("utf-8", errors="replace")
            cut = size > FILES_TEXT_MAX
            return {
                **info, "kind": "md" if ext in (".md", ".markdown") else "text", "content": text,
                "cut": cut, "big": len(text) > FILES_BIG_TEXT,
            }
        except Exception as error:  # noqa: BLE001
            return {**info, "kind": "binary", "note": f"Não consegui ler: {error}"}

    def open_file(self, conv_id: str, rel_path: str, editor: str = "") -> bool:
        """Abre no programa padrão do sistema ou, com editor="code", no VS Code.
        Executáveis/scripts (.bat, .exe, .ps1, .lnk...) nunca são executados: aparecem na pasta."""
        target = self._inside(conv_id, rel_path)
        if not target or not target.exists():
            return False
        code = shutil.which("code") if editor == "code" else None
        if code:
            # o VS Code só edita: seguro mesmo para scripts
            subprocess.Popen([code, str(target)], creationflags=NO_WINDOW)
        else:
            _open_path(str(target))
        return True

    def get_abs_path(self, conv_id: str, rel_path: str = "") -> str | None:
        """Caminho completo no formato do sistema (para "Copiar caminho"); None se fora da pasta."""
        target = self._inside(conv_id, rel_path)
        return str(target) if target else None

    def reveal_path(self, conv_id: str, rel_path: str = "") -> bool:
        """"Mostrar no Explorer/Finder/gerenciador de arquivos" (só dentro da pasta da conversa)."""
        target = self._inside(conv_id, rel_path)
        if not target or not target.exists():
            return False
        _reveal(str(target))
        return True

    def copy_text(self, text: str) -> bool:
        """Copia para a área de transferência (alternativa quando navigator.clipboard falha)."""
        text = text or ""
        try:
            if sys.platform == "win32":
                return self._copy_windows(text)
            if sys.platform == "darwin":
                return _pipe_to(["pbcopy"], text)
            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                if shutil.which(cmd[0]) and _pipe_to(cmd, text):
                    return True
        except Exception as error:  # noqa: BLE001
            print(f"[copy] {error}")
        return False

    def _copy_windows(self, text: str) -> bool:
        if self._window is None:
            return False
        from System import Action  # type: ignore
        from System.Windows.Forms import Clipboard, TextDataFormat  # type: ignore

        done = threading.Event()
        result = {"ok": False}

        def work() -> None:
            # Clipboard exige a thread da janela (STA): tudo dentro do BeginInvoke
            try:
                if text:
                    Clipboard.SetText(text, TextDataFormat.UnicodeText)
                else:
                    Clipboard.Clear()
                result["ok"] = True
            except Exception as error:  # noqa: BLE001
                print(f"[copy] {error}")
            finally:
                done.set()

        self._window.native.BeginInvoke(Action(work))
        done.wait(3)
        return result["ok"]

    def has_vscode(self) -> bool:
        return shutil.which("code") is not None

    def convert_file(self, conv_id: str) -> dict[str, Any] | None:
        assert self._window
        root = self._path(conv_id)
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(root) if root else config.HOME,
            file_types=("Documentos (*.pdf;*.docx;*.xlsx;*.pptx;*.csv;*.html)", "Todos (*.*)"),
        )
        if not result:
            return None
        source = result[0]
        try:
            markdown = convert.to_markdown(source)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}
        out = source + ".md"
        Path(out).write_text(markdown, encoding="utf-8")
        rel = ""
        if root:
            try:
                rel = str(Path(out).resolve().relative_to(root.resolve()))
            except ValueError:
                pass
        return {"ok": True, "name": Path(out).name, "path": out, "rel": rel, "content": markdown}

    def open_folder(self, conv_id: str) -> None:
        root = self._path(conv_id)
        if root:
            _open_path(str(root))

    TERMINAL_COMMAND = re.compile(r"/[\w:-]+")
    SESSION_ID = re.compile(r"[\w-]+")

    def open_terminal(self, conv_id: str, command: str = "") -> bool:
        """Abre o Claude Code no terminal na pasta da conversa, continuando a mesma sessão.
        `command` (ex.: "/config") vai como primeiro prompt: serve para os comandos de tela
        que só existem no terminal, inclusive os que o Claude Code lançar no futuro.
        Só aceita `/nome` (letras, números, _, - e :): o resto é recusado (False), pois o
        wt trata ";" como separador e o claude.cmd deixa "&" e "|" passarem."""
        conv = self.store.get(conv_id)
        if not conv:
            return False
        command = (command or "").strip()
        if command and not self.TERMINAL_COMMAND.fullmatch(command):
            return False
        claude = config.find_claude() or "claude"
        args = [claude]
        session = conv.get("session_id")
        if session:
            if not self.SESSION_ID.fullmatch(str(session)):
                return False
            args += ["--resume", str(session)]
        if command:
            args.append(command)
        path = conv["path"]
        if sys.platform == "win32":
            if shutil.which("wt"):
                # no wt, ";" separa comandos: no caminho vai escapado (\;)
                subprocess.Popen(["wt", "-d", path.replace(";", "\\;"), *args])
            else:
                subprocess.Popen(args, cwd=path, creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif sys.platform == "darwin":
            line = f"cd {shlex.quote(path)} && {shlex.join(args)}"
            line = line.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "{line}"'])
        else:
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term, "-e", *args], cwd=path)
                    break
        return True

    # --- saúde ------------------------------------------------------------------
    def check_health(self) -> dict[str, Any]:
        health: dict[str, Any] = {"installed": False, "version": None, "logged_in": False, "email": None, "plan": None}
        claude = config.find_claude()
        if not claude:
            return health
        try:
            v = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
            health["installed"] = v.returncode == 0
            health["version"] = v.stdout.strip()
            s = subprocess.run(
                [claude, "auth", "status"], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW
            )
            # deslogado, o comando pode sair com código != 0 mas ainda imprimir o JSON
            out = s.stdout.strip()
            if out.startswith("{"):
                status = json.loads(out)
                health["logged_in"] = bool(status.get("loggedIn"))
                health["email"] = status.get("email")
                health["plan"] = status.get("subscriptionType") or status.get("planTier")
                health["method"] = status.get("authMethod")
                health["org"] = status.get("orgName")
                health["expires"] = status.get("loginExpiresAt")
        except Exception as error:  # noqa: BLE001
            health["error"] = str(error)
        return health

    def start_login(self) -> bool:
        """Abre `claude auth login` num terminal visível (o login é interativo e abre o
        navegador). A tela de configurações consulta o status até o login concluir."""
        claude = config.find_claude()
        if not claude:
            return False
        if sys.platform == "win32":
            subprocess.Popen([claude, "auth", "login"], creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "\\"{claude}\\" auth login"'])
        else:
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term, "-e", claude, "auth", "login"])
                    break
        # as sessões abertas usam a credencial antiga: reconectam na próxima mensagem
        self.hub.disconnect_all()
        return True

    def conversation_usage(self, conv_id: str) -> dict[str, Any]:
        """Tokens e contexto da conversa (lidos do arquivo da sessão do Claude Code)."""
        conv = self.store.get(conv_id)
        try:
            result = usage.conversation_usage(conv.get("session_id") if conv else None)
            result["floor"] = self.hub.context_floor(conv_id)
            return result
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}

    def get_usage(self) -> dict[str, Any]:
        """Limites do plano (5 h / semana) e tokens com custo estimado (ver usage.py)."""
        return usage.usage_report()

    def logout(self) -> dict[str, Any]:
        claude = config.find_claude()
        if claude:
            self.hub.disconnect_all()
            try:
                subprocess.run(
                    [claude, "auth", "logout"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW
                )
            except Exception as error:  # noqa: BLE001
                return {**self.check_health(), "error": str(error)}
        return self.check_health()

    def open_url(self, url: str) -> None:
        if url.startswith(("https://", "http://")):
            import webbrowser

            webbrowser.open(url)


def _pipe_to(cmd: list[str], text: str) -> bool:
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), timeout=5, check=True, creationflags=NO_WINDOW)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _reveal(path: str) -> None:
    """Mostra o arquivo/pasta no gerenciador de arquivos (selecionado, quando dá)."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"], creationflags=NO_WINDOW)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        uri = Path(path).resolve().as_uri()
        try:
            done = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                 "--object-path", "/org/freedesktop/FileManager1",
                 "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
                capture_output=True, timeout=5,
            )
            if done.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
        subprocess.Popen(["xdg-open", path if os.path.isdir(path) else str(Path(path).parent)])


def _open_path(path: str) -> None:
    """Abre no programa padrão. Arquivos que o sistema executaria (.bat, .exe, .ps1, .lnk...)
    nunca são abertos: aparecem selecionados na pasta."""
    if _is_dangerous(path):
        _reveal(path)
    elif sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _allow_microphone(window: webview.Window) -> None:
    """WebView2: libera o microfone para a página do app (botão de falar). Sem isto, o
    WebView2 pergunta toda vez numa janelinha própria. O pywebview não trata isso."""
    if sys.platform != "win32" or getattr(window, "_rory_mic_hooked", False):
        return
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState  # type: ignore
        from System import Action  # type: ignore

        form = window.native

        def on_permission(sender: Any, args: Any) -> None:
            if args.PermissionKind == CoreWebView2PermissionKind.Microphone:
                args.State = CoreWebView2PermissionState.Allow

        def hook() -> None:
            # TUDO que toca o WebView2 roda aqui, na thread da janela. Ler
            # `CoreWebView2` de outra thread faz uma chamada COM que espera a janela
            # segurando o GIL, enquanto a janela espera o GIL: o app travava ao abrir.
            form.browser.webview.CoreWebView2.PermissionRequested += on_permission

        window._rory_mic_hooked = True  # type: ignore[attr-defined]
        form.BeginInvoke(Action(hook))
    except Exception as error:  # noqa: BLE001
        print(f"[mic] não consegui liberar o microfone automaticamente: {error}")


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _already_running() -> bool:
    """Instância única por pasta de dados. Se já houver uma aberta, traz a janela dela
    para a frente e devolve True (duas instâncias brigariam pelos mesmos arquivos)."""
    if sys.platform != "win32":
        # macOS/Linux: trava de arquivo na pasta de dados (não traz a janela da outra para a frente)
        try:
            import fcntl

            handle = open(config.DATA_DIR / "instance.lock", "w")  # noqa: SIM115
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _already_running.lock = handle  # type: ignore[attr-defined]
            return False
        except BlockingIOError:
            return True
        except (ImportError, OSError):
            return False
    import ctypes
    import hashlib

    tag = hashlib.md5(str(config.DATA_DIR).lower().encode()).hexdigest()[:10]
    kernel32 = ctypes.windll.kernel32
    _already_running.mutex = kernel32.CreateMutexW(None, False, f"Local\\RoryAssistant-{tag}")  # type: ignore[attr-defined]
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, config.WINDOW_TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return True


def _enable_crash_log() -> None:
    """pythonw não tem console: queda grava a pilha em crash.log. Se a janela travar,
    rode `py-spy dump --pid <pid>` (py-spy está no .venv) para ver onde parou."""
    import faulthandler

    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.DATA_DIR / "crash.log", "a", encoding="utf-8")  # noqa: SIM115
        log.write(f"\n=== início {now_iso()} pid {os.getpid()} ===\n")
        log.flush()
        faulthandler.enable(log, all_threads=True)
        _enable_crash_log.file = log  # type: ignore[attr-defined]
    except OSError:
        pass


def main() -> None:
    if _already_running():
        return
    _enable_crash_log()
    if sys.platform == "win32":
        # ícone e agrupamento próprios na barra de tarefas (senão aparece como "Python")
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"RorySystems.{config.APP_ID}")
    api = Api()
    window = webview.create_window(
        config.WINDOW_TITLE,
        url=str(config.UI_DIR / "index.html"),
        js_api=api,
        width=520,
        height=860,
        min_size=(380, 500),
        text_select=True,
        background_color="#0a0c10",
    )
    api._attach(window)
    window.events.closed += api.hub.shutdown
    window.events.closed += api.shell.close
    window.events.loaded += lambda: _allow_microphone(window)
    icon = config.ICON_ICO if sys.platform == "win32" else config.ICON_PNG
    webview.start(
        debug=bool(os.environ.get("RORY_DEBUG")),
        private_mode=False,
        storage_path=str(config.DATA_DIR / "webview"),
        # porta própria: a padrão (42001) é fixa e outra instância/app pywebview
        # poderia servir uma interface velha. O estado não depende do localStorage.
        http_port=_free_port(),
        icon=str(icon) if icon.exists() else None,
    )


if __name__ == "__main__":
    main()
