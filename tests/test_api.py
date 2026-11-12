import pytest
from fastapi.testclient import TestClient

from pdfindex.app import create_app
from pdfindex.config import Config

from conftest import fake_ocr, make_image_pdf, make_text_pdf


@pytest.fixture
def client(tmp_path, pdf_dir):
    make_text_pdf(pdf_dir / "a.pdf", ["alpha page", "beta page about valves"])
    make_image_pdf(pdf_dir / "b.pdf", "plain body", "x")
    cfg = Config(folders=[str(pdf_dir)], db_path=str(tmp_path / "app.db"))
    app = create_app(cfg, ocr=fake_ocr, watch=False)
    app.state.scanner.run_blocking()
    return TestClient(app, base_url="http://localhost")


def test_security_headers_and_host_check(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400


def test_cross_origin_mutations_rejected(client):
    body = {"text": "x"}
    assert client.put("/api/notes/1/1", json=body, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.put("/api/notes/1/1", json=body, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/rescan", headers={"origin": "http://evil.example"}).status_code == 403
    ok = client.put("/api/notes/1/1", json=body, headers={"origin": "http://localhost", "sec-fetch-site": "same-origin"})
    assert ok.status_code == 200


def test_note_html_is_escaped_in_snippets(client):
    client.put("/api/notes/1/1", json={"text": "<img src=x onerror=alert(1)> zebra"})
    snip = client.get("/api/search", params={"q": "zebra"}).json()["groups"][0]["hits"][0]["snippet"]
    assert "<img" not in snip and "&lt;img" in snip and "<mark>zebra</mark>" in snip


def test_symlinked_pdf_not_served(tmp_path, pdf_dir, client):
    outside = tmp_path / "outside.pdf"
    make_text_pdf(outside, ["secret"])
    link = pdf_dir / "link.pdf"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted")
    client.app.state.scanner.run_blocking()
    assert client.get("/api/search", params={"q": "secret"}).json()["count"] == 0


def test_rescan_status_and_search(client):
    st = client.get("/api/status").json()
    assert st["documents"] == 2 and st["last"]["indexed"] == 2
    data = client.get("/api/search", params={"q": "valves"}).json()
    hit = data["groups"][0]["hits"][0]
    assert hit["page"] == 2 and hit["badge"] == "Text"
    assert client.get("/api/search", params={"q": "turbine", "source": "image"}).json()["count"] == 1


def test_file_filter_only_and_exclude(client):
    docs = client.get("/api/docs").json()
    a = next(d["id"] for d in docs if d["path"].endswith("a.pdf"))
    b = next(d["id"] for d in docs if d["path"].endswith("b.pdf"))
    count = lambda **p: client.get("/api/search", params={"q": "valves", **p}).json()["count"]
    assert count() == 1
    assert count(docs=str(a), mode="only") == 1
    assert count(docs=str(b), mode="only") == 0
    assert count(docs=str(a), mode="exclude") == 0
    assert count(docs=f"{b},{b}", mode="exclude") == 1
    assert count(docs="") == 1


def test_file_filter_rejects_bad_input(client):
    s = lambda **p: client.get("/api/search", params={"q": "valves", **p}).status_code
    assert s(docs="1;DROP TABLE documents") == 422
    assert s(docs="1", mode="bogus") == 422
    assert s(docs=",".join(map(str, range(6000)))) == 422
    assert s(docs=",".join(map(str, range(4000))), mode="exclude") == 200

    import re
    from pathlib import Path

    for f in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        r = client.get(f"/static/vendor/{f}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    static = Path(__file__).parent.parent / "pdfindex" / "static"
    for page in ("index.html", "viewer.html", "index.js", "viewer.js", "app.css"):
        text = (static / page).read_text(encoding="utf-8")
        assert not re.search(r"""(src|href|from|import|url)\s*[=(]?\s*['"]?(https?:)?//""", text), page
        assert "<script>" not in text and " style=" not in text, page


def test_pdf_streaming_and_unknown_doc(client):
    r = client.get("/api/pdf/1")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get("/api/pdf/999").status_code == 404


def test_notes_api_and_viewer(client):
    assert client.put("/api/notes/1/2", json={"text": "check valve spec"}).status_code == 200
    assert client.get("/api/notes/1/2").json()["text"] == "check valve spec"
    assert client.get("/api/docs/1").json()["note_pages"] == [2]
    assert client.get("/api/search", params={"q": "spec", "source": "note"}).json()["count"] == 1
    assert len(client.get("/api/notes").json()) == 1
    assert client.get("/view/1?page=2&q=valves").status_code == 200
    viewer = client.get("/view/1?page=2&q=valves").text
    assert 'id="back"' in viewer and "Back to search" in viewer
    assert "pdfindex.lastSearch" in client.get("/static/index.js").text
    home = client.get("/").text
    assert 'id="split"' in home and 'role="separator"' in home and 'id="pdfview"' in home
    js = client.get("/static/index.js").text
    assert 'id="filesbtn"' in home and 'name="fmode"' in home
    assert 'hitOrder' in js and 'id="hitorder"' in home
    assert 'File filter' in js and 'pdfindex.filesOpen' in js and "classList.toggle('active'" in js and '#filesbtn.active' in client.get('/static/app.css').text
    assert "embed=1" in js and "pdfindex.split" in js
    assert "embed" in client.get("/static/viewer.js").text
    assert 'id="foldall"' in home and 'id="unfoldall"' in home
    assert 'id="sort"' in home and "Most hits first" in home and "File name Z" in home
    assert "opened" in js and "SORTS" in js and "sort: sortKey" in js
    bat = (__import__("pathlib").Path(__file__).parent.parent / "scripts" / "start.bat").read_text()
    assert "-m pdfindex serve --config" in bat and "PORT" in bat and "PDFINDEX_HOME" in bat and "C:\\" not in bat and "pip install -r requirements.txt" in bat
    assert 'aria-expanded' in js and 'aria-controls' in js and "Show all" in js
    assert client.get("/api/ocr/2/1").json()[0]["source"] == "image_ocr"


def test_version_changelog_and_cli(client):
    import re
    import subprocess
    import sys
    from pdfindex.__version__ import DESIGNER, __version__, RELEASE_TIMESTAMP
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    v = client.get("/api/version").json()
    assert v["version"] == __version__ and v["released"] == RELEASE_TIMESTAMP and v["designer"] == "Andrzej Grzegorczyk" == DESIGNER
    log = client.get("/api/changelog").text
    assert f"## [{__version__}]" in log
    assert "README" in client.get("/").text and 'id="helpdlg"' in client.get("/").text
    out = subprocess.run([sys.executable, "-m", "pdfindex", "--version"], capture_output=True, text=True).stdout
    assert __version__ in out and DESIGNER in out


