"""Claude Vision-baserad metadata-extraktion via tool_use."""

import base64

from anthropic import AsyncAnthropic
from loguru import logger

from app.config import settings
from app.services.ocr.base import ExtractedMetadata

_SYSTEM_PROMPT = (
    "Du är expert på att extrahera metadata från klassiska och moderna notomslag. "
    "Användaren visar en bild av framsidan på en notpost. "
    "Extrahera fält så noggrant du kan - lämna fält tomma om du inte är säker, gissa inte. "
    "Tänk på att: titlar kan vara flerspråkiga (original + översättning); "
    "kompositör anges ofta som 'av X' eller 'Music by X'; "
    "arrangör som 'Arr. X' eller 'Arranged by X'; "
    "besättning står ofta som 'SATB', 'for mixed choir', 'SSA' osv.; "
    "förlagsnummer är vanligen i hörnet, t.ex. 'GH-1234'. "
    "Returnera resultatet via verktyget extract_score_metadata."
)

_TOOL = {
    "name": "extract_score_metadata",
    "description": "Spara extraherad metadata från ett notomslag",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Huvudtitel"},
            "original_title": {"type": "string", "description": "Originaltitel om olika"},
            "composer": {"type": "string"},
            "arranger": {"type": "string"},
            "lyricist": {"type": "string"},
            "voicing": {
                "type": "string",
                "description": "SATB, SAB, SSA, unison, solo, etc.",
            },
            "accompaniment": {
                "type": "string",
                "enum": ["a_cappella", "piano", "organ", "other"],
            },
            "publisher": {"type": "string"},
            "edition_number": {"type": "string"},
            "language": {
                "type": "string",
                "description": "ISO 639-1, t.ex. sv, en, la, de",
            },
        },
    },
}


class ClaudeVisionProvider:
    name = "claude_vision"

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            logger.warning("ANTHROPIC_API_KEY saknas - claude_vision kommer att misslyckas")
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key or "missing")

    async def extract(self, image_bytes: bytes) -> ExtractedMetadata:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        response = await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "extract_score_metadata"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extrahera metadata från detta notomslag.",
                        },
                    ],
                }
            ],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_score_metadata":
                data = block.input or {}
                logger.info("claude_vision extraherade: {}", list(data.keys()))
                return ExtractedMetadata(provider=self.name, **data)

        logger.warning("claude_vision returnerade inget tool_use-block")
        return ExtractedMetadata(provider=self.name)
