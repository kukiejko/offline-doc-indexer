import os

from pdfindex import app as app_mod
from pdfindex.app import Scanner
from pdfindex.config import Config
from pdfindex.db import connect, list_issues
from pdfindex.indexer import index_pdf, scan_folders
from pdfindex.notes import get_note, save_note

from conftest import fake_ocr, make_image_pdf, make_text_pdf


def _db_path(conn):
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def test_wal_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_note_can_be_saved_while_ocr_runs(conn, pdf_dir):
    p = pdf_dir / "i.pdf"
    make_image_pdf(p, "body text", "x")
    index_pdf(conn, p)  # no OCR yet; document row exists
    doc_id = conn.execute("SELECT id FROM documents").fetchone()["id"]
    other = connect(_db_path(conn))
    seen = []

    def slow_ocr(png):
        save_note(other, doc_id, 1, "written during OCR")
        seen.append(1)
        return "turbine"

    assert index_pdf(conn, p, ocr=slow_ocr) == "indexed"
    assert seen and get_note(conn, doc_id, 1) == "written during OCR"


def test_reindexed_when_ocr_becomes_available(conn, pdf_dir):
    p = pdf_dir / "i.pdf"
    make_image_pdf(p, "body text", "x")
    assert index_pdf(conn, p, ocr=None) == "indexed"
    assert index_pdf(conn, p, ocr=None) == "skipped"
    assert index_pdf(conn, p, ocr=fake_ocr) == "indexed"
    assert index_pdf(conn, p, ocr=fake_ocr) == "skipped"
    assert index_pdf(conn, p, ocr=fake_ocr, min_px=500) == "indexed"  # settings changed


def test_failed_ocr_is_retried_next_scan(conn, pdf_dir):
    p = pdf_dir / "i.pdf"
    make_image_pdf(p, "body text", "x")

    def boom(png):
        raise RuntimeError("down")

    index_pdf(conn, p, ocr=boom)
    assert index_pdf(conn, p, ocr=fake_ocr) == "indexed"
    assert list_issues(conn) == []  # stale issues cleared on reindex


def test_unchanged_errored_pdf_not_retried_and_issues_cleared_when_missing(conn, pdf_dir):
    bad = pdf_dir / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert index_pdf(conn, bad) == "error"
    assert index_pdf(conn, bad) == "skipped"
    assert len(list_issues(conn)) == 1
    os.remove(bad)
    scan_folders(conn, [pdf_dir])
    assert list_issues(conn) == []


def test_scan_requested_during_scan_reruns(tmp_path, monkeypatch):
    cfg = Config(folders=[str(tmp_path)], db_path=str(tmp_path / "s.db"))
    scanner = Scanner(cfg, None)
    calls = []

    def fake_scan(conn, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            assert scanner.start(queue=True) is False  # change arrives mid-scan
        return {}

    monkeypatch.setattr(app_mod.indexer, "scan_folders", fake_scan)
    scanner.run_blocking()
    assert len(calls) == 2 and not scanner.state["running"]


def test_scanner_close_cancels_timer(tmp_path):
    scanner = Scanner(Config(folders=[], db_path=str(tmp_path / "s.db")), None)
    scanner.schedule(delay=60)
    scanner.close()
    assert scanner._timer is None and scanner.start() is False
