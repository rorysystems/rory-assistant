"""Painel "Mudanças": o que mudou no git da pasta da conversa (status, diff por arquivo,
marcar como revisado = `git add`, reverter). Antes de reverter, o arquivo atual é copiado
para a pasta de dados (`revertidos/`), nunca para dentro do projeto."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
GIT_TIMEOUT = 20
DIFF_MAX = 400_000  # caracteres do diff mostrados
NEW_FILE_MAX = 200_000  # bytes lidos de um arquivo novo (sem histórico no git)
BACKUP_DIR = config.DATA_DIR / "revertidos"

LABELS = {"M": "modificado", "A": "novo", "D": "apagado", "R": "renomeado", "C": "copiado", "U": "conflito", "T": "tipo mudou", "?": "novo"}


class GitError(Exception):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=off", *args],
            capture_output=True, timeout=GIT_TIMEOUT, creationflags=NO_WINDOW,
        )
    except FileNotFoundError as error:
        raise GitError("Git não encontrado no PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise GitError("O git demorou demais para responder.") from error
    if check and done.returncode != 0:
        raise GitError(done.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} falhou")
    return done


def toplevel(folder: Path) -> Path:
    done = _git(folder, "rev-parse", "--show-toplevel", check=False)
    if done.returncode != 0:
        raise GitError("Esta pasta não é um repositório git.")
    return Path(done.stdout.decode("utf-8", "replace").strip())


def _inside(top: Path, rel: str) -> Path:
    target = (top / rel).resolve()
    if target != top.resolve() and top.resolve() not in target.parents:
        raise GitError("Caminho fora do repositório.")
    return target


def _has_head(top: Path) -> bool:
    return _git(top, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def parse_status(raw: bytes) -> list[dict[str, Any]]:
    """`git status --porcelain=v1 -z`: "XY caminho\\0"; renomeados trazem o nome antigo
    no campo seguinte."""
    parts = raw.decode("utf-8", "replace").split("\0")
    files: list[dict[str, Any]] = []
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        x, y, path = item[0], item[1], item[3:]
        orig = ""
        if x in "RC" or y in "RC":
            orig = parts[i] if i < len(parts) else ""
            i += 1
        code = "?" if x == "?" else ("U" if "U" in (x + y) or (x == y and x in "AD") else (x if x != " " else y))
        files.append({
            "path": path, "orig": orig, "x": x, "y": y, "code": code,
            "label": LABELS.get(code, code), "untracked": x == "?", "staged": x not in " ?",
        })
    return files


def parse_numstat(raw: bytes) -> dict[str, tuple[int, int]]:
    """`git diff --numstat -z`: "add\\tdel\\tcaminho\\0" (renomeado: "add\\tdel\\t\\0antigo\\0novo\\0")."""
    out: dict[str, tuple[int, int]] = {}
    parts = raw.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(parts):
        fields = parts[i].split("\t")
        i += 1
        if len(fields) < 3:
            continue
        path = fields[2]
        if not path:  # renomeado
            path = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
        add = int(fields[0]) if fields[0].isdigit() else 0
        rem = int(fields[1]) if fields[1].isdigit() else 0
        out[path] = (add, rem)
    return out


def changes(folder: Path) -> dict[str, Any]:
    top = toplevel(folder)
    # só o que está dentro da pasta da conversa (que pode ser uma subpasta do repositório)
    try:
        scope = folder.resolve().relative_to(top.resolve()).as_posix() or "."
    except ValueError:  # letra de unidade/maiúsculas diferentes: mostra o repositório todo
        scope = "."
    files = parse_status(_git(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", scope).stdout)
    if _has_head(top):
        stats = parse_numstat(_git(top, "diff", "HEAD", "--numstat", "-z", "--", scope, check=False).stdout)
    else:
        stats = {}
    for f in files:
        add, rem = stats.get(f["path"], (0, 0))
        if f["untracked"]:
            try:
                p = top / f["path"]
                data = p.read_bytes()[:NEW_FILE_MAX]
                add = data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)
            except OSError:
                add = 0
        f["add"], f["del"] = add, rem
    branch = _git(top, "branch", "--show-current", check=False).stdout.decode("utf-8", "replace").strip()
    return {"root": str(top), "branch": branch or "(sem branch)", "files": files}


def _new_file_diff(target: Path, rel: str) -> str:
    try:
        data = target.read_bytes()[:NEW_FILE_MAX]
    except OSError as error:
        raise GitError(str(error)) from error
    if b"\0" in data[:8000]:
        return f"Arquivo novo binário: {rel}"
    lines = data.decode("utf-8", "replace").splitlines()
    head = f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n"
    return head + "\n".join("+" + line for line in lines)


def diff(folder: Path, rel: str) -> dict[str, Any]:
    top = toplevel(folder)
    target = _inside(top, rel)
    tracked = _git(top, "ls-files", "--error-unmatch", "--", rel, check=False).returncode == 0
    if not tracked and target.is_file():
        text = _new_file_diff(target, rel)
    else:
        args = ["diff", "HEAD", "--", rel] if _has_head(top) else ["diff", "--cached", "--", rel]
        done = _git(top, *args, check=False)
        text = done.stdout.decode("utf-8", "replace")
        if not text.strip():
            text = "Sem diferenças de texto (talvez só permissão ou fim de linha)."
    cut = len(text) > DIFF_MAX
    return {"path": rel, "diff": text[:DIFF_MAX], "cut": cut}


def stage(folder: Path, rel: str, orig: str = "") -> None:
    top = toplevel(folder)
    _inside(top, rel)
    _git(top, "add", "-A", "--", *[p for p in (rel, orig) if p])


def _backup(top: Path, target: Path, rel: str) -> Path | None:
    if not target.is_file():
        return None
    dest = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S") / top.name / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)
    return dest


def revert(folder: Path, rel: str, orig: str = "") -> dict[str, Any]:
    """Volta o arquivo ao último commit. Arquivo novo (sem commit) é tirado do projeto e
    fica guardado só em `revertidos/`."""
    top = toplevel(folder)
    target = _inside(top, rel)
    in_head = _has_head(top) and _git(top, "cat-file", "-e", f"HEAD:{rel}", check=False).returncode == 0
    saved = _backup(top, target, rel)
    if target.is_dir():
        raise GitError("Reverter uma pasta inteira não é suportado.")
    if orig:  # renomeado: o nome antigo volta do HEAD
        _inside(top, orig)
        _git(top, "restore", "--source=HEAD", "--staged", "--worktree", "--", orig)
    if in_head:
        _git(top, "restore", "--source=HEAD", "--staged", "--worktree", "--", rel)
    else:  # arquivo que não existe no último commit: sai do índice e do projeto
        _git(top, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", rel, check=False)
        if target.is_file():
            target.unlink()
    return {"backup": str(saved) if saved else ""}
