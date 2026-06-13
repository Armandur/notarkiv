from datetime import datetime
from app.utils.dates import now_utc
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class PublisherLinkKind(StrEnum):
    WIKIPEDIA = "wikipedia"
    WIKIDATA = "wikidata"
    MUSICBRAINZ = "musicbrainz"
    IMSLP = "imslp"
    OFFICIAL = "official"
    OTHER = "other"


class Publisher(SQLModel, table=True):
    """Notutgivare som strukturerad entitet (Verbum, Gehrmans, Carus,
    etc.). Ersätter `Piece.publisher`-fritextfältet med en dedupad
    referens så stavningsvarianter ("Verbum" / "Verbum AB" / "Verbum
    Förlag") räknas som samma."""

    __tablename__ = "publishers"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    # Sorterbar variant - typiskt samma som name men kan vara t.ex.
    # "Gehrmans Musikförlag" för dem som vill ha särskild sortering
    sort_name: str = Field(index=True)
    country: str | None = None  # ISO-3166-1 alpha-2 om relevant
    website_url: str | None = None
    description: str | None = None
    # Källa-URL för beskrivningen (för CC BY-SA-attribution när texten
    # kommer från Wikipedia). Sätts av enrich_publisher_from_mb.
    description_source_url: str | None = None
    # Kopplingar till externa datakällor - hämtas och fylls automatiskt
    # via berikning likt Person-flödet. MB Labels har en stor katalog
    # över notutgivare (Verbum, Gehrmans, Carus, Bärenreiter etc.).
    musicbrainz_label_id: str | None = Field(default=None, index=True)
    wikidata_id: str | None = Field(default=None, index=True)
    # Sätts när berikning kört senast (None = aldrig berikad)
    enriched_at: datetime | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class PublisherLink(SQLModel, table=True):
    """Externa länkar knutna till ett förlag - Wikipedia, IMSLP,
    officiella hemsidor, andra. Skapas vid MB-berikning från URL-rels."""

    __tablename__ = "publisher_links"

    id: int | None = Field(default=None, primary_key=True)
    publisher_id: int = Field(foreign_key="publishers.id", index=True, ondelete="CASCADE")
    url: str
    kind: PublisherLinkKind = Field(default=PublisherLinkKind.OTHER, sa_type=String)
    label: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=now_utc)
