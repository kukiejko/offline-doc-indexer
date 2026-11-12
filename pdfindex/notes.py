import time

from .search import strip_sentinels


def save_note(conn, doc_id, page, text):
    text = strip_sentinels(text).strip()
    conn.execute("DELETE FROM pages_fts WHERE doc_id=? AND page=? AND source='note'", (doc_id, page))
    if not text:
        conn.execute("DELETE FROM notes WHERE doc_id=? AND page=?", (doc_id, page))
    else:
        conn.execute(
            "INSERT INTO notes (doc_id,page,text,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(doc_id,page) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at, stale_at=NULL",
            (doc_id, page, text, time.time()),
        )
        conn.execute(
            "INSERT INTO pages_fts (doc_id,page,source,bbox,content) VALUES (?,?,?,?,?)",
            (doc_id, page, "note", None, text),
        )
    conn.commit()


def get_note(conn, doc_id, page):
    r = conn.execute("SELECT text FROM notes WHERE doc_id=? AND page=?", (doc_id, page)).fetchone()
    return r["text"] if r else ""


def get_note_stale(conn, doc_id, page):
    r = conn.execute("SELECT stale_at FROM notes WHERE doc_id=? AND page=?", (doc_id, page)).fetchone()
    return r["stale_at"] if r else None


def dismiss_stale(conn, doc_id, page):
    conn.execute("UPDATE notes SET stale_at=NULL WHERE doc_id=? AND page=?", (doc_id, page))
    conn.commit()


def stale_pages(conn, doc_id):
    return [r["page"] for r in conn.execute(
        "SELECT page FROM notes WHERE doc_id=? AND stale_at IS NOT NULL ORDER BY page", (doc_id,))]


def pages_with_notes(conn, doc_id):
    return [r["page"] for r in conn.execute("SELECT page FROM notes WHERE doc_id=? ORDER BY page", (doc_id,))]


def list_notes(conn):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT n.doc_id, d.path, n.page, n.text, n.updated_at, n.stale_at FROM notes n "
            "JOIN documents d ON d.id=n.doc_id WHERE d.status != 'missing' ORDER BY d.path, n.page"
        )
    ]


def list_orphaned(conn):
    """Notes attached to documents whose PDF is gone, grouped by document."""
    out = {}
    for r in conn.execute(
        "SELECT n.doc_id, d.path, n.page, n.text FROM notes n "
        "JOIN documents d ON d.id=n.doc_id WHERE d.status='missing' ORDER BY d.path, n.page"
    ):
        g = out.setdefault(r["doc_id"], {"doc_id": r["doc_id"], "path": r["path"], "notes": []})
        g["notes"].append({"page": r["page"], "text": r["text"]})
    return list(out.values())


def relink_orphan(conn, orphan_id, target_id):
    """Move all notes of a missing document onto another document (appending on page clashes)."""
    old = conn.execute("SELECT status FROM documents WHERE id=?", (orphan_id,)).fetchone()
    tgt = conn.execute("SELECT status, pages FROM documents WHERE id=?", (target_id,)).fetchone()
    if not old or old["status"] != "missing" or not tgt or tgt["status"] == "missing" or orphan_id == target_id:
        raise ValueError("invalid relink")
    for n in conn.execute("SELECT page, text FROM notes WHERE doc_id=? ORDER BY page", (orphan_id,)).fetchall():
        existing = get_note(conn, target_id, n["page"])
        save_note(conn, target_id, n["page"], f"{existing}\n\n---\n\n{n['text']}" if existing else n["text"])
    conn.execute("DELETE FROM notes WHERE doc_id=?", (orphan_id,))
    conn.execute("DELETE FROM pages_fts WHERE doc_id=?", (orphan_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (orphan_id,))
    conn.commit()
