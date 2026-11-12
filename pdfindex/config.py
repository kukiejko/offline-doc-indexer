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
    disabled_folders: list = field(default_factory=list)
    tesseract_path: str = ""
    min_ocr_image_px: int = 100
    watcher: bool = False
    verify_checksums: bool = False
    db_path: str = "pdfindex.db"
    file_types: list = field(default_factory=lambda: list(ALL_TYPES))
    config_path: str = ""


_LIST_RE = {k: re.compile(rf"^{k}\s*=\s*\[.*?\]", re.S | re.M) for k in ("folders", "disabled_folders")}


def _norm(p):
    return os.path.normcase(str(Path(p).resolve()))


def _save_list(path, key, entries):
    """Rewrite only one list entry of the config file, keeping all other lines and comments."""
    p = Path(path)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    line = f"{key} = [" + ", ".join(json.dumps(str(e).replace("\\", "/"), ensure_ascii=False) for e in entries) + "]"
    if _LIST_RE[key].search(text):
        text = _LIST_RE[key].sub(lambda m: line, text, count=1)
    else:
        text = line + "\n" + text
    p.write_text(text, encoding="utf-8")


def _raw_list(path, key):
    p = Path(path)
    return list(tomllib.loads(p.read_text(encoding="utf-8")).get(key, [])) if p.exists() else []


def _without(raw, target, config_path):
    base = Path(config_path).resolve().parent
    return [r for r in raw if _norm(r if Path(r).is_absolute() else base / r) != target]


def enabled_folders(cfg):
    off = {_norm(f) for f in cfg.disabled_folders}
    return [f for f in cfg.folders if _norm(f) not in off]


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
    _save_list(cfg.config_path, "folders", _raw_list(cfg.config_path, "folders") + [resolved])
    cfg.folders.append(resolved)
    return resolved


def remove_folder(cfg, folder):
    """Remove a folder from the config. Raises KeyError when it is not configured."""
    if not cfg.config_path:
        raise ValueError("No config file is in use, so the folder cannot be saved.")
    target = _norm(folder)
    if not any(_norm(f) == target for f in cfg.folders):
        raise KeyError("This folder is not in the list.")
    _save_list(cfg.config_path, "folders", _without(_raw_list(cfg.config_path, "folders"), target, cfg.config_path))
    cfg.folders[:] = [f for f in cfg.folders if _norm(f) != target]
    if any(_norm(f) == target for f in cfg.disabled_folders):
        _save_list(cfg.config_path, "disabled_folders",
                   _without(_raw_list(cfg.config_path, "disabled_folders"), target, cfg.config_path))
        cfg.disabled_folders[:] = [f for f in cfg.disabled_folders if _norm(f) != target]


def set_folder_enabled(cfg, folder, enabled):
    """Switch a configured folder on or off (persisted as `disabled_folders`). Raises KeyError when unknown."""
    if not cfg.config_path:
        raise ValueError("No config file is in use, so the setting cannot be saved.")
    target = _norm(folder)
    match = next((f for f in cfg.folders if _norm(f) == target), None)
    if match is None:
        raise KeyError("This folder is not in the list.")
    cfg.disabled_folders[:] = [f for f in cfg.disabled_folders if _norm(f) != target]
    if not enabled:
        cfg.disabled_folders.append(match)
    raw = _without(_raw_list(cfg.config_path, "disabled_folders"), target, cfg.config_path)
    _save_list(cfg.config_path, "disabled_folders", raw if enabled else raw + [match])


def load_config(path="config.toml"):
    p = Path(path)
    if not p.exists():
        return Config(config_path=str(p.resolve()))
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    base = p.resolve().parent

    def resolve(value):
        q = Path(value)
        return str(q if q.is_absolute() else base / q)

    return Config(
        folders=[resolve(f) for f in data.get("folders", [])],
        disabled_folders=[resolve(f) for f in data.get("disabled_folders", [])],
        tesseract_path=data.get("tesseract_path", ""),
        min_ocr_image_px=int(data.get("min_ocr_image_px", 100)),
        watcher=bool(data.get("watcher", False)),
        verify_checksums=bool(data.get("verify_checksums", False)),
        db_path=resolve(data.get("db_path", "pdfindex.db")),
        file_types=[t for t in (str(x).lower().lstrip(".") for x in data.get("file_types", ALL_TYPES)) if t in ALL_TYPES],
        config_path=str(p.resolve()),
    )
