import mimetypes
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import indexer
from . import notes as notes_mod
from .__version__ import DESIGNER, RELEASE_TIMESTAMP, __version__
from .config import Config
from .extract import ALL_TYPES, TYPE_LABELS, UNIT_NAMES, doc_type, LEGACY_EXTS
from .db import connect, list_issues
from .search import group_by_document, search

STATIC = Path(__file__).parent / "static"


def _read_doc(name: str) -> str:
    p = Path(__file__).parent.parent / name
    return p.read_text(encoding="utf-8") if p.is_file() else f"{name} is not available in this installation."
# Windows registries often map .mjs to text/plain, which browsers refuse for module scripts.
mimetypes.add_type("text/javascript", ".mjs")

CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
    "img-src 'self' data: blob:; worker-src 'self' blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class NoteBody(BaseModel):
    text: str


class RelinkBody(BaseModel):
    target_id: int


class Scanner:
    """Runs scans in a background thread and exposes progress."""

    def __init__(self, cfg: Config, ocr):
        self.cfg, self.ocr = cfg, ocr
        self.lock = threading.Lock()
        self.state = {"running": False, "mode": None, "last": None, "error": None, "cancelling": False,
                      "started_at": None, "files_done": 0, "files_total": 0, "pages_done": 0, "pages_total": 0,
                      "current": "", "page": 0, "pages": 0, "phase": "text", "ocr_items": 0, "issues": 0, "errors": 0}
        self._timer = None
        self._dirty = False
        self._closed = False
        self._cancel = threading.Event()

    def start(self, queue=False, full=False, verify=None):
        """Start a scan. Returns False if one is already running (or closed).
        With queue, a changed-only rerun is scheduled once the running scan finishes.
        `verify` (default: config verify_checksums) hashes every file to catch edits that keep size and mtime."""
        verify = self.cfg.verify_checksums if verify is None else verify
        with self.lock:
            if self._closed:
                return False
            if self.state["running"]:
                if queue:
                    self._dirty = True
                return False
            self._cancel.clear()
            mode = "full" if full else "verify" if verify else "changed"
            self.state.update(running=True, mode=mode, error=None, cancelling=False,
                              started_at=time.time(), files_done=0, files_total=0, pages_done=0, pages_total=0,
                              current="", page=0, pages=0, phase="text", ocr_items=0, issues=0, errors=0)
            self._dirty = False
        threading.Thread(target=self._run, args=(full, verify), daemon=True).start()
        return True

    def cancel(self):
        """Ask the running scan to stop at the next page. Safe: the current PDF is not half-written."""
        with self.lock:
            if not self.state["running"]:
                return False
            self.state["cancelling"] = True
        self._cancel.set()
        return True

    def run_blocking(self, full=False, verify=None):
        if self.start(full=full, verify=verify):
            while self.state["running"]:
                threading.Event().wait(0.05)

    def _report(self, snap):
        self.state.update(snap)

    def snapshot(self):
        s = dict(self.state)
        if s["running"] and s["started_at"]:
            elapsed = time.time() - s["started_at"]
            s["elapsed"] = round(elapsed, 1)
            done, total = s["pages_done"], s["pages_total"]
            s["eta"] = round(elapsed / done * (total - done), 0) if done and total > done else None
        return s

    def _run(self, full, verify=False):
        conn = None
        t0 = time.time()
        try:
            conn = connect(self.cfg.db_path)
            while True:
                stats = indexer.scan_folders(
                    conn, self.cfg.folders, self.ocr, self.cfg.min_ocr_image_px,
                    force=full, cancel=self._cancel, report=self._report, verify=verify,
                    file_types=self.cfg.file_types)
                stats["duration"] = round(time.time() - t0, 1)
                stats["mode"] = "full" if full else "verify" if verify else "changed"
                stats["pages"] = self.state["pages_done"]
                stats["ocr_items"] = self.state["ocr_items"]
                stats["issues"] = self.state["issues"]
                self.state["last"] = stats
                with self.lock:
                    if stats.get("cancelled") or not self._dirty or self._closed:
                        self.state.update(running=False, cancelling=False)
                        break
                    self._dirty = False
                full, t0 = False, time.time()
                verify = self.cfg.verify_checksums
        except Exception as e:
            self.state["error"] = str(e)
            with self.lock:
                self.state.update(running=False, cancelling=False)
        finally:
            if conn:
                conn.close()

    def schedule(self, delay=5.0):
        """Debounced rescan used by the folder watcher."""
        with self.lock:
            if self._closed:
                return
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(delay, self.start, kwargs={"queue": True})
            self._timer.daemon = True
            self._timer.start()

    def close(self):
        self._cancel.set()
        with self.lock:
            self._closed = True
            if self._timer:
                self._timer.cancel()
                self._timer = None


def _watched(path, file_types=None):
    name = Path(path).name
    if name.startswith("~$"):
        return False
    t = doc_type(path)
    return (t is not None and t in (file_types if file_types is not None else ALL_TYPES)) or Path(path).suffix.lower() in LEGACY_EXTS


def watch_relevant(event, file_types=None):
    """PDF changes, moves/renames (src or dest) and folder moves/deletes all warrant a rescan."""
    paths = [str(getattr(event, "src_path", "")), str(getattr(event, "dest_path", ""))]
    return bool(getattr(event, "is_directory", False)) or any(_watched(p, file_types) for p in paths)


def start_watcher(cfg: Config, scanner: Scanner):
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class H(FileSystemEventHandler):
        def on_any_event(self, event):
            if watch_relevant(event, cfg.file_types):
                scanner.schedule()

    obs = Observer()
    for f in cfg.folders:
        if Path(f).is_dir():
            obs.schedule(H(), f, recursive=True)
    obs.daemon = True
    obs.start()
    return obs


def create_app(cfg: Config, ocr=None, watch=True):
    connect(cfg.db_path).close()
    scanner = Scanner(cfg, ocr)
    observer = start_watcher(cfg, scanner) if watch and cfg.watcher else None

    @asynccontextmanager
    async def lifespan(app):
        yield
        scanner.close()
        if observer:
            observer.stop()
            observer.join(timeout=5)

    app = FastAPI(title="PDF Indexer", lifespan=lifespan)
    app.state.scanner = scanner
    app.state.observer = observer
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method in MUTATING:
            site = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            if site is not None and site not in ("same-origin", "none"):
                return JSONResponse({"detail": "Cross-site request rejected"}, status_code=403)
            if origin is not None and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def db():
        return connect(cfg.db_path)

    def doc_or_404(conn, doc_id):
        r = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not r or r["status"] == "missing":
            raise HTTPException(404, "Document not found")
        return r

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/view/{doc_id}")
    def view(doc_id: int):
        return FileResponse(STATIC / "viewer.html")

    MAX_FILTER_IDS = 5000

    def parse_ids(raw):
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit() or len(part) > 9:
                raise HTTPException(422, "docs must be a comma-separated list of numeric ids")
            ids.append(int(part))
        if len(ids) > MAX_FILTER_IDS:
            raise HTTPException(422, f"at most {MAX_FILTER_IDS} files can be selected")
        return ids

    @app.get("/api/docs")
    def list_docs():
        conn = db()
        try:
            rows = conn.execute("SELECT id, path, doc_type FROM documents WHERE status != 'missing' ORDER BY path").fetchall()
            return [{"id": r["id"], "path": r["path"], "type": r["doc_type"]} for r in rows]
        finally:
            conn.close()

    @app.get("/api/search")
    def api_search(q: str = "", source: str = "", folder: str = "", docs: str = "", mode: str = "only", types: str = ""):
        tlist = [t for t in types.split(",") if t]
        if any(t not in ALL_TYPES for t in tlist):
            raise HTTPException(422, "unknown file type")
        if mode not in ("only", "exclude"):
            raise HTTPException(422, "mode must be 'only' or 'exclude'")
        ids = parse_ids(docs)
        conn = db()
        try:
            res = search(conn, q, [s for s in source.split(",") if s] or None, folder or None,
                         doc_ids=ids if mode == "only" else None,
                         exclude_ids=ids if mode == "exclude" else None,
                         types=tlist or None)
            for r in res:
                r.pop("content", None)
            return {"query": q, "count": len(res), "groups": group_by_document(res)}
        finally:
            conn.close()

    @app.post("/api/rescan")
    def rescan(mode: str = "changed"):
        if mode not in ("changed", "verify", "full"):
            raise HTTPException(422, "mode must be 'changed', 'verify' or 'full'")
        return {"started": scanner.start(full=mode == "full", verify=mode == "verify")}

    @app.post("/api/scan/cancel")
    def cancel_scan():
        return {"cancelled": scanner.cancel()}

    @app.get("/api/version")
    def version():
        return {"name": "PDF Indexer", "version": __version__, "released": RELEASE_TIMESTAMP, "designer": DESIGNER}

    @app.get("/api/changelog", response_class=PlainTextResponse)
    def changelog():
        return _read_doc("CHANGELOG.md")

    @app.get("/api/readme", response_class=PlainTextResponse)
    def readme():
        return _read_doc("README.md")

    @app.get("/api/status")
    def status():
        conn = db()
        try:
            n = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='ok'").fetchone()["c"]
            return {**scanner.snapshot(), "documents": n, "ocr_available": ocr is not None, "folders": cfg.folders,
                    "file_types": cfg.file_types, "type_labels": TYPE_LABELS}
        finally:
            conn.close()

    @app.get("/api/issues")
    def issues():
        conn = db()
        try:
            return list_issues(conn)
        finally:
            conn.close()

    @app.get("/api/docs/{doc_id}")
    def doc_info(doc_id: int):
        conn = db()
        try:
            r = doc_or_404(conn, doc_id)
            return {
                "id": r["id"], "name": Path(r["path"]).name, "pages": r["pages"],
                "type": r["doc_type"], "type_label": TYPE_LABELS.get(r["doc_type"], r["doc_type"]),
                "unit_name": UNIT_NAMES.get(r["doc_type"], "Page"),
                "note_pages": notes_mod.pages_with_notes(conn, doc_id),
            }
        finally:
            conn.close()

    @app.get("/api/pdf/{doc_id}")
    def pdf(doc_id: int):
        conn = db()
        try:
            r = doc_or_404(conn, doc_id)
        finally:
            conn.close()
        if not indexer.path_is_servable(r["path"], cfg.folders):
            raise HTTPException(404, "File not available")
        return FileResponse(r["path"], media_type="application/pdf")

    @app.get("/api/units/{doc_id}")
    def units(doc_id: int):
        conn = db()
        try:
            doc_or_404(conn, doc_id)
            rows = conn.execute(
                "SELECT page, source, content FROM pages_fts WHERE doc_id=? AND source IN ('text','image_ocr','page_ocr') "
                "ORDER BY page, rowid", (doc_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/file/{doc_id}")
    def original_file(doc_id: int):
        conn = db()
        try:
            r = doc_or_404(conn, doc_id)
        finally:
            conn.close()
        if not indexer.path_is_servable(r["path"], cfg.folders):
            raise HTTPException(404, "File not available")
        media = mimetypes.guess_type(r["path"])[0] or "application/octet-stream"
        return FileResponse(r["path"], media_type=media, filename=Path(r["path"]).name)

    @app.get("/api/ocr/{doc_id}/{page}")
    def page_ocr(doc_id: int, page: int):
        conn = db()
        try:
            rows = conn.execute(
                "SELECT source, bbox, content FROM pages_fts WHERE doc_id=? AND page=? AND source IN ('image_ocr','page_ocr')",
                (doc_id, page),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/orphans")
    def orphans():
        conn = db()
        try:
            return notes_mod.list_orphaned(conn)
        finally:
            conn.close()

    @app.post("/api/orphans/{doc_id}/relink")
    def relink(doc_id: int, body: RelinkBody):
        conn = db()
        try:
            notes_mod.relink_orphan(conn, doc_id, body.target_id)
            return {"ok": True}
        except ValueError:
            raise HTTPException(422, "Cannot relink: pick an existing, indexed file")
        finally:
            conn.close()

    @app.get("/api/notes")
    def all_notes():
        conn = db()
        try:
            return notes_mod.list_notes(conn)
        finally:
            conn.close()

    @app.get("/api/notes/{doc_id}/{page}")
    def get_note(doc_id: int, page: int):
        conn = db()
        try:
            return {"text": notes_mod.get_note(conn, doc_id, page),
                    "stale_at": notes_mod.get_note_stale(conn, doc_id, page)}
        finally:
            conn.close()

    @app.post("/api/notes/{doc_id}/{page}/dismiss")
    def dismiss_note_warning(doc_id: int, page: int):
        conn = db()
        try:
            notes_mod.dismiss_stale(conn, doc_id, page)
            return {"ok": True}
        finally:
            conn.close()

    @app.put("/api/notes/{doc_id}/{page}")
    def put_note(doc_id: int, page: int, body: NoteBody):
        conn = db()
        try:
            doc_or_404(conn, doc_id)
            notes_mod.save_note(conn, doc_id, page, body.text)
            return JSONResponse({"ok": True})
        finally:
            conn.close()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

