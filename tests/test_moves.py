import shutil
from types import SimpleNamespace

from fastapi.testclient import TestClient

from pdfindex import notes as notes_mod
from pdfindex.app import create_app, watch_relevant
from pdfindex.config import Config
from pdfindex.db import list_issues
from pdfindex.indexer import scan_folders
from pdfindex.search import search

from conftest import make_text_pdf


def doc(conn, name):
    return conn.execute("SELECT * FROM documents WHERE path LIKE ?", (f"%{name}",)).fetchone()


def test_move_keeps_id_notes_and_text_without_reindex(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["turbine alpha"])
    scan_folders(conn, [pdf_dir])
    d = doc(conn, "a.pdf")
    notes_mod.save_note(conn, d["id"], 1, "my note")
    (pdf_dir / "sub").mkdir()
    shutil.move(pdf_dir / "a.pdf", pdf_dir / "sub" / "a.pdf")
    stats = scan_folders(conn, [pdf_dir])
    assert (stats["moved"], stats["indexed"], stats["removed"]) == (1, 0, 0)
    moved = conn.execute("SELECT * FROM documents").fetchall()
    assert len(moved) == 1 and moved[0]["id"] == d["id"] and moved[0]["path"].endswith("sub\\a.pdf")
    assert notes_mod.get_note(conn, d["id"], 1) == "my note"
    assert search(conn, "turbine")[0]["doc_id"] == d["id"]


def test_rename_detected(conn, pdf_dir):
    make_text_pdf(pdf_dir / "old.pdf", ["gearbox"])
    scan_folders(conn, [pdf_dir])
    old_id = doc(conn, "old.pdf")["id"]
    (pdf_dir / "old.pdf").rename(pdf_dir / "new.pdf")
    assert scan_folders(conn, [pdf_dir])["moved"] == 1
    assert doc(conn, "new.pdf")["id"] == old_id


def test_move_detected_after_file_was_already_marked_missing(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["gearbox"])
    scan_folders(conn, [pdf_dir])
    keep = pdf_dir / "elsewhere"
    keep.mkdir()
    shutil.move(pdf_dir / "a.pdf", keep / "a.pdf")
    (keep / "a.pdf").rename(keep.parent.parent / "a_tmp.pdf")  # leaves the scanned tree
    assert scan_folders(conn, [pdf_dir])["removed"] == 1
    shutil.move(keep.parent.parent / "a_tmp.pdf", pdf_dir / "b.pdf")
    assert scan_folders(conn, [pdf_dir])["moved"] == 1
    assert search(conn, "gearbox")


def test_duplicates_matched_one_to_one_preferring_same_name(conn, pdf_dir):
    for d in ("x", "y"):
        (pdf_dir / d).mkdir()
        make_text_pdf(pdf_dir / d / "same.pdf", ["identical"])
    scan_folders(conn, [pdf_dir])
    ids = {r["path"].split("\\")[-2]: r["id"] for r in conn.execute("SELECT id, path FROM documents")}
    (pdf_dir / "z").mkdir()
    shutil.move(pdf_dir / "y" / "same.pdf", pdf_dir / "z" / "same.pdf")
    shutil.move(pdf_dir / "x" / "same.pdf", pdf_dir / "x" / "renamed.pdf")
    stats = scan_folders(conn, [pdf_dir])
    assert stats["moved"] == 2 and stats["indexed"] == 0
    assert {r["id"] for r in conn.execute("SELECT id FROM documents")} == set(ids.values())
    assert doc(conn, "z\\same.pdf")["id"] == ids["y"]
    assert doc(conn, "renamed.pdf")["id"] == ids["x"]


def test_moved_and_modified_is_flagged_not_relinked(conn, pdf_dir):
    make_text_pdf(pdf_dir / "r.pdf", ["first", "second"])
    scan_folders(conn, [pdf_dir])
    old = doc(conn, "r.pdf")
    notes_mod.save_note(conn, old["id"], 1, "keep me")
    (pdf_dir / "n").mkdir()
    (pdf_dir / "r.pdf").unlink()
    make_text_pdf(pdf_dir / "n" / "r.pdf", ["first", "second edited"])
    stats = scan_folders(conn, [pdf_dir])
    assert stats["moved"] == 0 and stats["indexed"] == 1
    new = doc(conn, "n\\r.pdf")
    assert new["id"] != old["id"]
    assert any(i["message"].startswith("Possibly moved") for i in list_issues(conn))
    assert [g["doc_id"] for g in notes_mod.list_orphaned(conn)] == [old["id"]]
    notes_mod.relink_orphan(conn, old["id"], new["id"])
    assert notes_mod.get_note(conn, new["id"], 1) == "keep me"
    assert notes_mod.list_orphaned(conn) == []


def test_deleted_file_keeps_notes_as_orphaned_and_api_relinks(tmp_path, pdf_dir):
    make_text_pdf(pdf_dir / "gone.pdf", ["lost"])
    make_text_pdf(pdf_dir / "other.pdf", ["different"])
    app = create_app(Config(folders=[str(pdf_dir)], db_path=str(tmp_path / "o.db")), ocr=None, watch=False)
    client = TestClient(app, base_url="http://localhost")
    scanner = app.state.scanner
    scanner.run_blocking()
    docs = {d["path"].split("\\")[-1]: d["id"] for d in client.get("/api/docs").json()}
    assert client.put(f"/api/notes/{docs['gone.pdf']}/1", json={"text": "precious"}).status_code == 200
    (pdf_dir / "gone.pdf").unlink()
    scanner.run_blocking()
    assert scanner.state["last"]["removed"] == 1
    assert client.get("/api/search?q=lost").json()["count"] == 0
    orphans = client.get("/api/orphans").json()
    assert orphans[0]["notes"] == [{"page": 1, "text": "precious"}]
    assert client.post(f"/api/orphans/{docs['gone.pdf']}/relink", json={"target_id": 9999}).status_code == 422
    r = client.post(f"/api/orphans/{docs['gone.pdf']}/relink", json={"target_id": docs["other.pdf"]})
    assert r.status_code == 200
    assert client.get(f"/api/notes/{docs['other.pdf']}/1").json()["text"] == "precious"
    assert client.get("/api/orphans").json() == []


def test_watcher_reacts_to_moves_and_folder_events():
    ev = lambda **k: SimpleNamespace(src_path=k.get("src", ""), dest_path=k.get("dest", ""), is_directory=k.get("d", False))
    assert watch_relevant(ev(src="a.txt", dest="b.PDF"))
    assert watch_relevant(ev(src="folder", dest="folder2", d=True))
    assert not watch_relevant(ev(src="a.exe"))
