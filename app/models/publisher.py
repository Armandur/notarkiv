from datetime import datetime

from sqlmodel import Field, SQLModel


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
    # Kopplingar till externa datakällor - hämtas och fylls automatiskt
    # via berikning likt Person-flödet. MB Labels har en stor katalog
    # över notutgivare (Verbum, Gehrmans, Carus, Bärenreiter etc.).
    musicbrainz_label_id: str | None = Field(default=None, index=True)
    wikidata_id: str | None = Field(default=None, index=True)
    # Sätts när berikning kört senast (None = aldrig berikad)
    enriched_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
