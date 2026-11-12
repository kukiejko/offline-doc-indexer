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
    )
