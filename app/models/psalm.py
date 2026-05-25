from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


class PsalmBook(SQLModel, table=True):
    """En psalmbok eller sångbok som noter kan referera till.

    Användaren skapar och underhåller dessa via admin-vyn. Ingen kuraterad
    seed - varje församling kan ha olika repertoar.
    """

    __tablename__ = "psalm_books"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
