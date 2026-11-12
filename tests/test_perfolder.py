import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from pdfindex.app import create_app
from pdfindex.config import Config, load_config
from pdfindex.db import connect
from pdfindex.notes import save_note
from pdfindex.store import DB_NAME, SLOT

from conftest import fake_ocr, make_text_pdf


def two_folders(tmp_path, **cfg_kw):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    make_text_pdf(a / "one.pdf", ["shared word alpha"])
    make_text_pdf(b / "two.pdf", ["shared word zebra"])
    return a, b, Config(folders=[str(a), str(b)], db_path=str(tmp_path / "legacy.db"), **cfg_kw)


def count(db, sql):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def test_each_folder_gets_its_own_database_and_search_spans_both(tmp_path):
    a, b, cfg = two_folders(tmp_path)
    client = TestClient(create_app(cfg, ocr=fake_ocr, watch=False), base_url="http://localhost")
    client.app.state.scanner.run_blocking()
    assert count(a / DB_NAME, "SELECT COUNT(*) FROM documents") == 1
    assert count(b / DB_NAME, "SELECT COUNT(*) FROM documents") == 1
    assert not (tmp_path / "legacy.db").exists()
    groups = client.get("/api/search", params={"q": "shared"}).json()["groups"]
    assert sorted(Path(g["path"]).name for g in groups) == ["one.pdf", "two.pdf"]
    assert len({g["doc_id"] for g in groups}) == 2
    assert client.get("/api/status").json()["documents"] == 2


def test_notes_and_issues_stay_with_their_folder_and_filters_use_global_ids(tmp_path):
    a, b, cfg = two_folders(tmp_path)
    (b / "broken.pdf").write_bytes(b"not a pdf")
    client = TestClient(create_app(cfg, ocr=fake_ocr, watch=False), base_url="http://localhost")
    client.app.state.scanner.run_blocking()
    docs = {Path(d["path"]).name: d["id"] for d in client.get("/api/docs").json()}
    assert docs["two.pdf"] >= SLOT and docs["one.pdf"] < SLOT
    assert client.put(f"/api/notes/{docs['two.pdf']}/1", json={"text": "bnote"}).status_code == 200
    assert count(b / DB_NAME, "SELECT COUNT(*) FROM notes") == 1
    assert count(a / DB_NAME, "SELECT COUNT(*) FROM notes") == 0
    assert count(b / DB_NAME, "SELECT COUNT(*) FROM issues") >= 1
    assert count(a / DB_NAME, "SELECT COUNT(*) FROM issues") == 0
    assert [n["doc_id"] for n in client.get("/api/notes").json()] == [docs["two.pdf"]]
    assert any("broken.pdf" in i["path"] for i in client.get("/api/issues").json())
    only = client.get("/api/search", params={"q": "shared", "docs": str(docs["two.pdf"])}).json()["groups"]
    assert [Path(g["path"]).name for g in only] == ["two.pdf"]
    excl = client.get("/api/search", params={"q": "shared", "docs": str(docs["two.pdf"]), "mode": "exclude"}).json()["groups"]
    assert [Path(g["path"]).name for g in excl] == ["one.pdf"]
    assert client.get(f"/api/docs/{docs['two.pdf']}").json()["note_pages"] == [1]
    assert client.get(f"/api/pdf/{docs['two.pdf']}").status_code == 200
    assert client.get(f"/api/docs/{3 * SLOT + 1}").status_code == 404


def test_legacy_single_database_is_split_per_folder_and_left_untouched(tmp_path):
    a, b, cfg = two_folders(tmp_path)
    old = connect(cfg.db_path)
    ids = {}
    for name, folder in (("one.pdf", a), ("two.pdf", b)):
        cur = old.execute("INSERT INTO documents (path,size,mtime,hash,pages,status) VALUES (?,?,?,?,?,'ok')",
                          (str(folder / name), 1, 1.0, "h", 1))
        ids[name] = cur.lastrowid
        old.execute("INSERT INTO pages_fts (doc_id,page,source,bbox,content) VALUES (?,?,?,?,?)",
                    (cur.lastrowid, 1, "text", None, f"legacy {name}"))
    old.commit()
    save_note(old, ids["two.pdf"], 1, "old note")
    old.close()
    create_app(cfg, ocr=fake_ocr, watch=False)
    assert count(a / DB_NAME, "SELECT COUNT(*) FROM documents") == 1
    assert count(b / DB_NAME, "SELECT COUNT(*) FROM notes") == 1
    assert count(a / DB_NAME, "SELECT COUNT(*) FROM notes") == 0
    assert count(b / DB_NAME, "SELECT COUNT(*) FROM pages_fts WHERE content LIKE 'legacy two%'") == 1
    assert count(cfg.db_path, "SELECT COUNT(*) FROM documents") == 2


def test_removing_and_readding_a_folder_keeps_its_database(tmp_path):
    a, b, _ = two_folders(tmp_path)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'folders = ["a"]\ndb_path = "legacy.db"\n', encoding="utf-8")
    client = TestClient(create_app(load_config(cfg_path), ocr=fake_ocr, watch=False), base_url="http://localhost")
    client.post("/api/folders", json={"path": str(b)})
    wait_idle(client)
    did = {Path(d["path"]).name: d["id"] for d in client.get("/api/docs").json()}["two.pdf"]
    client.put(f"/api/notes/{did}/1", json={"text": "keep me"})
    client.request("DELETE", "/api/folders", json={"path": str(b)})
    wait_idle(client)
    assert client.get("/api/search", params={"q": "zebra"}).json()["count"] == 0
    client.post("/api/folders", json={"path": str(b)})
    wait_idle(client)
    assert [n["text"] for n in client.get("/api/notes").json()] == ["keep me"]


def wait_idle(client):
    sc = client.app.state.scanner
    while sc.state["running"]:
        time.sleep(0.05)
