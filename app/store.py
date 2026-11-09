"""Estado persistido: conversas, cada uma presa a uma pasta, e o histórico delas.

Tudo em %APPDATA%\\Rory Assistant (um lugar só, nada escrito nas pastas dos projetos):
- state.json          conversas (id, título, pasta, session_id do Claude, modo de
                      permissão, datas), conversa ativa, tema e preferências
                      (+ state.json.bak; arquivo ilegível vira state.json.corrupt-<ts>)
- history/<id>.jsonl  mensagens mostradas na tela, uma por linha (só append; texto,
                      ferramentas, anexos por id). O formato antigo <id>.json é migrado
                      sozinho na primeira leitura
- attachments/<id>.*  arquivos anexados (nunca base64 dentro do JSON)

O que o modelo "lembra" fica na sessão do Claude Code (~/.claude/projects); o histórico
daqui é para redesenhar a tela e listar as conversas antigas.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

_lock = threading.RLock()


def _write_json(path: Path, data: Any, backup: bool = False) -> None:
    """Grava com fsync antes do replace (queda de energia não deixa arquivo vazio). Com
    `backup`, mantém um .bak do conteúdo anterior (renovado no máximo a cada 60 s)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            if not bak.exists() or time.time() - bak.stat().st_mtime > 60:
                shutil.copy2(path, bak)
        except OSError:
            pass
    os.replace(tmp, path)


def _quarantine(path: Path) -> None:
    """Arquivo ilegível: guarda como .corrupt em vez de sobrescrever."""
    target = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
    try:
        os.replace(path, target)
    except OSError:
        pass


def _read_json(path: Path, default: Any, backup: bool = False) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(text)
    except ValueError:
        _quarantine(path)
        if backup:
            bak = path.with_suffix(path.suffix + ".bak")
            try:
                return json.loads(bak.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return default


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def norm_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def make_title(text: str, attachments: list[dict[str, Any]]) -> str:
    text = " ".join((text or "").split())
    if text:
        return text if len(text) <= 60 else text[:59].rstrip() + "…"
    if attachments:
        return "📎 " + attachments[0]["name"]
    return "Nova conversa"


class Store:
    def __init__(self) -> None:
        state = _read_json(config.STATE_FILE, {}, backup=True)
        self.state: dict[str, Any] = state if isinstance(state, dict) else {}
        self.state.setdefault("conversations", [])
        self.state.setdefault("theme", "dark")
        self.state.setdefault("active", None)
        self.state.pop("projects", None)  # formato antigo (0.1.0 de teste)
        # conversas criadas e nunca usadas não ficam acumulando (menos a aberta)
        self.state["conversations"] = [
            c
            for c in self.state["conversations"]
            if c["id"] == self.state["active"] or self._has_history(c["id"])
        ]
        self.save()

    def save(self) -> None:
        with _lock:
            _write_json(config.STATE_FILE, self.state, backup=True)

    # --- preferências / tema / conversa ativa (sempre sob o lock) -----------------
    def prefs(self) -> dict[str, Any]:
        with _lock:
            return dict(self.state.get("prefs") or {})

    def set_pref(self, key: str, value: Any) -> None:
        with _lock:
            prefs = self.state.setdefault("prefs", {})
            if key not in prefs or prefs[key] != value:
                prefs[key] = value
                self.save()

    def set_theme(self, theme: str) -> None:
        with _lock:
            theme = "light" if theme == "light" else "dark"
            if self.state.get("theme") != theme:
                self.state["theme"] = theme
                self.save()

    def set_active(self, conv_id: str | None) -> None:
        with _lock:
            if self.state.get("active") != conv_id:
                self.state["active"] = conv_id
                self.save()

    # --- conversas ------------------------------------------------------------
    def conversations(self) -> list[dict[str, Any]]:
        with _lock:
            return sorted(self.state["conversations"], key=lambda c: c.get("updated", ""), reverse=True)

    def get(self, conv_id: str) -> dict[str, Any] | None:
        return next((c for c in self.state["conversations"] if c["id"] == conv_id), None)

    def create(self, path: str) -> dict[str, Any]:
        with _lock:
            stamp = now_iso()
            conv = {
                "id": uuid.uuid4().hex[:12],
                "title": "Nova conversa",
                "path": str(Path(path)),
                "session_id": None,
                "permission_mode": self._last_mode_for(path),
                "created": stamp,
                "updated": stamp,
            }
            self.state["conversations"].append(conv)
            self.state["active"] = conv["id"]
            self.save()
            return conv

    def _last_mode_for(self, path: str) -> str:
        """Nova conversa na mesma pasta herda o modo de permissão da última."""
        key = norm_path(path)
        for c in self.conversations():
            if norm_path(c["path"]) == key:
                return c.get("permission_mode") or "default"
        return "default"

    def update(self, conv_id: str, **fields: Any) -> None:
        """Atualiza campos da conversa; não grava se nada mudou."""
        with _lock:
            conv = self.get(conv_id)
            if conv and any(k not in conv or conv[k] != v for k, v in fields.items()):
                conv.update(fields)
                self.save()

    def delete(self, conv_id: str) -> None:
        with _lock:
            self.state["conversations"] = [c for c in self.state["conversations"] if c["id"] != conv_id]
            if self.state["active"] == conv_id:
                rest = self.conversations()
                self.state["active"] = rest[0]["id"] if rest else None
            self.save()
            self._migrate_history(conv_id)
            path = self._history_file(conv_id)
            if path.exists():
                trash = config.HISTORY_DIR / "apagadas"
                trash.mkdir(exist_ok=True)
                os.replace(path, trash / path.name)

    def recent_folders(self, limit: int = 8) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for c in self.conversations():
            key = norm_path(c["path"])
            if key not in seen:
                seen.add(key)
                out.append(c["path"])
        return out[:limit]

    # --- histórico de exibição (JSONL: uma entrada por linha, só append) ---------
    def _history_file(self, conv_id: str) -> Path:
        return config.HISTORY_DIR / f"{conv_id}.jsonl"

    def _legacy_file(self, conv_id: str) -> Path:
        return config.HISTORY_DIR / f"{conv_id}.json"

    def _has_history(self, conv_id: str) -> bool:
        return self._history_file(conv_id).exists() or self._legacy_file(conv_id).exists()

    def _migrate_history(self, conv_id: str) -> None:
        legacy = self._legacy_file(conv_id)
        if not legacy.exists():
            return
        with _lock:
            target = self._history_file(conv_id)
            if target.exists():
                return
            items = _read_json(legacy, None)
            if not isinstance(items, list):
                return  # ilegível: _read_json já guardou como .corrupt
            tmp = target.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                for item in items:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            try:
                os.replace(legacy, legacy.with_suffix(".json.bak"))  # cleanup_old limpa depois
            except OSError:
                pass

    def _history_lines(self, conv_id: str) -> list[str]:
        self._migrate_history(conv_id)
        try:
            text = self._history_file(conv_id).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [line for line in text.split("\n") if line.strip()]

    @staticmethod
    def _parse_lines(lines: list[str]) -> list[dict[str, Any]]:
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # linha cortada por queda de energia: ignora
        return out

    def history(self, conv_id: str) -> list[dict[str, Any]]:
        return self._parse_lines(self._history_lines(conv_id))

    def history_page(self, conv_id: str, before: int | None = None, limit: int = 40) -> dict[str, Any]:
        """Uma página do histórico: as `limit` entradas anteriores ao índice `before`
        (None = as últimas). Devolve {items, start, total}; para a página anterior,
        chame de novo com before=start."""
        lines = self._history_lines(conv_id)
        total = len(lines)
        end = total if before is None else max(0, min(int(before), total))
        start = max(0, end - max(1, int(limit)))
        return {"items": self._parse_lines(lines[start:end]), "start": start, "total": total}

    def append_history(self, conv_id: str, entry: dict[str, Any]) -> None:
        with _lock:
            self._migrate_history(conv_id)
            path = self._history_file(conv_id)
            line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            with path.open("ab+") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell():
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":  # linha anterior cortada: não cola nela
                        handle.write(b"\n")
                handle.write(line)
            conv = self.get(conv_id)
            if conv:
                changed = False
                updated = entry.get("time") or now_iso()
                if conv.get("updated") != updated:
                    conv["updated"] = updated
                    changed = True
                if entry.get("role") == "user" and conv.get("title") in (None, "", "Nova conversa"):
                    conv["title"] = make_title(entry.get("text", ""), entry.get("attachments") or [])
                    changed = True
                if changed:
                    self.save()

    def cleanup_old(self, days: int = 30) -> int:
        """Apaga com mais de `days` dias: anexos que nenhuma conversa existente cita,
        históricos em history/apagadas/ e sobras de migração/corrupção em history/.
        Devolve quantos arquivos removeu."""
        limit = time.time() - days * 86400
        removed = 0
        in_use: set[str] = set()
        for conv in list(self.state["conversations"]):
            for line in self._history_lines(conv["id"]):
                in_use.update(re.findall(r'"id":\s*"([^"]+)"', line))
        for folder in (config.ATTACH_DIR, config.HISTORY_DIR / "apagadas", config.HISTORY_DIR):
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                try:
                    if not path.is_file() or path.stat().st_mtime >= limit:
                        continue
                    if folder == config.HISTORY_DIR and not (
                        path.name.endswith(".json.bak") or ".corrupt" in path.name
                    ):
                        continue
                    if folder == config.ATTACH_DIR and (path.name in in_use or path.stem in in_use or any(path.name.startswith(i) for i in in_use)):
                        continue
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    # --- anexos -----------------------------------------------------------------
    def save_attachment(self, name: str, data: bytes) -> str:
        suffix = Path(name).suffix.lower()[:10]
        att_id = uuid.uuid4().hex + suffix
        (config.ATTACH_DIR / att_id).write_bytes(data)
        return att_id

    def attachment_data_url(self, att_id: str) -> str | None:
        path = self.attachment_path(att_id)
        if not path.exists():
            return None
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    def attachment_path(self, att_id: str) -> Path:
        return config.ATTACH_DIR / Path(att_id).name
