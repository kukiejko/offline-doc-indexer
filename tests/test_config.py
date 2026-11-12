from pathlib import Path

from pdfindex.config import load_config


def test_relative_paths_resolve_against_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "coll"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text('folders = ["pdfs"]\ndb_path = "pdfindex.db"\n', encoding="utf-8")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    cfg = load_config(cfg_dir / "config.toml")
    assert Path(cfg.db_path) == (cfg_dir / "pdfindex.db").resolve()
    assert Path(cfg.folders[0]) == (cfg_dir / "pdfs").resolve()


def test_absolute_paths_kept(tmp_path):
    db = tmp_path / "x.db"
    (tmp_path / "config.toml").write_text(f'db_path = "{db.as_posix()}"\n', encoding="utf-8")
    assert Path(load_config(tmp_path / "config.toml").db_path) == db
