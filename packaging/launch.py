"""Ponto de entrada do executável (PyInstaller não roda `python -m app.main`)."""

import os
import sys

if sys.platform.startswith("linux"):
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")  # o pacote Linux usa o Qt do pip

from app.main import main

if __name__ == "__main__":
    main()
