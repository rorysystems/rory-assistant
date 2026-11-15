import base64

import pytest

from app import main


@pytest.fixture()
def api(store, tmp_path):
    a = main.Api.__new__(main.Api)
    a.store = store
    a._shells = {}
    a._shell_stopped = set()
    return a


def test_inside_blocks_escape(api, tmp_path):
    conv = api.store.create(str(tmp_path))
    (tmp_path / "ok.txt").write_text("x")
    assert api._inside(conv["id"], "ok.txt") == (tmp_path / "ok.txt").resolve()
    assert api._inside(conv["id"], "..") is None
    assert api._inside(conv["id"], "../outro.txt") is None


def test_safe_url():
    ok = main.Api.safe_url
    assert ok("https://exemplo.com/a?b=1")
    assert ok("javascript:alert(1)") is None
    assert ok("file:///C:/x") is None
    assert ok("https://user:pw@exemplo.com") is None
    assert ok("https://exemplo.com/a b") is None
    assert ok("https://") is None


def test_dangerous_extensions():
    assert main._is_dangerous("a.BAT") and main._is_dangerous("x.ps1")
    assert not main._is_dangerous("a.txt")


def test_attachment_limits(api, tmp_path, monkeypatch):
    conv = api.store.create(str(tmp_path))
    monkeypatch.setattr(main, "ATTACH_MAX_BYTES", 10)
    big = base64.b64encode(b"x" * 100).decode()
    r = api.send(conv["id"], "oi", [{"name": "grande.bin", "data": big}])
    assert not r["ok"] and "grande.bin" in r["error"]
