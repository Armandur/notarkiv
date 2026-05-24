"""Tesseract-baserad OCR. Returnerar främst raw_text; minimal fältsplit."""

import io
import re

import pytesseract
from loguru import logger
from PIL import Image

from app.services.ocr.base import ExtractedMetadata


class TesseractProvider:
    name = "tesseract"

    async def extract(self, image_bytes: bytes) -> ExtractedMetadata:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "L":
            img = img.convert("L")

        try:
            text = pytesseract.image_to_string(img, lang="swe+eng+lat")
        except pytesseract.TesseractError as exc:
            logger.warning("Tesseract misslyckades: {}", exc)
            text = ""

        return _parse_text(text)


def _parse_text(text: str) -> ExtractedMetadata:
    """Heuristisk parsning. Återvänder ExtractedMetadata med raw_text alltid."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    title = lines[0] if lines else None
    composer = _find(text, r"(?:av|by|music\s+by)\s+([A-ZÅÄÖ][^\n,]{2,40})", group=1)
    arranger = _find(text, r"(?:arr\.?|arranged\s+by)\s+([A-ZÅÄÖ][^\n,]{2,40})", group=1)
    voicing = _find(text, r"\b(SATB|SSAA|TTBB|SAB|SSA|TB|unison)\b")
    edition_number = _find(text, r"\b([A-Z]{1,5}[\s\-]?\d{2,6})\b")

    return ExtractedMetadata(
        provider="tesseract",
        title=title,
        composer=composer,
        arranger=arranger,
        voicing=voicing,
        edition_number=edition_number,
        raw_text=text,
    )


def _find(text: str, pattern: str, group: int = 0) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    return m.group(group).strip()
