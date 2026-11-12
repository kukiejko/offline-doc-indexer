import os

from pdfindex.indexer import index_pdf, scan_folders
from pdfindex.db import list_issues
from pdfindex.notes import get_note, list_notes, pages_with_notes, save_note
from pdfindex.search import group_by_document, search

from conftest import fake_ocr, make_image_pdf, make_scan_pdf, make_text_pdf


def test_text_indexing_and_page_numbers(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["first page intro", "second page about compressors"])
    assert index_pdf(conn, pdf_dir / "a.pdf") == "indexed"
    r = search(conn, "compressors")
    assert [(x["page"], x["badge"]) for x in r] == [(2, "Text")]
    assert "<mark>" in r[0]["snippet"]


def test_rescan_skips_unchanged_and_reindexes_changed(conn, pdf_dir):
    p = pdf_dir / "a.pdf"
    make_text_pdf(p, ["alpha"])
    assert scan_folders(conn, [pdf_dir])["indexed"] == 1
    assert scan_folders(conn, [pdf_dir])["skipped"] == 1
    make_text_pdf(p, ["bravo"])
    os.utime(p, (1, 1))
    assert scan_folders(conn, [pdf_dir])["indexed"] == 1
    assert search(conn, "alpha") == []
    assert len(search(conn, "bravo")) == 1


def test_phrase_and_stemming(conn, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["the pumps are running fast", "running pumps"])
    index_pdf(conn, pdf_dir / "a.pdf")
    assert {x["page"] for x in search(conn, "pump run")} == {1, 2}
    assert [x["page"] for x in search(conn, '"pumps are running"')] == [1]


def test_image_ocr_source_and_bbox(conn, pdf_dir):
    make_image_pdf(pdf_dir / "i.pdf", "body text", "x")
    index_pdf(conn, pdf_dir / "i.pdf", ocr=fake_ocr)
    r = search(conn, "turbine")
    assert r[0]["badge"] == "Image" and r[0]["source"] == "image_ocr" and r[0]["bbox"]


def test_scanned_page_uses_page_ocr(conn, pdf_dir):
    make_scan_pdf(pdf_dir / "s.pdf", "x")
    index_pdf(conn, pdf_dir / "s.pdf", ocr=fake_ocr)
    assert search(conn, "schematic")[0]["source"] == "page_ocr"


def test_corrupt_pdf_is_logged_not_fatal(conn, pdf_dir):
    (pdf_dir / "bad.pdf").write_bytes(b"not a pdf")
    make_text_pdf(pdf_dir / "ok.pdf", ["fine"])
    stats = scan_folders(conn, [pdf_dir])
    assert stats["error"] == 1 and stats["indexed"] == 1
    assert "bad.pdf" in list_issues(conn)[0]["path"]


def test_ocr_failure_is_logged(conn, pdf_dir):
    make_image_pdf(pdf_dir / "i.pdf", "body text", "x")

    def boom(png):
        raise RuntimeError("tess died")

    assert index_pdf(conn, pdf_dir / "i.pdf", ocr=boom) == "indexed"
    assert len(search(conn, "body")) == 1
    assert "OCR failed" in list_issues(conn)[0]["message"]


def test_notes_create_update_search_and_survive_reindex(conn, pdf_dir):
    p = pdf_dir / "a.pdf"
    make_text_pdf(p, ["alpha"])
    index_pdf(conn, p)
    doc_id = conn.execute("SELECT id FROM documents").fetchone()["id"]
    save_note(conn, doc_id, 1, "check torque value")
    assert search(conn, "torque")[0]["badge"] == "Note"
    save_note(conn, doc_id, 1, "verify pressure")
    assert search(conn, "torque") == [] and len(search(conn, "pressure")) == 1
    make_text_pdf(p, ["gamma"])
    os.utime(p, (1, 1))
    index_pdf(conn, p)
    assert get_note(conn, doc_id, 1) == "verify pressure"
    assert len(search(conn, "pressure")) == 1
    assert pages_with_notes(conn, doc_id) == [1] and len(list_notes(conn)) == 1
    save_note(conn, doc_id, 1, "")
    assert search(conn, "pressure") == [] and list_notes(conn) == []


def test_source_filter_and_grouping(conn, pdf_dir):
    make_image_pdf(pdf_dir / "i.pdf", "turbine manual", "x")
    index_pdf(conn, pdf_dir / "i.pdf", ocr=fake_ocr)
    assert len(search(conn, "turbine")) == 2
    assert len(search(conn, "turbine", sources=["image"])) == 1
    assert len(group_by_document(search(conn, "turbine"))) == 1


def test_search_input_is_sanitised(conn):
    assert search(conn, 'AND OR " ( * NEAR') is not None
