import threading

import pytest
from fastapi.testclient import TestClient

from pdfindex.app import Scanner, create_app
from pdfindex.config import Config
from pdfindex.indexer import scan_folders

from conftest import fake_ocr, make_image_pdf, make_text_pdf


def test_page_level_progress_snapshots(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["one", "two", "three"])
    make_image_pdf(pdf_dir / "b.pdf", "body", "x")
    snaps = []
    scan_folders(conn, [pdf_dir], fake_ocr, report=snaps.append)
    assert snaps[0]["pages_total"] == 4 and snaps[0]["files_total"] == 2
    assert any(s["phase"] == "ocr" for s in snaps)
    last = snaps[-1]
    assert (last["pages_done"], last["files_done"], last["ocr_items"]) == (4, 2, 1)
    assert [s["pages_done"] for s in snaps] == sorted(s["pages_done"] for s in snaps)


def test_full_reindex_ignores_unchanged(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["one"])
    assert scan_folders(conn, [pdf_dir])["indexed"] == 1
    assert scan_folders(conn, [pdf_dir])["skipped"] == 1
    assert scan_folders(conn, [pdf_dir], force=True)["indexed"] == 1


def test_cancel_writes_nothing_for_unfinished_pdf(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["one", "two", "three"])
    cancel = threading.Event()
    report = lambda s: cancel.set() if s["pages_done"] >= 1 else None
    stats = scan_folders(conn, [pdf_dir], cancel=cancel, report=report)
    assert stats["cancelled"] and stats["indexed"] == 0
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 0
    assert scan_folders(conn, [pdf_dir])["indexed"] == 1


def test_single_scan_at_a_time_and_cancel(tmp_path, pdf_dir):
    make_image_pdf(pdf_dir / "a.pdf", "body", "x")
    gate, entered = threading.Event(), threading.Event()

    def slow_ocr(png):
        entered.set()
        gate.wait(5)
        return "text"

    scanner = Scanner(Config(folders=[str(pdf_dir)], db_path=str(tmp_path / "s.db")), slow_ocr)
    assert not scanner.cancel()
    assert scanner.start()
    assert entered.wait(5)
    assert not scanner.start()
    snap = scanner.snapshot()
    assert snap["running"] and snap["phase"] == "ocr" and snap["elapsed"] >= 0
    assert scanner.cancel() and scanner.state["cancelling"]
    gate.set()
    while scanner.state["running"]:
        threading.Event().wait(0.02)
    assert scanner.state["last"]["cancelled"] and scanner.state["last"]["indexed"] == 0


def test_status_has_summary_after_finish_and_mode_validation(tmp_path, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["alpha"])
    app = create_app(Config(folders=[str(pdf_dir)], db_path=str(tmp_path / "a.db")), ocr=None, watch=False)
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/rescan?mode=bogus").status_code == 422
    app.state.scanner.run_blocking(full=True)
    st = client.get("/api/status").json()
    assert not st["running"] and st["last"]["mode"] == "full" and st["last"]["indexed"] == 1
    assert st["last"]["duration"] >= 0 and st["last"]["pages"] == 1
    assert client.post("/api/scan/cancel").json() == {"cancelled": False}
