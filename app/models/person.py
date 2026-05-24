from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class ContributorRole(StrEnum):
    COMPOSER = "composer"
    ARRANGER = "arranger"
    LYRICIST = "lyricist"
    EDITOR = "editor"
    CONDUCTOR = "conductor"
    OTHER = "other"


class Person(SQLModel, table=True):
    """En person knuten till en eller flera noter (kompositör, arrangör, textförfattare)."""

    __tablename__ = "people"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sort_name: str = Field(index=True)
    birth_year: int | None = None
    death_year: int | None = None
    biography: str | None = None
    wikipedia_url: str | None = None
    musicbrainz_artist_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PieceContributor(SQLModel, table=True):
    """Länk mellan en not och en person, med roll."""

    __tablename__ = "piece_contributors"

    id: int | None = Field(default=None, primary_key=True)
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    person_id: int = Field(foreign_key="people.id", index=True)
    role: ContributorRole = Field(sa_type=String)
    sort_order: int = Field(default=0)
