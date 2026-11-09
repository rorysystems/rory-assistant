"""Sugestões para o "!comando": as mesmas do PowerShell (TabExpansion2) + histórico.

Um powershell.exe fica aberto em segundo plano (abrir um a cada tecla levaria ~0,5 s) e
responde uma linha JSON por pedido. Fora do Windows, usa `compgen` do bash.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
MARK = "@@RORY@@"
MAX_ITEMS = 60
REPLY_TIMEOUT = 4.0  # s: o completador que não responde é morto e reiniciado
_hist_cache: dict[str, Any] = {"key": None, "lines": []}

# Laço do completador: lê {"cwd","text","col"} por linha e devolve o resultado do TabExpansion2.
_PS_SERVER = r"""
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = [Text.Encoding]::UTF8
$stdin = [Console]::In
while ($true) {
  $line = $stdin.ReadLine()
  if ($line -eq $null) { break }
  $out = @{ start = 0; length = 0; items = @() }
  try {
    $req = $line | ConvertFrom-Json
    if ($req.cwd) { Set-Location -LiteralPath $req.cwd }
    $r = TabExpansion2 -inputScript $req.text -cursorColumn ([int]$req.col)
    $out.start = $r.ReplacementIndex
    $out.length = $r.ReplacementLength
    $out.items = @($r.CompletionMatches | Select-Object -First __MAX__ | ForEach-Object {
      $tip = [string]$_.ToolTip
      if ($tip.Length -gt 140) { $tip = $tip.Substring(0, 140) + '...' }
      @{ t = $_.CompletionText; l = $_.ListItemText; k = [string]$_.ResultType; d = $tip.Trim() }
    })
  } catch {}
  [Console]::Out.WriteLine('__MARK__' + (ConvertTo-Json $out -Compress -Depth 4))
  [Console]::Out.Flush()
}
""".replace("__MAX__", str(MAX_ITEMS)).replace("__MARK__", MARK)

# Subcomandos do git (o PowerShell sem posh-git não completa depois de "git ").
GIT_COMMANDS = {
    "add": "Adiciona arquivos ao próximo commit", "commit": "Grava as mudanças",
    "status": "Mostra o que mudou", "push": "Envia os commits ao remoto",
    "pull": "Baixa e junta as mudanças do remoto", "fetch": "Baixa as mudanças do remoto",
    "checkout": "Troca de branch ou restaura arquivos", "switch": "Troca de branch",
    "branch": "Lista, cria ou apaga branches", "merge": "Junta outra branch nesta",
    "rebase": "Reaplica commits sobre outra base", "log": "Histórico de commits",
    "diff": "Diferenças ainda não commitadas", "stash": "Guarda as mudanças de lado",
    "restore": "Desfaz mudanças em arquivos", "reset": "Volta o HEAD / tira do stage",
    "remote": "Gerencia os remotos", "clone": "Copia um repositório", "init": "Cria um repositório",
    "tag": "Cria ou lista tags", "show": "Mostra um commit", "cherry-pick": "Aplica um commit específico",
    "revert": "Cria um commit que desfaz outro", "rm": "Remove arquivos do repositório",
    "mv": "Move ou renomeia arquivos", "blame": "Quem mudou cada linha", "config": "Configurações do git",
    "clean": "Apaga arquivos não rastreados", "reflog": "Histórico do HEAD",
}
GIT_REF_COMMANDS = {"checkout", "switch", "merge", "rebase", "branch", "cherry-pick", "reset", "log", "diff", "show", "push", "pull"}


def _ps_history() -> list[str]:
    """Histórico do PSReadLine (o mesmo que o terminal sugere em cinza)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    path = Path(appdata) / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if _hist_cache["key"] == key:  # só relê quando o arquivo mudou
            return _hist_cache["lines"]
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    result = [line for line in lines[-3000:] if line.strip() and not line.endswith("`")]
    _hist_cache.update(key=key, lines=result)
    return result


def _git_refs(cwd: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes", "refs/tags"],
            cwd=cwd, capture_output=True, timeout=3, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [r for r in proc.stdout.decode("utf-8", "replace").splitlines() if r and not r.endswith("/HEAD")]


class ShellCompleter:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen[bytes]:
        if self._proc and self._proc.poll() is None:
            return self._proc
        encoded = base64.b64encode(_PS_SERVER.encode("utf-16-le")).decode()
        self._proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
        # leitor próprio: assim a espera pela resposta tem timeout (readline sozinho trava)
        lines: queue.Queue[bytes | None] = queue.Queue()
        self._lines = lines
        threading.Thread(target=self._pump, args=(self._proc, lines), daemon=True).start()
        return self._proc

    @staticmethod
    def _pump(proc: subprocess.Popen[bytes], lines: "queue.Queue[bytes | None]") -> None:
        try:
            assert proc.stdout
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)

    def warm(self) -> None:
        if sys.platform == "win32":
            with self._lock:
                self._start()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _powershell(self, cwd: str, text: str, col: int) -> dict[str, Any]:
        # outro pedido em andamento (digitação rápida): este já seria descartado pela tela
        if not self._lock.acquire(timeout=2):
            return {"start": col, "length": 0, "items": []}
        try:
            for _ in range(2):  # o processo pode ter morrido: tenta de novo uma vez
                proc = self._start()
                try:
                    assert proc.stdin
                    lines = self._lines
                    while not lines.empty():  # sobras de um pedido que estourou o tempo
                        lines.get_nowait()
                    proc.stdin.write((json.dumps({"cwd": cwd, "text": text, "col": col}) + "\n").encode("utf-8"))
                    proc.stdin.flush()
                    while True:
                        line = lines.get(timeout=REPLY_TIMEOUT)
                        if line is None:
                            raise OSError("completador fechou")
                        line = line.decode("utf-8", "replace").lstrip("﻿").strip()
                        if line.startswith(MARK):
                            return json.loads(line[len(MARK):])
                except (OSError, ValueError, queue.Empty):
                    self.close()  # morreu ou travou: o próximo pedido abre outro
            return {"start": col, "length": 0, "items": []}
        finally:
            self._lock.release()

    def _bash(self, cwd: str, text: str, col: int) -> dict[str, Any]:
        head = text[:col]
        word = head.split(" ")[-1]
        flag = "-c" if " " not in head.strip() else "-f"
        try:
            proc = subprocess.run(["bash", "-c", f"compgen {flag} -- \"$1\" | sort -u | head -{MAX_ITEMS}", "_", word],
                                  cwd=cwd, capture_output=True, timeout=3)
            names = proc.stdout.decode("utf-8", "replace").split()
        except (OSError, subprocess.TimeoutExpired):
            names = []
        kind = "Command" if flag == "-c" else "ProviderItem"
        return {"start": col - len(word), "length": len(word), "items": [{"t": n, "l": n, "k": kind, "d": ""} for n in names]}

    def _git(self, cwd: str, text: str, col: int) -> dict[str, Any] | None:
        head = text[:col]
        words = head.split(" ")
        if len(words) < 2 or words[0].lower() not in ("git", "git.exe"):
            return None
        word = words[-1]
        if len(words) == 2:
            items = [{"t": k, "l": k, "k": "Command", "d": v} for k, v in GIT_COMMANDS.items() if k.startswith(word)]
        elif words[1] in GIT_REF_COMMANDS and not word.startswith("-"):
            items = [{"t": r, "l": r, "k": "ParameterValue", "d": "branch / tag"} for r in _git_refs(cwd) if r.startswith(word)]
            if not items:
                return None  # deixa o PowerShell sugerir arquivos (ex.: git checkout -- arq)
        else:
            return None
        return {"start": col - len(word), "length": len(word), "items": items[:MAX_ITEMS]}

    def complete(self, cwd: str, text: str, col: int, extra_history: list[str]) -> dict[str, Any]:
        col = max(0, min(col, len(text)))
        result = self._git(cwd, text, col)
        if result is None:
            if sys.platform == "win32":
                result = self._powershell(cwd, text, col)
            else:
                result = self._bash(cwd, text, col)
        # histórico: comandos que começam com o que já foi digitado (mais recentes primeiro)
        history: list[str] = []
        typed = text.strip()
        if typed and col >= len(text.rstrip()):
            seen: set[str] = set()
            for line in reversed(extra_history + (_ps_history() if sys.platform == "win32" else [])):
                line = line.strip()
                if line.lower().startswith(typed.lower()) and line != typed and line not in seen:
                    seen.add(line)
                    history.append(line)
                    if len(history) >= 5:
                        break
        result["history"] = history
        return result
