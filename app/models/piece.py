from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class CopyrightStatus(StrEnum):
    ORIGINAL = "original"
    LICENSED_COPY = "licensed_copy"
    PUBLIC_DOMAIN = "public_domain"
    UNKNOWN = "unknown"


class Piece(SQLModel, table=True):
    __tablename__ = "pieces"

    id: int | None = Field(default=None, primary_key=True)

    title: str
    original_title: str | None = None
    # Denormaliserad cache för sökning/listning - byggs från PieceContributor.
    # Format: "Felix Mendelssohn (composer); Bob Smith (arranger)"
    contributors_cache: str | None = None
    language: str | None = None
    publisher: str | None = None
    edition_number: str | None = None
    difficulty: int | None = None
    duration_seconds: int | None = None
    copyright_status: CopyrightStatus | None = Field(default=None, sa_type=String)
    musicbrainz_work_id: str | None = None
    notes: str | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: int | None = Field(default=None, foreign_key="users.id")
