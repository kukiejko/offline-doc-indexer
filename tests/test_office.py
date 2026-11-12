import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from pdfindex import extract, indexer
from pdfindex.app import create_app, watch_relevant
from pdfindex.config import Config, load_config

from conftest import _image_png, fake_ocr

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
RELS = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'
REL_T = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _rel(i, typ, target):
    return f'<Relationship Id="{i}" Type="{REL_T}{typ}" Target="{target}"/>'


def make_docx(path, paras, image=None):
    body = ""
    for style, text in paras:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    rels = ""
    if image:
        body += '<w:p><w:r><w:drawing><a:blip r:embed="rId9"/></w:drawing></w:r></w:p>'
        rels = _rel("rId9", "image", "media/image1.png")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {W} {A} {R}><w:body>{body}</w:body></w:document>")
        z.writestr("word/_rels/document.xml.rels", RELS.format(rels))
        if image:
            z.writestr("word/media/image1.png", image)


def make_xlsx(path, sheets, rows_per_sheet=None):
    wb, rels, ss = "", "", []
    with zipfile.ZipFile(path, "w") as z:
        for i, (name, rows) in enumerate(sheets, 1):
            wb += f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
            rels += _rel(f"rId{i}", "worksheet", f"worksheets/sheet{i}.xml")
            xml = ""
            for r in rows:
                cells = ""
                for v in r:
                    if isinstance(v, str):
                        ss.append(v)
                        cells += f'<c t="s"><v>{len(ss) - 1}</v></c>'
                    else:
                        cells += f"<c><v>{v}</v></c>"
                xml += f"<row>{cells}</row>"
            z.writestr(f"xl/worksheets/sheet{i}.xml", f"<worksheet {S}><sheetData>{xml}</sheetData></worksheet>")
        z.writestr("xl/workbook.xml", f"<workbook {S} {R}><sheets>{wb}</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", RELS.format(rels))
        z.writestr("xl/sharedStrings.xml", f"<sst {S}>" + "".join(f"<si><t>{s}</t></si>" for s in ss) + "</sst>")


def make_pptx(path, slides, image=None):
    ids, rels = "", ""
    with zipfile.ZipFile(path, "w") as z:
        for i, (text, notes) in enumerate(slides, 1):
            ids += f'<p:sldId id="{255 + i}" r:id="rId{i}"/>'
            rels += _rel(f"rId{i}", "slide", f"slides/slide{i}.xml")
            pic = '<a:blip r:embed="rId5"/>' if image and i == 1 else ""
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld {A} {R} xmlns:p='p'><a:p><a:r><a:t>{text}</a:t></a:r></a:p>{pic}</p:sld>")
            srels = ""
            if image and i == 1:
                srels += _rel("rId5", "image", "../media/image1.png")
            if notes:
                srels += _rel("rId6", "notesSlide", f"../notesSlides/notesSlide{i}.xml")
                z.writestr(f"ppt/notesSlides/notesSlide{i}.xml", f"<p:notes {A} xmlns:p='p'><a:p><a:r><a:t>{notes}</a:t></a:r></a:p></p:notes>")
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", RELS.format(srels))
        z.writestr("ppt/presentation.xml", f"<p:presentation {R} xmlns:p='p'><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
        z.writestr("ppt/_rels/presentation.xml.rels", RELS.format(rels))
        if image:
            z.writestr("ppt/media/image1.png", image)


def pages(conn, doc_id, source="text"):
    return {r["page"]: r["content"] for r in conn.execute(
        "SELECT page, content FROM pages_fts WHERE doc_id=? AND source=?", (doc_id, source))}


def doc_row(conn, name):
    return conn.execute("SELECT * FROM documents WHERE path LIKE ?", ("%" + name,)).fetchone()


@pytest.fixture
def office_dir(pdf_dir):
    make_docx(pdf_dir / "report.docx", [("Heading1", "Intro"), (None, "alpha baseline text"),
                                        ("Heading1", "Results"), (None, "gamma turbine yield")])
    make_xlsx(pdf_dir / "data.xlsx", [("Wafers", [["lot", "delta"], ["L1", 5]]), ("Notes", [["epitaxy remark"]])])
    make_pptx(pdf_dir / "deck.pptx", [("first slide title", ""), ("second slide", "speaker mentions cryostat")])
    (pdf_dir / "log.txt").write_text("\n".join(f"line {i}" for i in range(200)) + "\nneedle in haystack", encoding="utf-8")
    (pdf_dir / "readme.md").write_text("# Title\nmarkdown sentinel", encoding="utf-8")
    return pdf_dir


def test_each_format_is_indexed_into_units(conn, office_dir):
    stats = indexer.scan_folders(conn, [str(office_dir)])
    assert stats["indexed"] == 5 and stats["error"] == 0
    d = doc_row(conn, "report.docx")
    assert d["doc_type"] == "docx" and d["pages"] == 2
    assert "alpha baseline" in pages(conn, d["id"])[1] and "gamma turbine" in pages(conn, d["id"])[2]
    x = doc_row(conn, "data.xlsx")
    assert x["doc_type"] == "xlsx" and x["pages"] == 2
    assert pages(conn, x["id"])[1].startswith("Sheet: Wafers") and "L1\t5" in pages(conn, x["id"])[1]
    p = doc_row(conn, "deck.pptx")
    assert p["pages"] == 2 and "cryostat" in pages(conn, p["id"])[2]
    t = doc_row(conn, "log.txt")
    assert t["pages"] == 3 and "needle" in pages(conn, t["id"])[3]
    assert doc_row(conn, "readme.md")["doc_type"] == "md"


def test_embedded_images_are_ocrd(conn, pdf_dir):
    img = _image_png("hello")
    make_docx(pdf_dir / "a.docx", [(None, "body")], image=img)
    make_pptx(pdf_dir / "b.pptx", [("slide", "")], image=img)
    indexer.scan_folders(conn, [str(pdf_dir)], ocr=fake_ocr, min_px=50)
    for name in ("a.docx", "b.pptx"):
        d = doc_row(conn, name)
        assert "recognized turbine" in pages(conn, d["id"], "image_ocr")[1]


def test_file_types_config_and_lock_and_legacy(conn, office_dir):
    (office_dir / "~$report.docx").write_bytes(b"lock")
    (office_dir / "old.doc").write_bytes(b"x")
    (office_dir / "old.xls").write_bytes(b"x")
    names = {p.name for p in indexer.list_files([str(office_dir)])}
    assert "~$report.docx" not in names and "old.doc" not in names and len(names) == 5
    assert {p.name for p in indexer.list_files([str(office_dir)], ["txt", "md"])} == {"log.txt", "readme.md"}
    stats = indexer.scan_folders(conn, [str(office_dir)])
    assert stats["unsupported"] == 2
    msgs = [r["message"] for r in conn.execute("SELECT message FROM issues")]
    assert sum(m.startswith("Unsupported format") for m in msgs) == 2
    (office_dir / "old.doc").unlink()
    indexer.scan_folders(conn, [str(office_dir)])
    assert conn.execute("SELECT COUNT(*) FROM issues WHERE message LIKE 'Unsupported%'").fetchone()[0] == 1


def test_config_file_types(tmp_path):
    (tmp_path / "config.toml").write_text('file_types = ["PDF", ".docx", "exe"]', encoding="utf-8")
    assert load_config(str(tmp_path / "config.toml")).file_types == ["pdf", "docx"]
    assert load_config(str(tmp_path / "none.toml")).file_types == extract.ALL_TYPES


def test_zip_bomb_and_corrupt_files_are_rejected(conn, pdf_dir):
    with zipfile.ZipFile(pdf_dir / "bomb.docx", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (30 * 1024 * 1024))
    (pdf_dir / "bad.xlsx").write_bytes(b"not a zip")
    stats = indexer.scan_folders(conn, [str(pdf_dir)])
    assert stats["error"] == 2
    msgs = " ".join(r["message"] for r in conn.execute("SELECT message FROM issues"))
    assert "zip bomb" in msgs and "cannot open" in msgs


def test_xml_entity_bombs_are_refused(conn, pdf_dir):
    evil = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><w:document %s><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>' % W
    with zipfile.ZipFile(pdf_dir / "evil.docx", "w") as z:
        z.writestr("word/document.xml", evil)
    assert indexer.scan_folders(conn, [str(pdf_dir)])["error"] == 1


def test_size_cap(conn, pdf_dir, monkeypatch):
    monkeypatch.setattr(extract, "MAX_TEXT_BYTES", 10)
    (pdf_dir / "big.txt").write_text("x" * 100)
    assert indexer.scan_folders(conn, [str(pdf_dir)])["error"] == 1
    assert indexer.scan_folders(conn, [str(pdf_dir)])["skipped"] == 1
    assert "too large" in conn.execute("SELECT message FROM issues").fetchone()[0]


def test_sheet_row_cap_warns(conn, pdf_dir, monkeypatch):
    monkeypatch.setattr(extract, "MAX_SHEET_ROWS", 3)
    make_xlsx(pdf_dir / "big.xlsx", [("S", [[f"row{i}"] for i in range(10)])])
    indexer.scan_folders(conn, [str(pdf_dir)])
    d = doc_row(conn, "big.xlsx")
    assert d["status"] == "ok" and "row2" in pages(conn, d["id"])[1] and "row5" not in pages(conn, d["id"])[1]
    assert conn.execute("SELECT message FROM issues").fetchone()[0].startswith("Warning: sheet 'S' is large")


def test_rename_and_modify_keep_doc_id_and_notes(conn, office_dir):
    indexer.scan_folders(conn, [str(office_dir)])
    d = doc_row(conn, "deck.pptx")
    conn.execute("INSERT INTO notes (doc_id,page,text,updated_at) VALUES (?,?,?,0)", (d["id"], 2, "n"))
    conn.commit()
    (office_dir / "deck.pptx").rename(office_dir / "sub.pptx")
    assert indexer.scan_folders(conn, [str(office_dir)])["moved"] == 1
    assert doc_row(conn, "sub.pptx")["id"] == d["id"]
    make_pptx(office_dir / "sub.pptx", [("completely new", "")])
    stats = indexer.scan_folders(conn, [str(office_dir)], verify=True)
    assert stats["modified"] == 1
    assert doc_row(conn, "sub.pptx")["id"] == d["id"]
    assert conn.execute("SELECT stale_at FROM notes WHERE doc_id=?", (d["id"],)).fetchone()[0] is not None


def test_watch_relevant_types(tmp_path):
    class E:
        is_directory = False
        src_path = str(tmp_path / "a.docx")
        dest_path = ""
    assert watch_relevant(E())
    assert not watch_relevant(E(), ["pdf"])
    E.src_path = str(tmp_path / "~$a.docx")
    assert not watch_relevant(E())
    E.src_path = str(tmp_path / "a.exe")
    assert not watch_relevant(E())


@pytest.fixture
def client(office_dir, tmp_path):
    cfg = Config(folders=[str(office_dir)], db_path=str(tmp_path / "app.db"))
    app = create_app(cfg, watch=False)
    app.state.scanner.run_blocking()
    return TestClient(app, base_url="http://127.0.0.1")


def test_search_type_filter_and_badges(client):
    r = client.get("/api/search", params={"q": "cryostat"}).json()
    assert r["groups"][0]["type"] == "pptx"
    assert client.get("/api/search", params={"q": "cryostat", "types": "pdf,docx"}).json()["count"] == 0
    assert client.get("/api/search", params={"q": "cryostat", "types": "pptx"}).json()["count"] == 1
    assert client.get("/api/search", params={"q": "x", "types": "exe"}).status_code == 422
    assert client.get("/api/status").json()["type_labels"]["xlsx"] == "Excel"


def test_units_and_original_file(client, office_dir):
    docs = {d["path"].split("\\")[-1].split("/")[-1]: d for d in client.get("/api/docs").json()}
    d = docs["report.docx"]
    info = client.get(f"/api/docs/{d['id']}").json()
    assert info["type"] == "docx" and info["unit_name"] == "Section" and info["pages"] == 2
    units = client.get(f"/api/units/{d['id']}").json()
    assert [u["page"] for u in units] == [1, 2]
    f = client.get(f"/api/file/{d['id']}")
    assert f.status_code == 200 and "attachment" in f.headers["content-disposition"] and "report.docx" in f.headers["content-disposition"]
    assert f.content == (office_dir / "report.docx").read_bytes()


def test_original_file_refuses_paths_outside_folders(client, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    from pdfindex.db import connect
    conn = connect(client.app.state.scanner.cfg.db_path)
    conn.execute("UPDATE documents SET path=? WHERE doc_type='txt'", (str(outside),))
    conn.commit()
    did = conn.execute("SELECT id FROM documents WHERE doc_type='txt'").fetchone()[0]
    assert client.get(f"/api/file/{did}").status_code == 404
    assert client.get("/api/file/9999").status_code == 404
