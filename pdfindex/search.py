import html
import json
import re

BADGES = {"text": "Text", "image_ocr": "Image", "page_ocr": "Image", "note": "Note"}
SOURCE_GROUPS = {"text": ["text"], "image": ["image_ocr", "page_ocr"], "note": ["note"]}
_OPEN, _CLOSE = "\x02", "\x03"


def safe_snippet(raw):
    """HTML-escape an FTS snippet, then turn the sentinel markers into <mark> tags.

    Content is stripped of the sentinel characters when stored, so they only come from snippet()."""
    return html.escape(raw, quote=True).replace(_OPEN, "<mark>").replace(_CLOSE, "</mark>")


def strip_sentinels(text):
    return text.replace(_OPEN, " ").replace(_CLOSE, " ")


def build_match(query):
    """Turn user input into a safe FTS5 query: quoted phrases kept, words ANDed."""
    parts = []
    for m in re.finditer(r'"([^"]*)"|(\S+)', query):
        if m.group(1) is not None:
            words = re.findall(r"\w+", m.group(1))
            if words:
                parts.append('"' + " ".join(words) + '"')
        else:
            parts.extend(f'"{w}"' for w in re.findall(r"\w+", m.group(2)))
    return " AND ".join(parts)


def search(conn, query, sources=None, path_prefix=None, limit=200, doc_ids=None, exclude_ids=None, types=None):
    match = build_match(query)
    if not match:
        return []
    sql = (
        "SELECT f.doc_id, d.path, d.doc_type, f.page, f.source, f.bbox, "
        "snippet(pages_fts, 4, '\x02', '\x03', ' … ', 24) AS snippet, "
        "f.content, bm25(pages_fts) AS score "
        "FROM pages_fts f JOIN documents d ON d.id = f.doc_id "
        "WHERE pages_fts MATCH ? AND d.status != 'missing'"
    )
    args = [match]
    if doc_ids:
        sql += " AND d.id IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(sorted(set(doc_ids))))
    if exclude_ids:
        sql += " AND d.id NOT IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(sorted(set(exclude_ids))))
    if types:
        sql += f" AND d.doc_type IN ({','.join('?' * len(types))})"
        args += list(types)
    if sources:
        srcs = [s for g in sources for s in SOURCE_GROUPS.get(g, [g])]
        sql += f" AND f.source IN ({','.join('?' * len(srcs))})"
        args += srcs
    if path_prefix:
        sql += " AND d.path LIKE ?"
        args.append(path_prefix.replace("%", "") + "%")
    sql += " ORDER BY score LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["snippet"] = safe_snippet(d["snippet"])
        d["badge"] = BADGES.get(d["source"], d["source"])
        out.append(d)
    return out


def group_by_document(results):
    groups = {}
    for r in results:
        g = groups.setdefault(r["doc_id"], {"doc_id": r["doc_id"], "path": r["path"], "type": r["doc_type"], "hits": []})
        g["hits"].append(r)
    return list(groups.values())
