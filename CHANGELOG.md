# Changelog

All notable changes to PDF Indexer. Format: [Keep a Changelog](https://keepachangelog.com/), versioning: [SemVer](https://semver.org/).

## [1.3.3] - 2026-11-12

### Changed
- Prepared for public release as **Offline Doc Indexer** (MIT licence, third-party notices, generic `start.bat` using `PDFINDEX_HOME`/`PDFINDEX_CONFIG` instead of a hard-coded path, stricter `.gitignore`). No functional change.

## [1.3.2] - 2026-11-11

### Changed
- Maintenance release following 1.3.1 (page order within a file); version and release timestamp updated, no functional change.

## [1.3.1] - 2026-11-11

### Changed
- Hits inside each file card are now listed in page/unit order (then Text, Image, Note) by default; a **Hits within file** selector switches back to relevance order and is remembered with the last search. The first-5 preview and arrow-key navigation follow the visible order.

## [1.3.0] - 2026-11-11

### Summary
Minor release consolidating 1.2.x: indexing of Word, Excel, PowerPoint, text and Markdown files next to PDFs (1.2.0), continuous multi-page scrolling in the viewer (1.1.1), and a collapsible file filter whose button shows the filter state with colour, label and tooltip (1.2.1). See the 1.2.x entries below for details.

## [1.2.1] - 2026-11-11

### Changed
- The file filter is now a collapsible section: a **▸/▾ File filter: all files / only N of M selected / excluding N** button (aria-expanded) replaces the Files… button; it is collapsed by default and the open/closed state is remembered. The summary updates when you use the **Exclude file** button on a result. The button changes colour with its state: blue (no filter), dark orange with a "•" and the summary text (filter active), darker with an underline bar while open; a tooltip explains the state.

## [1.2.0] - 2026-11-11

### Added
- Indexing of Word (`.docx`), Excel (`.xlsx`, `.xlsm`), PowerPoint (`.pptx`), plain text (`.txt`) and Markdown (`.md`) files next to PDFs, offline and in pure Python (safe XML parsing, no macros executed, no external fetching). Units ("pages"): Word sections, one unit per Excel sheet, one per slide (including speaker notes), blocks of ~80 lines for text.
- Text inside images embedded in Word/PowerPoint files is OCR'd (source badge "Image") when Tesseract is available.
- Viewer for the new types shows the extracted text per unit with search-hit highlighting, continuous scrolling, per-unit notes and an **Open original file** button (served only from the indexed folders).
- `file_types` config option and a **Types** filter in search; type badges (PDF/Word/Excel/PowerPoint/Text/Markdown) on result cards, remembered with the last search.
- Legacy `.doc`, `.xls`, `.ppt` files are listed in Issues as "Unsupported format"; lock files (`~$*`) are skipped.
- Guards: file size caps, zip-bomb limits, XML entity-bomb protection, per-sheet row/cell caps with a visible warning in Issues.

### Changed
- File filter, sorting, folding, move/rename detection, checksum verification, modified-file handling, watcher, progress and orphaned notes work for all file types.
## [1.1.1] - 2026-11-11

### Changed
- PDF viewer scrolls continuously: scrolling past the end of a page continues on the next page (and back to the previous one). Pages render lazily near the viewport, so large PDFs stay fast. The page number, URL, notes panel and OCR banner follow the page in view; search-hit highlights are kept on every page. Previous/Next buttons and the page box still work.
## [1.1.0] - 2026-11-11

### Added
- Help menu with release info: version, release timestamp, changelog, keyboard shortcuts and a README link; `--version`, `/api/version`, `/api/changelog`, `/api/readme`.
- App designer credit ("Designed by Andrzej Grzegorczyk") in Help, `/api/version`, `--version`, README and the `start.bat` banner.
- Sorting of file cards: most hits first (default), fewest hits first, file name A→Z / Z→A; remembered with the last search.
- `start.bat` launcher for the collection folder (first-run setup, browser auto-open, `start.bat scan`, `PORT` override).

### Changed
- Result cards are folded by default; unfold per file or with Unfold all.

## [1.0.0] - 2026-11-11

### Added
- Indexing of PDF text and text inside images (OCR via Tesseract) with full-text search (phrases, stemming, ranking) and source badges (text / image / note).
- Split main page: results on top, PDF viewer below with page highlight, OCR banner and per-page notes; draggable splitter and keyboard navigation (Up/Down).
- Per-page notes stored in SQLite and searchable; Issues tab; Orphaned notes list with manual relink.
- File filter (only / exclude selected files), quick "Exclude file" on results.
- Results grouped per file with Fold all / Unfold all and "Show all N hits".`n- "Back to search" in the viewer and remembered last search (query, filters, fold state, sort, scroll).
- Manual Index / Rescan with page-level progress, ETA, cancel; modes: new/changed, verify checksums, full re-index.
- Move/rename detection that keeps document id, notes and indexed text; "possibly moved" issues.
- Modified-file detection by checksum with "document changed since note was written" warnings.
- Folder watcher (optional), including move and modify events.
- Everything stored next to `config.toml` (relative paths resolve against the config file).
- Offline operation: PDF.js bundled, no network at runtime; localhost-only with host/origin checks and CSP.



