import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    size INTEGER, mtime REAL, hash TEXT,
    pages INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    ocr_sig TEXT NOT NULL DEFAULT 'none',
    ocr_errors INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    doc_id UNINDEXED, page UNINDEXED, source UNINDEXED, bbox UNINDEXED,
    content, tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS notes (
    doc_id INTEGER NOT NULL, page INTEGER NOT NULL,
    text TEXT NOT NULL, updated_at REAL NOT NULL, stale_at REAL,
    PRIMARY KEY (doc_id, page)
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL, page INTEGER, message TEXT NOT NULL, created_at REAL NOT NULL
);
"""


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    for col, ddl in (("ocr_sig", "TEXT NOT NULL DEFAULT 'none'"), ("ocr_errors", "INTEGER NOT NULL DEFAULT 0"),
                     ("doc_type", "TEXT NOT NULL DEFAULT 'pdf'")):
        if col not in cols:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {ddl}")
    if "stale_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}:
        conn.execute("ALTER TABLE notes ADD COLUMN stale_at REAL")
    return conn


def add_issue(conn, path, page, message):
    """Insert an issue without committing (caller owns the transaction)."""
    conn.execute(
        "INSERT INTO issues (path, page, message, created_at) VALUES (?,?,?,?)",
        (str(path), page, message, time.time()),
    )


def log_issue(conn, path, page, message):
    add_issue(conn, path, page, message)
    conn.commit()


def clear_issues(conn, path):
    conn.execute("DELETE FROM issues WHERE path=?", (str(path),))


def list_issues(conn):
    return [dict(r) for r in conn.execute("SELECT path, page, message FROM issues ORDER BY id DESC")]
