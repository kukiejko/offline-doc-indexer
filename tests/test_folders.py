from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pdfindex.app import create_app
from pdfindex.config import load_config

from conftest import fake_ocr, make_text_pdf


@pytest.fixture
def setup(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    make_text_pdf(a / "one.pdf", ["alpha page"])
    make_text_pdf(b / "two.pdf", ["zebra page"])
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'# my settings\nfolders = ["a"]  # docs\nwatcher = false\ndb_path = "x.db"\n', encoding="utf-8")
    cfg = load_config(cfg_path)
    app = create_app(cfg, ocr=fake_ocr, watch=False)
    app.state.scanner.run_blocking()
    return TestClient(app, base_url="http://localhost"), cfg_path, b


def test_add_folder_persists_and_indexes(setup):
    client, cfg_path, b = setup
    r = client.post("/api/folders", json={"path": str(b)})
    assert r.status_code == 200 and r.json()["rescan_started"]
    text = cfg_path.read_text(encoding="utf-8")
    assert "# my settings" in text and "watcher = false" in text
    assert [Path(f).resolve() for f in load_config(cfg_path).folders] == [(cfg_path.parent / "a").resolve(), b.resolve()]
    client.app.state.scanner.run_blocking()
    assert client.get("/api/search", params={"q": "zebra"}).json()["count"] == 1
    assert len(client.get("/api/status").json()["folders"]) == 2


def test_add_folder_validation(setup, tmp_path):
    client, cfg_path, b = setup
    assert client.post("/api/folders", json={"path": str(tmp_path / "nope")}).status_code == 422
    assert client.post("/api/folders", json={"path": str(b / "two.pdf")}).status_code == 422
    assert client.post("/api/folders", json={"path": "  "}).status_code == 422
    assert client.post("/api/folders", json={"path": str(tmp_path / "a")}).status_code == 409
    assert client.post("/api/folders", json={"path": str(b)}, headers={"origin": "http://evil.example"}).status_code == 403


def test_favicon_served_and_linked(setup):
    client, _, _ = setup
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.content[:4] == b"\x00\x00\x01\x00"
    for page in ("/", "/static/viewer.html"):
        html = client.get(page).text
        assert 'href="/favicon.ico"' in html and "logo-32.png" in html
    assert client.get("/static/logo-32.png").content[:4] == b"\x89PNG"


def test_browse_folder_returns_pick_and_is_origin_protected(setup, monkeypatch):
    client, _, b = setup
    monkeypatch.setattr("pdfindex.picker.pick_folder", lambda: str(b))
    assert client.post("/api/folders/browse").json() == {"path": str(b)}
    monkeypatch.setattr("pdfindex.picker.pick_folder", lambda: "")
    assert client.post("/api/folders/browse").json() == {"path": ""}
    monkeypatch.setattr("pdfindex.picker.pick_folder", lambda: None)
    assert client.post("/api/folders/browse").status_code == 503
    assert client.post("/api/folders/browse", headers={"origin": "http://evil.example"}).status_code == 403


def test_remove_folder(setup, tmp_path):
    client, cfg_path, b = setup
    client.post("/api/folders", json={"path": str(b)})
    r = client.request("DELETE", "/api/folders", json={"path": str(b)})
    assert r.status_code == 200
    assert len(load_config(cfg_path).folders) == 1
    assert client.request("DELETE", "/api/folders", json={"path": str(b)}).status_code == 404


def test_add_folder_without_folders_key(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('db_path = "x.db"\n', encoding="utf-8")
    d = tmp_path / "d"
    d.mkdir()
    cfg = load_config(cfg_path)
    client = TestClient(create_app(cfg, ocr=fake_ocr, watch=False), base_url="http://localhost")
    assert client.post("/api/folders", json={"path": str(d)}).status_code == 200
    assert Path(load_config(cfg_path).folders[0]) == d.resolve()
