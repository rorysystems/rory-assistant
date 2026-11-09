"""Importa uma sessão do Claude Code (terminal) para o Rory Assistant.

A conversa importada **retoma a mesma sessão** (mesmo session_id), então o Claude no app
tem toda a memória do terminal. As mensagens também viram histórico de tela (texto do
usuário, respostas e um resumo das ferramentas usadas).

Uso:
  .venv\\Scripts\\python.exe scripts\\import_session.py <arquivo.jsonl | session_id> [--titulo "..."] [--pasta C:\\...]

O arquivo fica em ~/.claude/projects/<pasta-codificada>/<session_id>.jsonl.
Feche o app antes: ele guarda o estado em memória e sobrescreveria o state.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import tool_summary  # noqa: E402
from app.store import Store, now_iso  # noqa: E402

PASTED = re.compile(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", re.S)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
SKIP_PREFIXES = ("<command-name>", "<command-message>", "<local-command", "[Request interrupted")
QUEUED = "The user sent a new message while you were working:"


def find_transcript(arg: str) -> Path:
    path = Path(arg)
    if path.suffix == ".jsonl" and path.exists():
        return path
    hits = list((Path.home() / ".claude" / "projects").glob(f"*/{arg}.jsonl"))
    if not hits:
        sys.exit(f"Sessão não encontrada: {arg}")
    return hits[0]


def user_text(content: Any) -> str | None:
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return None
    text = REMINDER.sub("", content).strip()
    if not text or text.startswith(SKIP_PREFIXES):
        return None
    return text


def queued_text(text: str) -> str | None:
    """Mensagens que o usuário mandou enquanto o Claude trabalhava."""
    if QUEUED not in text:
        return None
    body = text.split(QUEUED, 1)[1]
    body = body.split("This is how Claude Code surfaces", 1)[0]
    return body.strip() or None


def local_time(stamp: str | None) -> str:
    """Horário do transcript (UTC, 'Z') para o horário local, como o app grava."""
    from datetime import datetime

    if not stamp:
        return now_iso()
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat(
            timespec="seconds"
        )
    except ValueError:
        return stamp[:19]


def tool_result_user_words(block: dict[str, Any]) -> str | None:
    """Palavras do usuário que chegam dentro de um resultado de ferramenta: resposta a uma
    pergunta (AskUserQuestion) ou o ajuste pedido ao recusar uma ação/plano."""
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    if not isinstance(content, str):
        return None
    if "the user said:" in content:
        return content.split("the user said:", 1)[1].strip() or None
    if content.startswith("The user answered:") or "User has answered your questions:" in content:
        answers = re.findall(r'"([^"]+)"="([^"]*)"', content)
        if answers:
            return "\n".join(f"**{q}** → {a}" for q, a in answers)
    return None


def convert(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    current: dict[str, Any] | None = None  # resposta do assistente em montagem
    tools: dict[str, dict[str, Any]] = {}

    def flush() -> None:
        nonlocal current
        if current and current["parts"]:
            history.append(current)
        current = None

    def add_user(text: str, time: str) -> None:
        flush()
        history.append({"role": "user", "text": text, "attachments": [], "time": time})

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        kind = d.get("type")
        if kind == "ai-title":
            meta["title"] = d.get("aiTitle")
        if kind == "attachment" and not d.get("isSidechain"):
            att = d.get("attachment") or {}
            if att.get("type") == "queued_command" and isinstance(att.get("prompt"), str) and att["prompt"].strip():
                add_user(PASTED.sub("[texto colado]", att["prompt"].strip()), local_time(d.get("timestamp")))
            continue
        if kind not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
            continue
        meta.setdefault("cwd", d.get("cwd"))
        meta.setdefault("session_id", d.get("sessionId"))
        time = local_time(d.get("timestamp"))
        msg = d.get("message") or {}
        content = msg.get("content")

        if kind == "user":
            # resultado de ferramenta: marca erro na ferramenta correspondente
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        if b.get("tool_use_id") in tools:
                            tools[b["tool_use_id"]]["error"] = bool(b.get("is_error"))
                        words = tool_result_user_words(b)
                        if words:
                            add_user(words, time)
                    if isinstance(b, dict) and b.get("type") == "text":
                        q = queued_text(b.get("text", ""))
                        if q:
                            add_user(PASTED.sub("[texto colado]", q), time)
            text = user_text(content)
            if text:
                q = queued_text(text)
                add_user(PASTED.sub("[texto colado]", q or text), time)
            continue

        # assistente
        if current is None:
            current = {"role": "assistant", "parts": [], "cost": None, "error": None, "time": time}
        current["time"] = time
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                parts = current["parts"]
                if parts and parts[-1]["type"] == "text":
                    parts[-1]["text"] += "\n\n" + b["text"]
                else:
                    parts.append({"type": "text", "text": b["text"]})
            elif b.get("type") == "tool_use":
                part = {
                    "type": "tool",
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "summary": tool_summary(b.get("name", ""), b.get("input") or {}),
                    "error": False,
                }
                tools[b.get("id")] = part
                current["parts"].append(part)
    flush()
    return history, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessao")
    ap.add_argument("--titulo")
    ap.add_argument("--pasta")
    args = ap.parse_args()

    path = find_transcript(args.sessao)
    history, meta = convert(path)
    if not meta.get("session_id"):
        sys.exit("Não achei o session_id no arquivo.")

    store = Store()
    existing = next((c for c in store.conversations() if c.get("session_id") == meta["session_id"]), None)
    if existing:
        sys.exit(f"Já importada: \"{existing['title']}\" ({existing['id']})")

    conv = store.create(args.pasta or meta.get("cwd") or str(Path.home()))
    from app import config

    (config.HISTORY_DIR / f"{conv['id']}.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.update(
        conv["id"],
        title=args.titulo or meta.get("title") or "Conversa importada do terminal",
        session_id=meta["session_id"],
        updated=history[-1]["time"] if history else now_iso(),
    )
    users = sum(1 for h in history if h["role"] == "user")
    print(f"Importada: {users} mensagens suas, {len(history) - users} respostas -> conversa {conv['id']}")


if __name__ == "__main__":
    main()
