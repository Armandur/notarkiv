"""Claude Vision-baserad metadata-extraktion via tool_use."""

import base64

from anthropic import AsyncAnthropic
from loguru import logger
from sqlmodel import Session, select

from app.db import engine
from app.models import Tag
from app.models.tag import TagKind
from app.services.app_settings import get_anthropic_api_key, get_claude_model
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
    "Använd svenska för voicing och accompaniment där det matchar våra "
    "fasta värden (se beskrivning i verktyget). "
    "Returnera resultatet via verktyget extract_score_metadata."
)


def _current_tag_names(kind: TagKind) -> list[str]:
    """Hämta aktuella tag-namn för given kind, sorterade på sort_order.
    Används för att bygga dynamisk tool-prompt så Claude alltid får
    samma alternativ som finns i admin-listan."""
    with Session(engine) as session:
        return [
            t.name for t in session.exec(
                select(Tag).where(Tag.kind == kind)
                .order_by(Tag.sort_order, Tag.name)
            ).all()
        ]


def _build_tool() -> dict:
    voicings = _current_tag_names(TagKind.VOICING) or ["SATB"]
    accs = _current_tag_names(TagKind.ACCOMPANIMENT) or ["a cappella"]
    return {
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
                    "description": (
                        "Använd ETT av: " + ", ".join(voicings) + ". "
                        "Lämna tomt om besättningen inte tydligt matchar."
                    ),
                },
                "accompaniment": {
                    "type": "string",
                    "description": (
                        "Använd ETT av (svenska): " + ", ".join(accs) + ". "
                        "Lämna tomt om det inte tydligt matchar."
                    ),
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
        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("Anthropic API-nyckel saknas - claude_vision kommer att misslyckas")
        self._client = AsyncAnthropic(api_key=api_key or "missing")

    async def extract(self, image_bytes: bytes) -> ExtractedMetadata:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        tool = _build_tool()
        response = await self._client.messages.create(
            model=get_claude_model(),
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=[tool],
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
