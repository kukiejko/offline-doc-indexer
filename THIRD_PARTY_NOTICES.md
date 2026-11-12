# Third-party notices

Offline Doc Indexer itself is MIT licensed (see `LICENSE`). It uses the following third-party components.

## Bundled in this repository

- **PDF.js** 4.7.76, Apache License 2.0, (c) Mozilla and contributors. Files `pdfindex/static/vendor/pdf.min.mjs` and `pdf.worker.min.mjs`; the licence text is kept in `pdfindex/static/vendor/LICENSE`. https://github.com/mozilla/pdf.js

## Python dependencies (installed with pip, not bundled; pinned in `requirements.txt`)

| Package | Version | Licence |
|---|---|---|
| FastAPI | 0.162.4 | MIT |
| Starlette | 1.10.0 | BSD-3-Clause |
| Pydantic | 2.14.3 | MIT |
| Uvicorn | 0.54.0 | BSD-3-Clause |
| PyMuPDF | 2.1 | **GNU AGPL-3.0** or Artifex commercial licence |
| pytesseract | 0.3.13 | Apache-2.0 |
| Pillow | 13.0.0 | MIT-CMU (HPND) |
| watchdog | 7.1.0 | Apache-2.0 |
| defusedxml | 0.7.1 | Python Software Foundation License |

Important: **PyMuPDF is AGPL-3.0 (or commercial).** Using it locally is fine; if you distribute or offer this program as a network service together with PyMuPDF, the AGPL terms apply to the combined work unless you hold a commercial PyMuPDF licence. Development dependencies (`requirements-dev.txt`, e.g. pytest) are not distributed.

## Optional external tool

- **Tesseract OCR** (Apache-2.0) is not included; install it separately for OCR.
