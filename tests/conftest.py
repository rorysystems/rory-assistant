"""Os testes usam uma pasta de dados temporária (nunca as conversas reais)."""

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="rory-tests-")
os.environ["RORY_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture()
def store():
    from app import config
    from app.store import Store

    for folder in (config.HISTORY_DIR, config.ATTACH_DIR):
        for item in folder.glob("*"):
            if item.is_file():
                item.unlink()
    config.STATE_FILE.unlink(missing_ok=True)
    return Store()
