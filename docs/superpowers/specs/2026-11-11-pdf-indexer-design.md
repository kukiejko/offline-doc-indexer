# PDF File Indexer: Design

## Goal
A local Windows web app that indexes English PDFs (hundreds of files, up to ~10k pages), including text inside images, lets the user search words or phrases, opens the PDF at the matching page in the browser, and stores per-page notes.

## Stack
Python, FastAPI, SQLite FTS5 (porter stemmer), PyMuPDF, Tesseract OCR, PDF.js viewer, watchdog. Binds to localhost only.

## Configuration
`config.toml`: list of PDF folders, Tesseract path, minimum OCR image size, watcher on/off.

## Indexing
- Per PDF, store path, size, mtime, hash. Unchanged files are skipped.
- Per page, extract the text layer (`source=text`).
- OCR embedded images above a minimum size (`source=image_ocr`), keeping bounding boxes.
- Pages with no text layer are OCRed as a whole (`source=page_ocr`).
- Records: `(document, page, source, content)`.
- Triggers: "Rescan" button, CLI command, optional folder watcher with debounce.
- OCR runs in a background worker; the UI shows progress.
- Corrupt or password-protected PDFs, and single-image OCR failures, are logged and listed under "Issues"; they never stop the run.

## Storage (SQLite)
- `documents`: id, path, size, mtime, hash, status.
- `pages_fts` (FTS5): document, page, source, content.
- `notes`: document, page, text, updated_at. Notes are also indexed for search (`source=note`).

## Search
- One box; quoted phrases, stemmed word variants, relevance ranking.
- Results grouped by PDF with highlighted snippets, page number, and source badge (Text / Image / Note).
- Filters: PDF/folder, source type.

## Viewer
- `/view/{doc}?page=N&q=term` opens PDF.js at page N and highlights the term for text-layer hits.
- For image-OCR hits, show the OCR text in a banner; highlight the image region from bounding boxes if feasible.
- PDFs are streamed from configured folders and never modified.

## Notes
- Side panel with a free-text note for the current page, autosaved.
- Pages with notes are marked; a "Notes" list shows all notes across PDFs.

## Testing
Pytest with small fixture PDFs (text layer, text in image, full-page scan). Cover indexing, re-scan skipping, search (phrase, stemming, source badges), and notes (create, update, search).

## Out of scope
Semantic/embedding search, multi-user hosting, writing annotations into PDFs.
