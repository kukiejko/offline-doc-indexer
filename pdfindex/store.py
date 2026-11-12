"""Per-folder index databases.

Every configured folder keeps its own SQLite index (`docindex.db` inside the folder), so a collection can be
moved, backed up or shared together with its notes. Documents are addressed across folders by a global id:
`slot * SLOT + local_id`, where slot is the folder's position in the config (slot 0 keeps the plain local ids)."""
import os
from pathlib import Path

from . import indexer
from .config import enabled_folders
from .db import connect

DB_NAME = "docindex.db"
SLOT = 10 ** 9
COUNTERS = ("files_done", "files_total", "pages_done", "pages_total", "ocr_items", "issues", "errors")


def gid(slot, local_id):
    return slot * SLOT + local_id


def split_gid(g):
    return divmod(g, SLOT)


def db_file(folder):
    return Path(folder) / DB_NAME


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


def _under(path, root):
    return _norm(path).startswith(root.rstrip("\\/") + os.sep)


def migrate_legacy(conn, legacy, folder):
    """Copy the part of an old single-database index that belongs to `folder` (ids are kept). The old file is not modified."""
    root = _norm(folder)
    src = connect(legacy)
    try:
        with conn:
            for d in src.execute("SELECT * FROM documents").fetchall():
                if not _under(d["path"], root):
                    continue
                cols = d.keys()
                conn.execute(f"INSERT INTO documents ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(d))
                conn.executemany(
                    "INSERT INTO pages_fts (doc_id,page,source,bbox,content) VALUES (?,?,?,?,?)",
                    [tuple(r) for r in src.execute(
                        "SELECT doc_id,page,source,bbox,content FROM pages_fts WHERE doc_id=?", (d["id"],))])
                conn.executemany(
                    "INSERT INTO notes (doc_id,page,text,updated_at,stale_at) VALUES (?,?,?,?,?)",
                    [tuple(r) for r in src.execute(
                        "SELECT doc_id,page,text,updated_at,stale_at FROM notes WHERE doc_id=?", (d["id"],))])
            conn.executemany(
                "INSERT INTO issues (path,page,message,created_at) VALUES (?,?,?,?)",
                [tuple(r) for r in src.execute("SELECT path,page,message,created_at FROM issues").fetchall()
                 if _under(r["path"], root)])
    finally:
        src.close()


def open_folder_db(folder, legacy_db=None):
    """Open (creating if needed) the folder's index. A brand-new database is seeded from the legacy single database."""
    path = db_file(folder)
    fresh = not path.exists()
    conn = connect(path)
    if fresh and legacy_db and Path(legacy_db).is_file():
        try:
            migrate_legacy(conn, legacy_db, folder)
        except Exception:
            conn.close()
            path.unlink(missing_ok=True)
            raise
    return conn


def scan_all(cfg, ocr, progress=None, force=False, cancel=None, report=None, verify=False):
    """Scan every configured folder into its own database; progress counters are summed across folders.
    Returns (merged stats, list of per-folder error messages)."""
    total = {"indexed": 0, "skipped": 0, "error": 0, "removed": 0, "moved": 0, "modified": 0,
             "modified_files": [], "cancelled": False, "unsupported": 0}
    base = dict.fromkeys(COUNTERS, 0)
    errors = []
    for folder in enabled_folders(cfg):
        if cancel is not None and cancel.is_set():
            total["cancelled"] = True
            break
        if not Path(folder).is_dir():
            errors.append(f"{folder}: folder not found")
            continue
        last = {}

        def rep(snap, base=dict(base), last=last):
            last.update(snap)
            if report:
                report({k: v + base[k] if k in base else v for k, v in snap.items()})

        try:
            conn = open_folder_db(folder, cfg.db_path)
        except Exception as e:
            errors.append(f"{folder}: cannot open index database ({e})")
            continue
        try:
            stats = indexer.scan_folders(conn, [folder], ocr, cfg.min_ocr_image_px, progress, force=force,
                                         cancel=cancel, report=rep, verify=verify, file_types=cfg.file_types)
        finally:
            conn.close()
        for k, v in stats.items():
            if k == "modified_files":
                total[k] += v
            elif k == "cancelled":
                total[k] = total[k] or v
            elif isinstance(v, int):
                total[k] = total.get(k, 0) + v
        for k in COUNTERS:
            base[k] += last.get(k, 0)
        if stats.get("cancelled"):
            break
    return total, errors
