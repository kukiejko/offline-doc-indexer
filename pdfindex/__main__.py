import argparse

import uvicorn

from .__version__ import DESIGNER, RELEASE_TIMESTAMP, __version__
from .config import load_config
from .db import connect
from .indexer import scan_folders
from .ocr import make_ocr


def main():
    ap = argparse.ArgumentParser(prog="pdfindex")
    ap.add_argument("command", choices=["serve", "scan"])
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--verify", action="store_true", help="scan: verify checksums of all files")
    ap.add_argument("--version", action="version", version=f"PDF Indexer {__version__} ({RELEASE_TIMESTAMP}), designed by {DESIGNER}")
    a = ap.parse_args()
    cfg = load_config(a.config)
    ocr = make_ocr(cfg.tesseract_path)
    if ocr is None:
        print("WARNING: Tesseract not found; text inside images will NOT be indexed. See README.")
    if a.command == "scan":
        conn = connect(cfg.db_path)
        print(scan_folders(conn, cfg.folders, ocr, cfg.min_ocr_image_px,
                           lambda i, n, c: print(f"[{i}/{n}] {c}") if c else None,
                           verify=a.verify or cfg.verify_checksums))
        return
    from .app import create_app

    uvicorn.run(create_app(cfg, ocr), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()

