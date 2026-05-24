"""Gemensamt interface för OCR/vision-providers."""

from typing import Protocol

from pydantic import BaseModel


class ExtractedMetadata(BaseModel):
    title: str | None = None
    original_title: str | None = None
    composer: str | None = None
    arranger: str | None = None
    lyricist: str | None = None
    voicing: str | None = None
    accompaniment: str | None = None  # a_cappella, piano, organ, other
    publisher: str | None = None
    edition_number: str | None = None
    language: str | None = None

    raw_text: str | None = None
    confidence: float | None = None
    provider: str = ""


class OCRProvider(Protocol):
    name: str

    async def extract(self, image_bytes: bytes) -> ExtractedMetadata: ...


def get_provider(name: str) -> OCRProvider:
    """Factory som returnerar instans för konfigurerad provider."""
    from app.services.ocr.claude_vision import ClaudeVisionProvider
    from app.services.ocr.tesseract import TesseractProvider

    if name == "claude_vision":
        return ClaudeVisionProvider()
    if name == "tesseract":
        return TesseractProvider()
    if name == "hybrid":
        # Hybrid är planerad men inte implementerad än. Fall tillbaka till
        # claude_vision så flödet fungerar utan extra felsökning.
        return ClaudeVisionProvider()
    raise ValueError(f"Okänd OCR-provider: {name}")
