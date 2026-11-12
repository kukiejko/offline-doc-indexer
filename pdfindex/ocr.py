import io
import os
import shutil

_DEFAULT_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def find_tesseract(configured=""):
    for cand in (configured, shutil.which("tesseract"), _DEFAULT_WIN):
        if cand and os.path.isfile(cand):
            return cand
    return None


def make_ocr(configured=""):
    """Return a function png_bytes -> text, or None when Tesseract is unavailable."""
    exe = find_tesseract(configured)
    if not exe:
        return None
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = exe

    def ocr(png_bytes):
        return pytesseract.image_to_string(Image.open(io.BytesIO(png_bytes)), lang="eng")

    return ocr
