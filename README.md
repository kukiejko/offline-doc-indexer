# Offline Doc Indexer

Local Windows web app (formerly "PDF Indexer"): index English PDFs, Word, Excel, PowerPoint, text and Markdown files (including text inside images via OCR), search words or phrases, see the matching PDF page right below the results, and keep per-page notes (searchable). Works fully offline. Designed by Andrzej Grzegorczyk. Licensed under MIT, see `LICENSE` and `THIRD_PARTY_NOTICES.md`. Design: `docs/superpowers/specs/2026-11-11-pdf-indexer-design.md`.

## Quick start

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.toml config.toml          # then edit "folders"
.venv\Scripts\python -m pdfindex serve        # open http://127.0.0.1:8000 and press Index / Rescan
```

## Setup

1. Python 3.11+.
2. Create the environment and install:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt       # runtime only (pinned)
   .venv\Scripts\pip install -r requirements-dev.txt   # adds pytest and httpx for tests
   ```
3. **Tesseract OCR** (optional; only needed for text inside images and scanned pages):
   - Download the Windows installer from https://github.com/UB-Mannheim/tesseract/wiki and install it with the English language data.
   - The app finds `tesseract` on PATH or at `C:\Program Files\Tesseract-OCR\tesseract.exe`. For a per-user install, set the path in `config.toml`, for example:
     `tesseract_path = "C:/Users/<user>/AppData/Local/Tesseract-OCR/tesseract.exe"`
   - Without Tesseract everything still works, but only the PDF text layer is indexed. A warning is shown on the console and in the app.
   - When Tesseract is installed (or its path/settings change) later, the next rescan re-indexes the affected PDFs automatically.
4. **Offline:** PDF.js 4.7.76 (Apache-2.0) is bundled in `pdfindex/static/vendor/` and served by the app; there are no CDN scripts, fonts or styles. Internet is only needed for the one-time install of Python packages and Tesseract.

## Configuration

`config.toml` (default: the current directory; choose another with `--config`). See `config.example.toml`.

| Key | Meaning |
|---|---|
| `folders` | PDF folders, searched recursively |
| `tesseract_path` | Path to `tesseract.exe`; empty = auto-detect |
| `min_ocr_image_px` | OCR only images at least this wide and tall (default 100) |
| `file_types` | file types to index, default all: `["pdf", "docx", "xlsx", "pptx", "txt", "md"]` |
| `watcher` | `true` = rescan automatically when indexed files change, are moved or renamed (with `serve`) |
| `verify_checksums` | `true` = every scan hashes all files, so edits that keep size and date are found (slower) |
| `db_path` | SQLite file, default `pdfindex.db` |

Relative `db_path` and `folders` are resolved against the **config file's folder**, not the working directory. Keeping `config.toml` inside your PDF collection therefore stores everything there: the single SQLite file `pdfindex.db` holds the index, OCR text, notes and issues (plus `-wal`/`-shm` side files while the app runs). Nothing else is persisted. PDFs are never modified.

## Run

```
.venv\Scripts\python -m pdfindex serve --config D:\pdfs\config.toml [--port 8000]   # http://127.0.0.1:8000
.venv\Scripts\python -m pdfindex scan  --config D:\pdfs\config.toml                 # index from the command line
```

Do not run two scans against the same database at once.

## Using the app

- **Split main page:** search and results on top, the PDF viewer below (with page highlight, OCR banner and a per-page notes panel). Click a result, or use **Up/Down** arrows to move through results (Down from the search box jumps to the first) and load each one; the selected result is highlighted. Drag the splitter, or focus it and use Up/Down (Shift = larger steps, Home/End), to resize; the size is remembered.
- **Search:** words are stemmed (`pump` finds `pumps`), `"quoted phrases"` match exactly. Results are ranked and carry badges: Text, Image (OCR), Note. Filter by source or folder path prefix.
- **File filter:** the **▸ File filter** button (it shows a summary such as "all files", "only 11 of 92 selected" or "excluding 3"; collapsed by default, state remembered; the button turns dark orange with a "•" while a filter is active and shows a pressed style while open) opens a searchable list of indexed PDFs (grouped by folder, with Select all / Clear / Invert on the visible files). Choose **Search only in selected files** or **Exclude selected files**. An active chip ("N files selected / excluded") with a ✕ clears it. Each result group also has an **Exclude file** button.
- **Fold / unfold per file:** results start **folded** (one header per file: "name · N hits"). Click a header to unfold; **Fold all** / **Unfold all** sit above the results. Unfolded cards with more than 5 hits show the first 5 with a **Show all N hits** button. Fold state is remembered with the last search (same query), and Up/Down navigation skips hidden hits.
- **Sort files:** *Most hits first* (default), *Fewest hits first*, *File name A→Z / Z→A*; remembered with the last search.
- **start.bat:** a launcher (`scripts\start.bat`; copy it into your collection folder next to `config.toml`) that finds the program via `PDFINDEX_HOME` (default: the parent of the script's folder, so set it when the script is copied elsewhere) and uses `config.toml` next to it (or `PDFINDEX_CONFIG`), creates `.venv` and installs requirements on first run, starts the server and opens http://127.0.0.1:8000 once it answers. `start.bat scan` only indexes. Set `PORT=8001` to use another port; it stops with a message if the port is busy or Python is missing.
- **Hit order:** hits inside a file card are listed in page order by default (selector **Hits within file**: Page order / Relevance; remembered).
- **Remembered search:** the last query, filters, file filter, scroll position and selected result are restored when you reopen the page.
- **Continuous scrolling:** the viewer shows all pages in one scrollable column; scroll past the end of a page to continue on the next (and back up). Pages render lazily near the viewport, so large PDFs stay fast. The page number, notes panel and OCR banner follow the page in view; the ◀ ▶ buttons and the page box still jump to a page.`n- **Standalone viewer:** Ctrl/Shift/middle-click a result (or open `/view/<id>?page=N`) for the full-page viewer, which has a **← Back to search** link and shows the searched term. Text hits are highlighted; image hits show the recognised text in a banner and outline the image.
- **Notes:** autosaved per page; the **Notes** tab lists all of them and they are searchable (Note badge).
- **Issues tab:** corrupt or password-protected PDFs and OCR failures. Unchanged errored PDFs are not retried on every scan.
- **Modified PDFs**: the fast scan compares size and date and only hashes files that differ (same hash + new date = just the date is updated). Choose **Verify checksums** in the Index panel (or `verify_checksums = true`, or `scan --verify`) to hash every file and catch edits that keep size and date; progress shows a "Checking checksum" phase. A changed file is fully re-indexed (text + OCR) keeping its id and notes; its notes get a "Document changed since this note was written" warning (in the viewer and Notes list) that you can dismiss, and editing the note clears it. The scan summary shows the modified count and lists the files.
- **Moved / renamed PDFs** are detected during a scan: a new path with the same size and content hash as a missing document relinks the existing entry (same id, notes and indexed text, no re-OCR). Identical duplicates are matched one-to-one, preferring the same file name. A moved *and edited* file is not relinked silently: it is indexed as new and an Issue "Possibly moved" is added. PDFs that were really deleted become *missing* (hidden from search); their notes are kept under **Notes → Orphaned notes**, where they can be relinked to another indexed file. The scan summary shows moved and missing counts, and the watcher also reacts to move/rename and folder-move events.
- **Index / Rescan** button (header) opens the indexing panel: choose *new/changed files only* (fast; unchanged files are skipped) or *full re-index incl. OCR*, then Start. The panel shows an overall page progress bar, files/pages done, current file and page, phase (text / OCR), elapsed time and rough ETA, OCR image, error and issue counts, and a **Cancel** button (stops at the next page; the PDF in progress is not half-indexed). Only one run at a time; progress continues after a page reload, and a summary (indexed/skipped/failed, duration) is shown when finished. The watcher uses the changed-only mode. API: `POST /api/rescan?mode=changed|full`, `POST /api/scan/cancel`, `GET /api/status`.

- **Office and text files:** besides PDFs the app indexes **Word (.docx), Excel (.xlsx/.xlsm), PowerPoint (.pptx), .txt and .md** (pure Python, offline; macros are never run). A *unit* replaces the PDF page: Word section (split at headings, ~40 paragraphs), Excel sheet (cell text, sheet name; capped at 50,000 rows / 500,000 cells per sheet with a warning in Issues), PowerPoint slide incl. speaker notes, text/Markdown block of ~80 lines. Images embedded in Word/PowerPoint are OCR'd like PDF images (badge *Image*). Results show a type badge and the **Types** checkboxes filter by type (also saved with the last search). The viewer shows the extracted text with highlights, per-unit notes and **Open original file** (downloads the file from the indexed folders only). Legacy .doc/.xls/.ppt files are listed in **Issues** as *Unsupported format*; ~$ lock files are skipped; files over 100 MB (50 MB for text) or suspicious zip archives are refused with an Issue. Excel/Word/PowerPoint viewing is text only, not the original layout.

## Security

The server binds to `127.0.0.1` only and accepts only `localhost`/`127.0.0.1` Host headers. Mutating requests from another origin are rejected. A strict Content-Security-Policy (same-origin only, framing only by itself) and other security headers are set. Symlinked PDFs and files outside the configured folders are never served.

## Tests

```
.venv\Scripts\python -m pytest
```

OCR is replaced by a fake in tests, so Tesseract is not required to run them. One symlink test is skipped when Windows symlink permission is missing.

## Releasing

1. Bump `__version__` and `RELEASE_TIMESTAMP` in `pdfindex/__version__.py` (SemVer `X.Y.Z`, ISO 8601 timestamp).
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md` (Added / Changed / Fixed).
3. Run `python -m pytest -q` (a test checks the changelog has an entry for the current version), commit, then merge.

The version, timestamp and changelog are shown under **Help** in the app, at `/api/version` and `/api/changelog`, and via `python -m pdfindex --version`; `start.bat` prints the version at startup.


