"""Exportar conversa: o histórico de exibição vira Markdown (o HTML é gerado na tela a
partir dele, com o mesmo renderizador do chat)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

SHELL_OUT_MAX = 4000
TOOL_NAMES_HIDDEN = {"TodoWrite"}


def _when(iso: Any) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def _fence(text: str) -> str:
    """Cerca de código que não colide com crases dentro do texto."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def safe_filename(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title or "conversa").strip(" .")
    return (re.sub(r"\s+", " ", name) or "conversa")[:80]


def to_markdown(conv: dict[str, Any], history: list[dict[str, Any]], tools: bool = True) -> str:
    title = conv.get("title") or "Conversa"
    out = [f"# {title}", "", f"Pasta: `{conv.get('path', '')}` · exportado em {datetime.now().strftime('%d/%m/%Y %H:%M')}", ""]
    for entry in history:
        role = entry.get("role")
        when = _when(entry.get("time"))
        stamp = f" · {when}" if when else ""
        if role == "user":
            out += ["---", "", f"### Você{stamp}", ""]
            if entry.get("text"):
                out += [entry["text"], ""]
            names = [a.get("name", "") for a in entry.get("attachments") or []]
            if names:
                out += ["Anexos: " + ", ".join(f"`{n}`" for n in names), ""]
        elif role == "assistant":
            out += [f"### Claude{stamp}", ""]
            for part in entry.get("parts") or []:
                if part.get("type") == "text" and (part.get("text") or "").strip():
                    out += [part["text"].strip(), ""]
                elif tools and part.get("type") == "tool" and part.get("name") not in TOOL_NAMES_HIDDEN:
                    summary = (part.get("summary") or "").replace("`", "'")
                    mark = " (erro)" if part.get("error") else ""
                    out += [f"> 🔧 **{part.get('name', '')}** `{summary}`{mark}" if summary else f"> 🔧 **{part.get('name', '')}**{mark}", ""]
            if entry.get("error"):
                out += [f"> ⚠ {entry['error']}", ""]
        elif role == "shell":
            output = str(entry.get("output") or "")
            if len(output) > SHELL_OUT_MAX:
                output = output[:SHELL_OUT_MAX] + "\n[…saída cortada…]"
            body = f"> {entry.get('command', '')}\n{output}".rstrip()
            fence = _fence(body)
            out += ["---", "", f"### Comando{stamp}", "", f"{fence}text", body, fence, ""]
        elif role == "divider":
            out += [f"*— {entry.get('text') or 'contexto compactado'} —*", ""]
    return "\n".join(out).rstrip() + "\n"
