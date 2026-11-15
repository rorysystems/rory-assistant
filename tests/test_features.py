"""Painel de mudanças (git), busca, fila e exportação."""

import shutil
import subprocess

import pytest

from app import export, gitdiff
from app.store import entry_text

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git não instalado")


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(gitdiff, "BACKUP_DIR", tmp_path / "_backup")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("um\ndois\n", encoding="utf-8")
    (root / "b.txt").write_text("fica\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "inicio")
    return root


@needs_git
def test_changes_diff_stage_revert(repo, tmp_path):
    (repo / "a.txt").write_text("um\ntrês\n", encoding="utf-8")
    (repo / "novo file.txt").write_text("x\ny\n", encoding="utf-8")
    (repo / "b.txt").unlink()
    info = gitdiff.changes(repo)
    by = {f["path"]: f for f in info["files"]}
    assert by["a.txt"]["code"] == "M" and by["a.txt"]["add"] == 1 and by["a.txt"]["del"] == 1
    assert by["novo file.txt"]["untracked"] and by["novo file.txt"]["add"] == 2
    assert by["b.txt"]["code"] == "D"

    d = gitdiff.diff(repo, "a.txt")["diff"]
    assert "-dois" in d and "+três" in d
    assert "+x" in gitdiff.diff(repo, "novo file.txt")["diff"]

    gitdiff.stage(repo, "a.txt")
    assert {f["path"]: f for f in gitdiff.changes(repo)["files"]}["a.txt"]["staged"]

    out = gitdiff.revert(repo, "a.txt")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "um\ndois\n"
    assert "três" in open(out["backup"], encoding="utf-8").read()
    gitdiff.revert(repo, "novo file.txt")
    assert not (repo / "novo file.txt").exists()
    gitdiff.revert(repo, "b.txt")
    assert (repo / "b.txt").exists()
    assert gitdiff.changes(repo)["files"] == []

    with pytest.raises(gitdiff.GitError):
        gitdiff.diff(repo, "../fora.txt")


@needs_git
def test_not_a_repo(tmp_path):
    folder = tmp_path / "solta"
    folder.mkdir()
    with pytest.raises(gitdiff.GitError):
        gitdiff.changes(folder)


def test_parse_status_rename():
    raw = b"R  novo.txt\0velho.txt\0?? x.txt\0 M y.txt\0"
    files = gitdiff.parse_status(raw)
    assert files[0]["path"] == "novo.txt" and files[0]["orig"] == "velho.txt" and files[0]["code"] == "R"
    assert files[1]["untracked"] and files[2]["code"] == "M" and not files[2]["staged"]


def test_search(store, tmp_path):
    a = store.create(str(tmp_path))
    b = store.create(str(tmp_path))
    store.append_history(a["id"], {"role": "user", "text": "Como faço o deploy?", "time": "2026-01-01T00:00:00"})
    store.append_history(a["id"], {"role": "assistant", "parts": [{"type": "text", "text": "Use o script de DEPLOY."}], "time": "2026-01-01T00:00:01"})
    store.append_history(b["id"], {"role": "shell", "command": "git log", "output": "nada", "time": "2026-01-01T00:00:02"})
    hits = store.search("deploy")
    assert {(h["conv"], h["index"]) for h in hits} == {(a["id"], 0), (a["id"], 1)}
    assert store.search("git log")[0]["conv"] == b["id"]
    assert store.search("x") == []  # curta demais


def test_queue(store, tmp_path):
    conv = store.create(str(tmp_path))
    store.queue_push(conv["id"], "um")
    q = store.queue_push(conv["id"], "dois")
    assert [i["text"] for i in q] == ["um", "dois"]
    assert store.queue_pop(conv["id"])["text"] == "um"
    assert [i["text"] for i in store.queue_remove(conv["id"], q[1]["id"])] == []
    assert store.queue_pop(conv["id"]) is None


def test_export_markdown():
    conv = {"title": "Teste: a/b", "path": "C:/x"}
    hist = [
        {"role": "user", "text": "oi", "attachments": [{"name": "a.png"}], "time": "2026-01-01T10:00:00"},
        {"role": "assistant", "parts": [{"type": "tool", "name": "Read", "summary": "a.py"}, {"type": "text", "text": "pronto"}], "time": "2026-01-01T10:00:05"},
        {"role": "shell", "command": "ls", "output": "```\na", "time": "2026-01-01T10:01:00"},
        {"role": "divider", "text": "compactado"},
    ]
    md = export.to_markdown(conv, hist)
    assert "# Teste: a/b" in md and "### Você · 01/01/2026 10:00" in md and "`a.png`" in md
    assert "**Read** `a.py`" in md and "pronto" in md and "````text" in md and "compactado" in md
    assert "Read" not in export.to_markdown(conv, hist, tools=False)
    assert export.safe_filename('a/b:c*?"') == "a b c"
    assert "pronto" in entry_text(hist[1])


def test_session_queue_dispatch(store, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from app.agent import Session

    conv = store.create(str(tmp_path))
    events = []
    hub = SimpleNamespace(store=store, emit=events.append)
    s = Session(hub, conv["id"])
    sent = []

    async def fake_submit(blocks, entry):
        sent.append((blocks[0]["text"], entry.get("queued")))

    s.submit = fake_submit

    async def run():
        store.queue_push(conv["id"], "primeira")
        store.queue_push(conv["id"], "segunda")
        s.queue_hold = True  # usuário parou o turno: não anda
        assert not s.next_in_queue()
        s.queue_hold = False
        assert s.next_in_queue() and s.busy
        assert not s.next_in_queue()  # ocupada: espera o turno acabar
        await asyncio.sleep(0)
        s.busy = False
        assert s.next_in_queue()
        await asyncio.sleep(0)

    asyncio.run(run())
    assert sent == [("primeira", True), ("segunda", True)]
    assert any(e.get("type") == "queued_sent" for e in events)
    assert store.queue(conv["id"]) == []
