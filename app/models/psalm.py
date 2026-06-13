from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Field, SQLModel, UniqueConstraint


class PsalmBook(SQLModel, table=True):
    """En psalmbok eller sångbok som noter kan referera till.

    En PsalmBook representerar en specifik utgåva. Vill man hantera 1986
    och 2026 års svenska psalmbok som separata referenser skapas två
    poster: 'Den svenska psalmboken' med edition '1986' respektive '2026'.
    """

    __tablename__ = "psalm_books"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    edition: str | None = None  # t.ex. "1986", "1986-tillägg", "2003"
    description: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=now_utc)


class PsalmEntry(SQLModel, table=True):
    """Referensdata: ett konkret psalmnummer i en psalmbok med titel och
    avdelning. Seedas från seed_data/psalms/*.yaml och används som
    autocomplete-lookup när användaren lägger en PiecePsalmRef."""

    __tablename__ = "psalm_entries"
    __table_args__ = (UniqueConstraint("book_id", "edition", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="psalm_books.id", index=True)
    edition: str | None = None
    number: int = Field(index=True)
    title: str
    section: str | None = None


class PiecePsalmRef(SQLModel, table=True):
    """En referens från en not till ett nummer i en psalmbok. En not kan
    finnas i flera psalmböcker - därför många-till-många via egen tabell."""

    __tablename__ = "piece_psalm_refs"
    __table_args__ = (
        UniqueConstraint("piece_id", "book_id", "edition", "number"),
    )

    id: int | None = Field(default=None, primary_key=True)
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    book_id: int = Field(foreign_key="psalm_books.id", index=True)
    edition: str | None = None  # t.ex. "1986", "1986-tillägg", "2003"
    number: int
    created_at: datetime = Field(default_factory=now_utc)
