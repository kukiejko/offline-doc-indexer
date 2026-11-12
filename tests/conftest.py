import fitz
import pytest

from pdfindex.db import connect


def _image_png(text):
    d = fitz.open()
    p = d.new_page(width=300, height=100)
    p.insert_text((10, 50), text, fontsize=20)
    return p.get_pixmap(dpi=100).tobytes("png")


@pytest.fixture
def conn(tmp_path):
    return connect(str(tmp_path / "t.db"))


@pytest.fixture
def pdf_dir(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    return d


def make_text_pdf(path, pages):
    doc = fitz.open()
    for t in pages:
        doc.new_page().insert_text((72, 100), t)
    doc.save(path)


def make_image_pdf(path, body, image_text):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), body)
    p.insert_image(fitz.Rect(72, 150, 372, 250), stream=_image_png(image_text))
    doc.save(path)


def make_scan_pdf(path, image_text):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=_image_png(image_text))
    doc.save(path)


def fake_ocr(png):
    return "recognized turbine schematic"
