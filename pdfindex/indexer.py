import hashlib
import json
import time
from pathlib import Path

import fitz

from .db import add_issue, clear_issues, log_issue
from .extract import ALL_TYPES, LEGACY_EXTS, doc_type, open_units, size_limit
from .search import strip_sentinels


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ocr_signature(ocr, min_px):
    return f"ocr:v1:{min_px}" if ocr is not None else "none"


class Cancelled(Exception):
    """Raised inside a scan when the user cancels; nothing is written for the current PDF."""


def _emit(report, **ev):
    if report:
        report(**ev)


def extract_page_records(page, ocr, min_px, on_phase=None):
    """Yield (source, content, bbox_json_or_None, error_or_None) for one page."""
    text = page.get_text().strip()
    if text:
        yield "text", text, None, None
    if ocr is None:
        return
    if not text:
        try:
            if on_phase:
                on_phase("ocr")
            png = page.get_pixmap(dpi=200).tobytes("png")
            out = ocr(png).strip()
            if out:
                yield "page_ocr", out, None, None
        except Exception as e:
            yield None, None, None, f"page OCR failed: {e}"
        return
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref")
        if not xref or info["width"] < min_px or info["height"] < min_px:
            continue
        try:
            if on_phase:
                on_phase("ocr")
            pix = fitz.Pixmap(page.parent, xref)
            if pix.colorspace and pix.colorspace.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            out = ocr(pix.tobytes("png")).strip()
            if out:
                yield "image_ocr", out, json.dumps(list(info["bbox"])), None
        except Exception as e:
            yield None, None, None, f"image OCR failed (xref {xref}): {e}"


def _is_unchanged(row, st, sig, ocr):
    if not row or row["size"] != st.st_size:
        return False
    if row["status"] == "error":
        return True
    if row["status"] != "ok":
        return False
    # Retry when OCR became available, settings changed, or earlier OCR calls failed.
    if ocr is not None and (row["ocr_sig"] != sig or row["ocr_errors"] > 0):
        return False
    return True


class Result(str):
    """'indexed' / 'skipped' / 'error', with .modified True when a known file's content changed."""
    modified = False


def index_pdf(conn, path, ocr=None, min_px=100, force=False, report=None, cancel=None, verify=False):
    """Index one PDF. Returns 'indexed', 'skipped' or 'error'.

    All extraction and OCR happens before any database write, so the write
    transaction is short and notes can be saved while a scan is running.
    `report(**event)` receives per-page progress; `cancel` is a threading.Event
    checked before every page (raises Cancelled, nothing is written).
    `verify` hashes the file even when size and mtime look unchanged.
    """
    path = Path(path)
    st = path.stat()
    sig = ocr_signature(ocr, min_px)
    row = conn.execute("SELECT * FROM documents WHERE path=?", (str(path),)).fetchone()
    conn.rollback()
    kind = doc_type(path) or "pdf"
    if st.st_size > size_limit(kind):
        # Too big to index safely: record the error without hashing; unchanged size+mtime skips it next time.
        if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime and row["status"] == "error":
            return Result("skipped")
        _store(conn, row, path, st, None, 0, "error", sig, 0, kind, [], [(None, f"file too large to index (> {size_limit(kind) >> 20} MB)")], False)
        return Result("error")
    if not force and not verify and row and row["mtime"] == st.st_mtime and _is_unchanged(row, st, sig, ocr):
        return Result("skipped")
    _emit(report, phase="hashing", page=0, pages=0)
    digest = file_hash(path)
    if not force and row and row["hash"] == digest and _is_unchanged(row, st, sig, ocr):
        if row["mtime"] != st.st_mtime:
            conn.execute("UPDATE documents SET mtime=? WHERE id=?", (st.st_mtime, row["id"]))
            conn.commit()
        return Result("skipped")
    modified = bool(row and row["hash"] and row["hash"] != digest)
    records, issues, pages, status, ocr_errors = [], [], 0, "ok", 0
    try:
        if kind == "pdf":
            records, issues, pages, ocr_errors = _extract_pdf(path, ocr, min_px, report, cancel)
        else:
            records, issues, pages, ocr_errors = _extract_unitfile(path, kind, ocr, min_px, report, cancel)
    except Cancelled:
        raise
    except Exception as e:
        status, records, issues = "error", [], [(None, f"cannot open: {e}")]

    if cancel is not None and cancel.is_set():
        raise Cancelled()
    _store(conn, row, path, st, digest, pages, status, sig, ocr_errors, kind, records, issues, modified)
    res = Result("error" if status == "error" else "indexed")
    res.modified = modified
    return res


def _extract_pdf(path, ocr, min_px, report, cancel):
    records, issues, ocr_errors = [], [], 0
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            raise ValueError("password protected")
        pages = doc.page_count
        for i in range(pages):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            _emit(report, phase="text", page=i + 1, pages=pages)
            n_ocr = n_issues = 0
            try:
                on_phase = lambda ph, i=i: _emit(report, phase=ph, page=i + 1, pages=pages)
                for source, content, bbox, err in extract_page_records(doc[i], ocr, min_px, on_phase):
                    if err:
                        ocr_errors += 1
                        n_issues += 1
                        issues.append((i + 1, err))
                    else:
                        records.append((i + 1, source, bbox, content))
                        if source != "text":
                            n_ocr += 1
            except Exception as e:
                n_issues += 1
                issues.append((i + 1, f"page failed: {e}"))
            _emit(report, page_done=True, ocr=n_ocr, issues=n_issues)
    finally:
        doc.close()
    return records, issues, pages, ocr_errors


def _extract_unitfile(path, kind, ocr, min_px, report, cancel):
    """Word/Excel/PowerPoint/text: one 'page' per unit (section, sheet, slide, block of lines)."""
    records, issues, ocr_errors = [], [], 0
    cur = [0, 0]
    on_phase = lambda ph: _emit(report, phase=ph, page=cur[0], pages=cur[1])
    n, gen, warnings, close = open_units(path, kind, ocr, min_px, on_phase)
    try:
        cur[1] = n
        for i, recs in enumerate(gen, 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            cur[0] = i
            _emit(report, phase="text", page=i, pages=n)
            n_ocr = n_issues = 0
            for source, content, bbox, err in recs:
                if err:
                    ocr_errors += 1
                    n_issues += 1
                    issues.append((i, err))
                else:
                    records.append((i, source, bbox, content))
                    if source != "text":
                        n_ocr += 1
            _emit(report, page_done=True, ocr=n_ocr, issues=n_issues)
    finally:
        close()
    for unit, msg in warnings:
        issues.append((unit, f"Warning: {msg}"))
    return records, issues, n, ocr_errors


def _store(conn, row, path, st, digest, pages, status, sig, ocr_errors, kind, records, issues, modified):
    with conn:
        if row:
            doc_id = row["id"]
            conn.execute(
                "UPDATE documents SET size=?, mtime=?, hash=?, pages=?, status=?, ocr_sig=?, ocr_errors=?, doc_type=? WHERE id=?",
                (st.st_size, st.st_mtime, digest, pages, status, sig, ocr_errors, kind, doc_id),
            )
        else:
            doc_id = conn.execute(
                "INSERT INTO documents (path,size,mtime,hash,pages,status,ocr_sig,ocr_errors,doc_type) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(path), st.st_size, st.st_mtime, digest, pages, status, sig, ocr_errors, kind),
            ).lastrowid
        conn.execute("DELETE FROM pages_fts WHERE doc_id=? AND source!='note'", (doc_id,))
        conn.executemany(
            "INSERT INTO pages_fts (doc_id, page, source, bbox, content) VALUES (?,?,?,?,?)",
            [(doc_id, p, s, b, strip_sentinels(c)) for p, s, b, c in records],
        )
        clear_issues(conn, path)
        for page, msg in issues:
            add_issue(conn, path, page, msg)
        if modified:
            conn.execute("UPDATE notes SET stale_at=? WHERE doc_id=? AND stale_at IS NULL", (time.time(), doc_id))


def _inside(path, roots):
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(r) for r in roots)


def list_files(folders, file_types=None):
    """Indexable files under the folders, skipping symlinks, lock files (~$*) and anything resolving outside them."""
    wanted = set(file_types if file_types is not None else ALL_TYPES)
    roots = [Path(f).resolve() for f in folders if Path(f).is_dir()]
    files = []
    for f in folders:
        for p in sorted(Path(f).rglob("*")):
            if p.name.startswith("~$") or doc_type(p) not in wanted:
                continue
            if p.is_file() and not p.is_symlink() and _inside(p, roots):
                files.append(p)
    return files


def list_pdfs(folders):
    return list_files(folders, ["pdf"])


def list_unsupported(folders):
    """Legacy binary Office files (.doc/.xls/.ppt) that cannot be indexed."""
    roots = [Path(f).resolve() for f in folders if Path(f).is_dir()]
    out = []
    for f in folders:
        for p in sorted(Path(f).rglob("*")):
            if (p.suffix.lower() in LEGACY_EXTS and not p.name.startswith("~$")
                    and p.is_file() and not p.is_symlink() and _inside(p, roots)):
                out.append(p)
    return out


UNSUPPORTED = "Unsupported format"


def sync_unsupported(conn, paths):
    with conn:
        keep = {str(p) for p in paths}
        for r in conn.execute("SELECT id, path FROM issues WHERE message LIKE ?", (UNSUPPORTED + "%",)).fetchall():
            if r["path"] not in keep:
                conn.execute("DELETE FROM issues WHERE id=?", (r["id"],))
        have = {r["path"] for r in conn.execute("SELECT path FROM issues WHERE message LIKE ?", (UNSUPPORTED + "%",))}
        for p in paths:
            if str(p) not in have:
                kind = LEGACY_EXTS[p.suffix.lower()]
                add_issue(conn, p, None, f"{UNSUPPORTED}: legacy {kind} ({p.suffix}) files are not indexed. "
                          "Save it as a modern format (.docx/.xlsx/.pptx) to include it.")


def path_is_servable(path, folders):
    p = Path(path)
    roots = [Path(f).resolve() for f in folders if Path(f).is_dir()]
    return p.is_file() and not p.is_symlink() and _inside(p, roots)


def _page_estimate(conn, path):
    """Page count without extracting: from the index when unchanged, else from the PDF header."""
    try:
        row = conn.execute("SELECT pages, size FROM documents WHERE path=? AND status='ok'", (str(path),)).fetchone()
        if row and row["size"] == path.stat().st_size and row["pages"]:
            return row["pages"]
        if doc_type(path) != "pdf":
            return 1
        with fitz.open(path) as d:
            return 0 if d.needs_pass else d.page_count
    except Exception:
        return 0


POSSIBLY_MOVED = "Possibly moved"


def detect_moves(conn, files):
    """Relink documents whose file moved or was renamed (same size and content hash).

    Keeps the doc id, notes and indexed text. Matching is one-to-one and prefers the
    same file name. Returns (moved_count, new_paths) where new_paths are files with
    no document row that were not matched.
    """
    present = {str(p) for p in files}
    known = {r["path"] for r in conn.execute("SELECT path FROM documents")}
    new_files = [p for p in files if str(p) not in known]
    gone = [r for r in conn.execute("SELECT id, path, size, hash, status FROM documents")
            if r["path"] not in present and r["hash"]]
    if not new_files or not gone:
        return 0, new_files
    by_size = {}
    for r in gone:
        by_size.setdefault(r["size"], []).append(r)
    cands = []
    for p in new_files:
        group = by_size.get(p.stat().st_size)
        if not group:
            continue
        try:
            digest = file_hash(p)
        except OSError:
            continue
        cands.append((p, digest, [r for r in group if r["hash"] == digest]))
    used, moved_paths = set(), set()
    for same_name in (True, False):
        for p, digest, matches in cands:
            if str(p) in moved_paths:
                continue
            free = [r for r in matches if r["id"] not in used
                    and (not same_name or Path(r["path"]).name.lower() == p.name.lower())]
            if not free:
                continue
            r = free[0]
            used.add(r["id"])
            moved_paths.add(str(p))
            st = p.stat()
            with conn:
                conn.execute("UPDATE documents SET path=?, mtime=?, status=CASE WHEN status='missing' THEN 'ok' ELSE status END "
                             "WHERE id=?", (str(p), st.st_mtime, r["id"]))
                conn.execute("UPDATE issues SET path=? WHERE path=?", (str(p), r["path"]))
    return len(moved_paths), [p for p in new_files if str(p) not in moved_paths]


def flag_possibly_moved(conn, new_paths):
    """New files that look like a modified copy of a missing document: flag, never relink silently."""
    for p in new_paths:
        row = conn.execute("SELECT pages, size FROM documents WHERE path=?", (str(p),)).fetchone()
        if not row:
            continue
        for g in conn.execute("SELECT id, path, pages, size FROM documents WHERE status='missing'"):
            if Path(g["path"]).name.lower() != p.name.lower():
                continue
            close_size = g["size"] and abs(row["size"] - g["size"]) <= 0.25 * g["size"]
            if close_size or g["pages"] == row["pages"]:
                with conn:
                    conn.execute("DELETE FROM issues WHERE path=? AND message LIKE ?", (str(p), POSSIBLY_MOVED + "%"))
                    add_issue(conn, p, None, f"{POSSIBLY_MOVED}: looks like a changed copy of {g['path']} "
                              "(notes stay on the old entry; relink them under Notes > Orphaned notes)")
                break


def scan_folders(conn, folders, ocr=None, min_px=100, progress=None, force=False, cancel=None, report=None, verify=False,
                 file_types=None):
    """Index all supported files. `report(snapshot_dict)` gets file- and page-level progress;
    `force` re-indexes unchanged files; `cancel` (threading.Event) stops between pages/units."""
    files = list_files(folders, file_types)
    moved, new_paths = detect_moves(conn, files)
    est = [_page_estimate(conn, p) for p in files]
    stats = {"indexed": 0, "skipped": 0, "error": 0, "removed": 0, "moved": moved, "modified": 0, "modified_files": [], "cancelled": False}
    snap = {"files_done": 0, "files_total": len(files), "pages_done": 0, "pages_total": sum(est),
            "current": "", "page": 0, "pages": 0, "phase": "text", "ocr_items": 0, "issues": 0, "errors": 0}

    def emit():
        if report:
            report(dict(snap))

    def on_event(**ev):
        if ev.get("page_done"):
            snap["pages_done"] += 1
            snap["ocr_items"] += ev.get("ocr", 0)
            snap["issues"] += ev.get("issues", 0)
        else:
            snap.update(phase=ev["phase"], page=ev["page"], pages=ev["pages"])
        emit()

    emit()
    for i, p in enumerate(files):
        if cancel is not None and cancel.is_set():
            stats["cancelled"] = True
            break
        base = snap["pages_done"]
        snap.update(files_done=i, current=str(p), page=0, pages=est[i], phase="text")
        if progress:
            progress(i, len(files), str(p))
        emit()
        try:
            res = index_pdf(conn, p, ocr, min_px, force, on_event, cancel, verify)
            stats[res] += 1
            if getattr(res, "modified", False):
                stats["modified"] += 1
                stats["modified_files"].append(str(p))
            if res == "error":
                snap["errors"] += 1
                snap["issues"] += 1
        except Cancelled:
            stats["cancelled"] = True
            break
        except Exception as e:
            log_issue(conn, p, None, f"unexpected: {e}")
            stats["error"] += 1
            snap["errors"] += 1
            snap["issues"] += 1
        snap.update(files_done=i + 1, pages_done=base + est[i])
        emit()
    if stats["cancelled"]:
        return stats
    present = {str(p) for p in files}
    with conn:
        for r in conn.execute("SELECT id, path FROM documents WHERE status!='missing'").fetchall():
            if r["path"] not in present:
                # Text stays indexed (search ignores missing docs) so a later move can be relinked cheaply.
                conn.execute("UPDATE documents SET status='missing' WHERE id=?", (r["id"],))
                clear_issues(conn, r["path"])
                stats["removed"] += 1
    flag_possibly_moved(conn, new_paths)
    unsupported = list_unsupported(folders)
    sync_unsupported(conn, unsupported)
    stats["unsupported"] = len(unsupported)
    if progress:
        progress(len(files), len(files), "")
    snap.update(current="", page=0, pages=0)
    emit()
    return stats
