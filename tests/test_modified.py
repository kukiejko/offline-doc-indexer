import os
import shutil

from fastapi.testclient import TestClient

from pdfindex import notes as notes_mod
from pdfindex.app import Scanner, create_app, watch_relevant
from pdfindex.config import Config, load_config
from pdfindex.indexer import scan_folders
from pdfindex.search import search

from conftest import make_text_pdf
from types import SimpleNamespace


def same_stat_overwrite(src_pdf, dst):
    """Replace content but restore size and mtime, as a sneaky edit would."""
    st = os.stat(dst)
    shutil.copy(src_pdf, dst)
    os.utime(dst, (st.st_atime, st.st_mtime))


def test_verify_detects_edit_hidden_behind_same_size_and_mtime(conn, pdf_dir, tmp_path):
    make_text_pdf(pdf_dir / "a.pdf", ["alphaxx one"])
    make_text_pdf(tmp_path / "b.pdf", ["betaxxx two"])
    assert (pdf_dir / "a.pdf").stat().st_size == (tmp_path / "b.pdf").stat().st_size
    scan_folders(conn, [pdf_dir])
    same_stat_overwrite(tmp_path / "b.pdf", pdf_dir / "a.pdf")
    assert scan_folders(conn, [pdf_dir])["skipped"] == 1  # fast mode misses it
    stats = scan_folders(conn, [pdf_dir], verify=True)
    assert stats["modified"] == 1 and stats["modified_files"] == [str(pdf_dir / "a.pdf")]
    assert search(conn, "betaxxx") and not search(conn, "alphaxx")


def test_verify_reports_hashing_phase(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["one"])
    scan_folders(conn, [pdf_dir])
    phases = []
    scan_folders(conn, [pdf_dir], verify=True, report=lambda s: phases.append(s["phase"]))
    assert "hashing" in phases


def test_modified_keeps_doc_id_and_flags_notes_until_dismissed(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["first"])
    scan_folders(conn, [pdf_dir])
    doc_id = conn.execute("SELECT id FROM documents").fetchone()[0]
    notes_mod.save_note(conn, doc_id, 1, "remember")
    make_text_pdf(pdf_dir / "a.pdf", ["first changed", "more pages"])
    stats = scan_folders(conn, [pdf_dir])
    assert stats["modified"] == 1
    assert conn.execute("SELECT id, pages FROM documents").fetchall()[0][:] == (doc_id, 2)
    assert notes_mod.get_note(conn, doc_id, 1) == "remember"
    assert notes_mod.get_note_stale(conn, doc_id, 1) is not None
    assert notes_mod.list_notes(conn)[0]["stale_at"] is not None
    notes_mod.dismiss_stale(conn, doc_id, 1)
    assert notes_mod.get_note_stale(conn, doc_id, 1) is None


def test_new_file_is_not_modified_and_editing_note_clears_warning(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["x"])
    assert scan_folders(conn, [pdf_dir])["modified"] == 0
    doc_id = conn.execute("SELECT id FROM documents").fetchone()[0]
    notes_mod.save_note(conn, doc_id, 1, "n")
    make_text_pdf(pdf_dir / "a.pdf", ["y"])
    scan_folders(conn, [pdf_dir])
    notes_mod.save_note(conn, doc_id, 1, "n edited")
    assert notes_mod.get_note_stale(conn, doc_id, 1) is None


def test_same_hash_new_mtime_only_updates_mtime(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["x"])
    scan_folders(conn, [pdf_dir])
    p = pdf_dir / "a.pdf"
    os.utime(p, (1_700_000_000, 1_700_000_000))
    stats = scan_folders(conn, [pdf_dir])
    assert (stats["skipped"], stats["indexed"], stats["modified"]) == (1, 0, 0)
    assert conn.execute("SELECT mtime FROM documents").fetchone()[0] == 1_700_000_000


def test_api_modes_config_and_dismiss(tmp_path, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["x"])
    (tmp_path / "c.toml").write_text('folders=["pdfs"]\nverify_checksums=true\n')
    assert load_config(tmp_path / "c.toml").verify_checksums is True
    assert Config().verify_checksums is False
    app = create_app(Config(folders=[str(pdf_dir)], db_path=str(tmp_path / "v.db")), ocr=None, watch=False)
    client = TestClient(app, base_url="http://localhost")
    app.state.scanner.run_blocking(verify=True)
    assert client.get("/api/status").json()["last"]["mode"] == "verify"
    assert client.post("/api/rescan?mode=bogus").status_code == 422
    doc_id = client.get("/api/docs").json()[0]["id"]
    client.put(f"/api/notes/{doc_id}/1", json={"text": "n"})
    make_text_pdf(pdf_dir / "a.pdf", ["changed"])
    app.state.scanner.run_blocking()
    assert client.get(f"/api/notes/{doc_id}/1").json()["stale_at"]
    assert client.post(f"/api/notes/{doc_id}/1/dismiss").status_code == 200
    assert client.get(f"/api/notes/{doc_id}/1").json()["stale_at"] is None


def test_scanner_uses_config_verify_by_default(tmp_path, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["x"])
    s = Scanner(Config(folders=[str(pdf_dir)], db_path=str(tmp_path / "s.db"), verify_checksums=True), None)
    s.run_blocking()
    assert s.state["last"]["mode"] == "verify"


def test_watcher_sees_modify_events():
    assert watch_relevant(SimpleNamespace(src_path="x.pdf", dest_path="", is_directory=False))
