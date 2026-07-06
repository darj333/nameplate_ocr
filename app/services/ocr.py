"""OCR service — wraps EasyOCR with a Tesseract fallback."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_reader = None  # lazy-loaded so import doesn't slow startup


def _get_reader(languages: list[str]):
    global _reader
    if _reader is None:
        import easyocr  # noqa: PLC0415

        logger.info("Initialising EasyOCR reader (first call, may take a moment)…")
        _reader = easyocr.Reader(languages, gpu=False)
    return _reader


def run_ocr(image_path: str | Path, languages: str = "en") -> str:
    """
    Run EasyOCR on *image_path* and return all detected text as a single string.
    Falls back to pytesseract if EasyOCR is unavailable.
    """
    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    path = str(image_path)

    try:
        reader = _get_reader(lang_list)
        results = reader.readtext(path, detail=0, paragraph=True)
        return "\n".join(results)
    except Exception as easyocr_err:
        logger.warning("EasyOCR failed (%s), trying pytesseract fallback…", easyocr_err)
        try:
            import pytesseract  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            img = Image.open(path)
            return pytesseract.image_to_string(img)
        except Exception as tess_err:
            raise RuntimeError(
                f"Both OCR engines failed. EasyOCR: {easyocr_err}; Tesseract: {tess_err}"
            ) from tess_err
