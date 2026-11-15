"""Log de diagnóstico: `app.log` na pasta de dados (rotativo, 3 x 512 KB).

O app roda com pythonw (sem console), então print() não vai a lugar nenhum; use `log`.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config

LOG_FILE = config.DATA_DIR / "app.log"

log = logging.getLogger("rory")


def _setup() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        handler: logging.Handler = RotatingFileHandler(
            LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.propagate = False


_setup()
