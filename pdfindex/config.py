import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .extract import ALL_TYPES


@dataclass
class Config:
    folders: list = field(default_factory=list)
    tesseract_path: str = ""
    min_ocr_image_px: int = 100
    watcher: bool = False
    verify_checksums: bool = False
    db_path: str = "pdfindex.db"
    file_types: list = field(default_factory=lambda: list(ALL_TYPES))
    config_path: str = ""


_FOLDERS_RE = re.compile(r"^folders\s*=\s*\[.*?\]", re.S | re.M)


def _norm(p):
    return os.path.normcase(str(Path(p).resolve()))


def save_folders(path, entries):
    """Rewrite only the `folders` entry of the config file, keeping all other lines and comments."""
    p = Path(path)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    line = "folders = [" + ", ".join(json.dumps(str(e).replace("\\", "/"), ensure_ascii=False) for e in entries) + "]"
    if _FOLDERS_RE.search(text):
        text = _FOLDERS_RE.sub(lambda m: line, text, count=1)
    else:
        text = line + "\n" + text
    p.write_text(text, encoding="utf-8")


def _raw_folders(path):
    p = Path(path)
    return list(tomllib.loads(p.read_text(encoding="utf-8")).get("folders", [])) if p.exists() else []


def add_folder(cfg, folder):
    """Validate, persist and activate a new document folder. Raises ValueError (invalid) or KeyError (duplicate)."""
    folder = str(folder).strip()
    if not folder:
        raise ValueError("Enter a folder path.")
    if not cfg.config_path:
        raise ValueError("No config file is in use, so the folder cannot be saved.")
    q = Path(folder)
    if not q.is_absolute():
        raise ValueError("Enter the full path of the folder (for example C:\\Docs).")
    if not q.is_dir():
        raise ValueError("This folder does not exist or is not a folder.")
    resolved = str(q.resolve())
    if any(_norm(f) == _norm(resolved) for f in cfg.folders):
        raise KeyError("This folder is already indexed.")
    save_folders(cfg.config_path, _raw_folders(cfg.config_path) + [resolved])
    cfg.folders.append(resolved)
    return resolved


def remove_folder(cfg, folder):
    """Remove a folder from the config. Raises KeyError when it is not configured."""
    if not cfg.config_path:
        raise ValueError("No config file is in use, so the folder cannot be saved.")
    target = _norm(folder)
    if not any(_norm(f) == target for f in cfg.folders):
        raise KeyError("This folder is not in the list.")
    base = Path(cfg.config_path).resolve().parent
    kept = [r for r in _raw_folders(cfg.config_path)
            if _norm(r if Path(r).is_absolute() else base / r) != target]
    save_folders(cfg.config_path, kept)
    cfg.folders[:] = [f for f in cfg.folders if _norm(f) != target]


def load_config(path="config.toml"):
    p = Path(path)
    if not p.exists():
        return Config()
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    base = p.resolve().parent

    def resolve(value):
        q = Path(value)
        return str(q if q.is_absolute() else base / q)

    return Config(
        folders=[resolve(f) for f in data.get("folders", [])],
        tesseract_path=data.get("tesseract_path", ""),
        min_ocr_image_px=int(data.get("min_ocr_image_px", 100)),
        watcher=bool(data.get("watcher", False)),
        verify_checksums=bool(data.get("verify_checksums", False)),
        db_path=resolve(data.get("db_path", "pdfindex.db")),
        file_types=[t for t in (str(x).lower().lstrip(".") for x in data.get("file_types", ALL_TYPES)) if t in ALL_TYPES],
                config_path=str(p.resolve()),
    )
