import json

from app import config


def test_history_page_and_incremental_index(store, tmp_path):
    conv = store.create(str(tmp_path))
    for i in range(100):
        store.append_history(conv["id"], {"role": "user", "text": f"msg {i}", "time": "2026-01-01T00:00:00"})
    page = store.history_page(conv["id"], None, 40)
    assert page["total"] == 100 and page["start"] == 60
    assert page["items"][0]["text"] == "msg 60" and page["items"][-1]["text"] == "msg 99"
    older = store.history_page(conv["id"], page["start"], 40)
    assert older["start"] == 20 and older["items"][-1]["text"] == "msg 59"
    # acrescenta depois de indexado: o índice só olha o trecho novo
    store.append_history(conv["id"], {"role": "user", "text": "novo", "time": "2026-01-01T00:00:01"})
    assert store.history_page(conv["id"], None, 1)["items"][0]["text"] == "novo"
    assert store.history_page(conv["id"], 0, 10)["items"] == []


def test_history_survives_cut_line(store, tmp_path):
    conv = store.create(str(tmp_path))
    store.append_history(conv["id"], {"role": "user", "text": "a", "time": "t"})
    path = config.HISTORY_DIR / f"{conv['id']}.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"role": "user", "te')  # queda de energia no meio da linha
    store.append_history(conv["id"], {"role": "user", "text": "b", "time": "t"})
    texts = [m["text"] for m in store.history_page(conv["id"])["items"]]
    assert texts == ["a", "b"]


def test_save_soon_and_flush(store, tmp_path):
    conv = store.create(str(tmp_path))
    store.append_history(conv["id"], {"role": "user", "text": "oi", "time": "2030-01-01T00:00:00"})
    store.flush()
    saved = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    assert saved["conversations"][0]["updated"] == "2030-01-01T00:00:00"
    assert saved["conversations"][0]["title"] == "oi"


def test_get_returns_copy(store, tmp_path):
    conv = store.create(str(tmp_path))
    copy = store.get(conv["id"])
    copy["title"] = "mudou fora"
    assert store.get(conv["id"])["title"] == "Nova conversa"


def test_group_cycle_refused(store):
    a = store.add_group("a")
    b = store.add_group("b", a["id"])
    store.set_group_parent(a["id"], b["id"])  # a dentro de b, que já está em a: recusa
    assert "parent" not in next(g for g in store.groups() if g["id"] == a["id"])


def test_delete_group_lifts_children(store, tmp_path):
    a = store.add_group("a")
    b = store.add_group("b", a["id"])
    conv = store.create(str(tmp_path))
    store.set_conv_group(conv["id"], b["id"])
    store.delete_group(b["id"])
    assert store.get(conv["id"])["group"] == a["id"]


def test_attachment_name_is_confined(store):
    att = store.save_attachment("x.txt", b"1")
    assert store.attachment_path("../../" + att).parent == config.ATTACH_DIR
