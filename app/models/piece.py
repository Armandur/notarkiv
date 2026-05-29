import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class CopyrightStatus(StrEnum):
    ORIGINAL = "original"
    LICENSED_COPY = "licensed_copy"
    PUBLIC_DOMAIN = "public_domain"
    UNKNOWN = "unknown"


def _new_public_id() -> str:
    return uuid.uuid4().hex


class Piece(SQLModel, table=True):
    __tablename__ = "pieces"

    id: int | None = Field(default=None, primary_key=True)
    # Stabilt ID för QR-koder och etiketter. Slumpat UUID4 utan bindestreck
    # (32 tecken) så det blir kortare i URLs och scannerinput.
    public_id: str | None = Field(
        default_factory=_new_public_id, unique=True, index=True
    )

    title: str
    original_title: str | None = None
    # Denormaliserad cache för sökning/listning - byggs från PieceContributor.
    # Format: "Felix Mendelssohn (composer); Bob Smith (arranger)"
    contributors_cache: str | None = None
    language: str | None = None
    publisher: str | None = None  # legacy fritext, ersatt av publisher_id
    publisher_id: int | None = Field(default=None, foreign_key="publishers.id", index=True)
    edition_number: str | None = None
    difficulty: int | None = None
    duration_seconds: int | None = None
    copyright_status: CopyrightStatus | None = Field(default=None, sa_type=String)
    musicbrainz_work_id: str | None = None
    spotify_url: str | None = None
    notes: str | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: int | None = Field(default=None, foreign_key="users.id")
