"""Text extraction for Office and plain-text files (no external programs, no macros, no network).

Each extractor returns (n_units, generator, warnings). The generator yields, per unit, a list of
(source, content, bbox, error) tuples, like the PDF page loop. `warnings` is a list of (unit, message)
that is complete once the generator is exhausted. A "unit" is the page-like chunk of a file:
a Word section, an Excel sheet, a PowerPoint slide or a block of text lines.
"""
import io
import posixpath
import zipfile
from pathlib import Path

from defusedxml.ElementTree import fromstring, iterparse

TYPE_BY_EXT = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx",
               ".pptx": "pptx", ".txt": "txt", ".md": "md"}
TYPE_LABELS = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "pptx": "PowerPoint", "txt": "Text", "md": "Markdown"}
UNIT_NAMES = {"pdf": "Page", "docx": "Section", "xlsx": "Sheet", "pptx": "Slide", "txt": "Part", "md": "Part"}
ALL_TYPES = list(TYPE_LABELS)
LEGACY_EXTS = {".doc": "Word", ".xls": "Excel", ".ppt": "PowerPoint"}

MAX_OFFICE_BYTES = 100 * 1024 * 1024
MAX_TEXT_BYTES = 50 * 1024 * 1024
MAX_UNZIPPED = 500 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_RATIO = 1000
MAX_XML = 32 * 1024 * 1024
MAX_SHEET_XML = 100 * 1024 * 1024
MAX_SHEET_ROWS = 50000
MAX_SHEET_CELLS = 500000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 200
DOCX_PARAS, DOCX_CHARS = 40, 8000
TEXT_LINES, TEXT_CHARS = 80, 20000

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SKIP_IMAGE_EXTS = {".emf", ".wmf", ".svg", ".wdp", ".tmp"}


def doc_type(path):
    return TYPE_BY_EXT.get(Path(path).suffix.lower())


def size_limit(kind):
    return MAX_TEXT_BYTES if kind in ("txt", "md") else MAX_OFFICE_BYTES


class SafeZip:
    """Read-only OOXML package with zip-bomb limits; nothing is ever executed or extracted to disk."""

    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        infos = self.z.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError("archive has too many entries")
        if sum(i.file_size for i in infos) > MAX_UNZIPPED:
            raise ValueError("archive expands too large (possible zip bomb)")
        for i in infos:
            if i.file_size > (1 << 20) and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                raise ValueError("suspicious compression ratio (possible zip bomb)")
        self.names = set(self.z.namelist())

    def read(self, name, limit=MAX_XML):
        with self.z.open(name) as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"{name} is too large")
        return data

    def close(self):
        self.z.close()


def _rels(z, rel_path, base):
    """{id: (type, resolved_target)} for an OOXML relationships part."""
    out = {}
    if rel_path not in z.names:
        return out
    for el in fromstring(z.read(rel_path)):
        if el.get("TargetMode") == "External":
            continue
        t = el.get("Target", "")
        target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
        out[el.get("Id")] = (el.get("Type", "").rsplit("/", 1)[-1], target)
    return out


def _ocr_media(z, target, ocr, min_px):
    """OCR text of an embedded image, '' when too small/unsupported. Raises on real failures."""
    if Path(target).suffix.lower() in SKIP_IMAGE_EXTS or target not in z.names:
        return ""
    from PIL import Image
    im = Image.open(io.BytesIO(z.read(target, MAX_IMAGE_BYTES)))
    if im.width < min_px or im.height < min_px:
        return ""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return ocr(buf.getvalue()).strip()


def _image_records(z, rids, rels, ocr, min_px, on_phase, counter, warnings, unit):
    recs = []
    if ocr is None:
        return recs
    for rid in dict.fromkeys(rids):
        rel = rels.get(rid)
        if not rel or rel[0] != "image":
            continue
        if counter[0] >= MAX_IMAGES:
            if counter[0] == MAX_IMAGES:
                warnings.append((unit, f"only the first {MAX_IMAGES} images were OCR'd"))
                counter[0] += 1
            break
        counter[0] += 1
        try:
            if on_phase:
                on_phase("ocr")
            text = _ocr_media(z, rel[1], ocr, min_px)
            if text:
                recs.append(("image_ocr", text, None, None))
        except Exception as e:
            recs.append((None, None, None, f"image OCR failed ({rel[1]}): {e}"))
    return recs


def _wtext(p):
    parts = []

    def walk(el):
        for ch in el:
            if ch.tag == W + "p":
                continue
            if ch.tag == W + "t":
                parts.append(ch.text or "")
            elif ch.tag == W + "tab":
                parts.append("\t")
            elif ch.tag in (W + "br", W + "cr"):
                parts.append("\n")
            walk(ch)

    walk(p)
    return "".join(parts)


def _docx(z, ocr, min_px, on_phase, warnings):
    root = fromstring(z.read("word/document.xml"))
    rels = _rels(z, "word/_rels/document.xml.rels", "word")
    units, cur = [], {"lines": [], "rids": [], "chars": 0, "body": False}
    for p in root.iter(W + "p"):
        style = p.find(f"{W}pPr/{W}pStyle")
        name = (style.get(W + "val", "") if style is not None else "").lower()
        heading = name.startswith(("heading", "title"))
        text = _wtext(p).strip()
        rids = [b.get(R + "embed") for b in p.iter(A + "blip") if b.get(R + "embed")]
        if not text and not rids:
            continue
        if cur["lines"] and ((heading and cur["body"]) or len(cur["lines"]) >= DOCX_PARAS or cur["chars"] >= DOCX_CHARS):
            units.append(cur)
            cur = {"lines": [], "rids": [], "chars": 0, "body": False}
        if text:
            cur["lines"].append(text)
            cur["chars"] += len(text)
        cur["body"] = cur["body"] or not heading
        cur["rids"] += rids
    if cur["lines"] or cur["rids"]:
        units.append(cur)
    counter = [0]

    def gen():
        for i, u in enumerate(units, 1):
            recs = []
            if u["lines"]:
                recs.append(("text", "\n".join(u["lines"]), None, None))
            recs += _image_records(z, u["rids"], rels, ocr, min_px, on_phase, counter, warnings, i)
            yield recs

    return len(units), gen()


def _a_text(root, skip_digits=False):
    lines = []
    for p in root.iter(A + "p"):
        s = "".join((el.text or "") if el.tag == A + "t" else "\n" if el.tag == A + "br" else "" for el in p.iter()).strip()
        if s and not (skip_digits and s.isdigit()):
            lines.append(s)
    return lines


def _pptx(z, ocr, min_px, on_phase, warnings):
    pres_rels = _rels(z, "ppt/_rels/presentation.xml.rels", "ppt")
    pres = fromstring(z.read("ppt/presentation.xml"))
    slides = []
    for el in pres.iter():
        if el.tag.endswith("}sldId"):
            rel = pres_rels.get(el.get(R + "id"))
            if rel and rel[1] in z.names:
                slides.append(rel[1])
    counter = [0]

    def gen():
        for i, path in enumerate(slides, 1):
            root = fromstring(z.read(path))
            base = posixpath.dirname(path)
            rels = _rels(z, f"{base}/_rels/{posixpath.basename(path)}.rels", base)
            text = "\n".join(_a_text(root))
            notes = ""
            for typ, target in rels.values():
                if typ == "notesSlide" and target in z.names:
                    notes = "\n".join(_a_text(fromstring(z.read(target)), skip_digits=True))
            if notes:
                text = f"{text}\n\nSpeaker notes:\n{notes}".strip()
            recs = [("text", text, None, None)] if text else []
            rids = [b.get(R + "embed") for b in root.iter(A + "blip") if b.get(R + "embed")]
            recs += _image_records(z, rids, rels, ocr, min_px, on_phase, counter, warnings, i)
            yield recs

    return len(slides), gen()


def _shared_strings(z, warnings):
    if "xl/sharedStrings.xml" not in z.names:
        return []
    out = []
    for _, el in iterparse(io.BytesIO(z.read("xl/sharedStrings.xml", MAX_SHEET_XML)), events=("end",)):
        if el.tag == M + "si":
            s = []
            for ch in el:
                if ch.tag == M + "t":
                    s.append(ch.text or "")
                elif ch.tag == M + "r":
                    s.extend(t.text or "" for t in ch if t.tag == M + "t")
            out.append("".join(s))
            el.clear()
            if len(out) >= 2_000_000:
                warnings.append((None, "shared strings truncated at 2,000,000 entries"))
                break
    return out


def _xlsx(z, ocr, min_px, on_phase, warnings):
    wb = fromstring(z.read("xl/workbook.xml"))
    rels = _rels(z, "xl/_rels/workbook.xml.rels", "xl")
    sheets = []
    for el in wb.iter(M + "sheet"):
        rel = rels.get(el.get(R + "id"))
        if rel and rel[1] in z.names:
            sheets.append((el.get("name", "Sheet"), rel[1]))
    shared = _shared_strings(z, warnings)

    def gen():
        for i, (name, path) in enumerate(sheets, 1):
            rows, cells, lines, truncated = 0, 0, [], False
            for _, el in iterparse(io.BytesIO(z.read(path, MAX_SHEET_XML)), events=("end",)):
                if el.tag != M + "row":
                    continue
                vals = []
                for c in el:
                    if c.tag != M + "c":
                        continue
                    t = c.get("t")
                    v = c.find(M + "v")
                    if t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(M + "t"))
                    elif v is None or v.text is None:
                        val = ""
                    elif t == "s":
                        try:
                            val = shared[int(v.text)]
                        except (ValueError, IndexError):
                            val = ""
                    elif t in ("e", "b"):
                        val = ""
                    else:
                        val = v.text
                    if val.strip():
                        vals.append(val.strip())
                        cells += 1
                el.clear()
                if vals:
                    lines.append("\t".join(vals))
                    rows += 1
                if rows >= MAX_SHEET_ROWS or cells >= MAX_SHEET_CELLS:
                    truncated = True
                    break
            if truncated:
                warnings.append((i, f"sheet '{name}' is large: only the first {rows} rows / {cells} cells were indexed"))
            content = f"Sheet: {name}" + ("\n" + "\n".join(lines) if lines else "")
            yield [("text", content, None, None)]

    return len(sheets), gen()


def _decode(data):
    for enc in ("utf-8-sig", "utf-16"):
        if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace")


def _text(path):
    with open(path, "rb") as f:
        data = f.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError("file too large")
    chunks, cur, chars = [], [], 0
    for line in _decode(data).splitlines():
        cur.append(line)
        chars += len(line)
        if len(cur) >= TEXT_LINES or chars >= TEXT_CHARS:
            chunks.append("\n".join(cur))
            cur, chars = [], 0
    if cur:
        chunks.append("\n".join(cur))
    chunks = [c for c in chunks if c.strip()]
    return len(chunks), ([("text", c, None, None)] for c in chunks)


_ZIP_EXTRACTORS = {"docx": _docx, "pptx": _pptx, "xlsx": _xlsx}


def open_units(path, kind, ocr=None, min_px=100, on_phase=None):
    """Returns (n_units, generator, warnings, close). Raises ValueError/OSError for unreadable files."""
    warnings = []
    if kind in ("txt", "md"):
        n, gen = _text(path)
        return n, gen, warnings, lambda: None
    z = SafeZip(path)
    try:
        n, gen = _ZIP_EXTRACTORS[kind](z, ocr, min_px, on_phase, warnings)
    except Exception:
        z.close()
        raise
    return n, gen, warnings, z.close
