import shutil

from pdfindex import notes as notes_mod
from pdfindex.indexer import scan_folders
from pdfindex.search import group_by_document, search

from conftest import make_text_pdf


def _two_copies(pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["overfitting alpha"])
    (pdf_dir / "sub").mkdir()
    shutil.copy(pdf_dir / "a.pdf", pdf_dir / "sub" / "copy.pdf")


def test_identical_file_is_indexed_once_with_extra_path(conn, pdf_dir):
    _two_copies(pdf_dir)
    stats = scan_folders(conn, [pdf_dir])
    assert (stats["indexed"], stats["duplicates"]) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    groups = group_by_document(search(conn, "overfitting"))
    assert len(groups) == 1 and len(groups[0]["paths"]) == 2
    # stable on rescan; prefix filter also finds the copy's location
    stats = scan_folders(conn, [pdf_dir])
    assert (stats["indexed"], stats["duplicates"], stats["removed"]) == (0, 0, 0)
    assert len(search(conn, "overfitting", path_prefix=str(pdf_dir / "sub"))) == 1


def test_existing_separate_duplicates_are_merged_with_notes(conn, pdf_dir):
    _two_copies(pdf_dir)
    scan_folders(conn, [pdf_dir])
    # simulate an index made before deduplication: two rows for the same content
    row = conn.execute("SELECT * FROM documents").fetchone()
    copy = pdf_dir / "sub" / "copy.pdf"
    conn.execute("DELETE FROM doc_paths")
    conn.execute("INSERT INTO documents (path,size,mtime,hash,pages,status,ocr_sig,ocr_errors,doc_type) VALUES (?,?,?,?,?,?,?,?,?)",
                 (str(copy), row["size"], copy.stat().st_mtime, row["hash"], 1, "ok", row["ocr_sig"], 0, "pdf"))
    conn.commit()
    second = conn.execute("SELECT id FROM documents WHERE path=?", (str(copy),)).fetchone()["id"]
    notes_mod.save_note(conn, second, 1, "note on the copy")
    stats = scan_folders(conn, [pdf_dir])
    assert stats["duplicates"] == 1
    docs = conn.execute("SELECT id FROM documents").fetchall()
    assert [d["id"] for d in docs] == [second]  # the one with notes survives
    assert notes_mod.get_note(conn, second, 1) == "note on the copy"
    assert conn.execute("SELECT COUNT(*) FROM doc_paths").fetchone()[0] == 1


def test_main_path_deleted_promotes_copy(conn, pdf_dir):
    _two_copies(pdf_dir)
    scan_folders(conn, [pdf_dir])
    (pdf_dir / "a.pdf").unlink()
    stats = scan_folders(conn, [pdf_dir])
    assert (stats["removed"], stats["indexed"]) == (0, 0)
    d = conn.execute("SELECT * FROM documents").fetchone()
    assert d["status"] == "ok" and d["path"].endswith("copy.pdf")
    assert len(group_by_document(search(conn, "overfitting"))[0]["paths"]) == 1


def test_deleted_copy_is_forgotten(conn, pdf_dir):
    _two_copies(pdf_dir)
    scan_folders(conn, [pdf_dir])
    (pdf_dir / "sub" / "copy.pdf").unlink()
    scan_folders(conn, [pdf_dir])
    assert conn.execute("SELECT COUNT(*) FROM doc_paths").fetchone()[0] == 0
