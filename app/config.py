"""Nome do app e caminhos. Trocar o nome do produto = mudar só aqui (e no instalador)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_path

APP_NAME = "Rory Assistant"
APP_ID = "rory-assistant"
APP_VERSION = "1.0.0"

# Título da janela: o botão flutuante (claude-tray.ps1) procura a janela por ele.
WINDOW_TITLE = APP_NAME

if getattr(sys, "frozen", False):
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

UI_DIR = BASE_DIR / "ui"
ASSETS_DIR = BASE_DIR / "assets"
ICON_ICO = ASSETS_DIR / "icon.ico"
ICON_PNG = ASSETS_DIR / "icon-256.png"

# RORY_DATA_DIR: pasta de dados separada (testes sem mexer na instalação de verdade)
DATA_DIR = Path(os.environ["RORY_DATA_DIR"]) if os.environ.get("RORY_DATA_DIR") else user_data_path(
    APP_NAME, appauthor=False, roaming=True
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
HISTORY_DIR = DATA_DIR / "history"
ATTACH_DIR = DATA_DIR / "attachments"
HISTORY_DIR.mkdir(exist_ok=True)
ATTACH_DIR.mkdir(exist_ok=True)

HOME = str(Path.home())

DEFAULT_MODEL = os.environ.get("RORY_MODEL") or None  # None = o padrão do Claude Code


def find_claude() -> str | None:
    """CLI do Claude Code: o do PATH, os lugares comuns de instalação (apps abertos pelo
    menu/Finder não herdam o PATH do terminal) e, por último, o que vem dentro do wheel do
    claude-agent-sdk. Com o embutido, o único pré-requisito do usuário é fazer login."""
    import shutil

    found = shutil.which("claude")
    if found:
        return found
    exe = "claude.exe" if sys.platform == "win32" else "claude"
    home = Path.home()
    for folder in (home / ".local" / "bin", home / ".claude" / "local", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        candidate = folder / exe
        if candidate.is_file():
            return str(candidate)
    try:
        import claude_agent_sdk

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / exe
        if bundled.is_file():
            return str(bundled)
    except Exception:  # noqa: BLE001
        pass
    return None
