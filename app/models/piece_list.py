from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Field, SQLModel, UniqueConstraint


class PieceList(SQLModel, table=True):
    """En privat samling noter per användare.

    Default skapas en "Favoriter"-lista (is_favorites=True) per användare vid
    deras första login. Användaren kan skapa fler listor t.ex. "Konsert 14
    juni" eller "Bröllop-favoriter". Listor är alltid privata i v1 - ingen
    delning mellan användare.
    """

    __tablename__ = "piece_lists"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    name: str
    description: str | None = None
    is_favorites: bool = Field(default=False)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class PieceListItem(SQLModel, table=True):
    """Koppling piece <-> list. Sort-order låter användaren ordna om
    manuellt i lista-detaljvyn. Many-to-many med extra metadata."""

    __tablename__ = "piece_list_items"
    __table_args__ = (UniqueConstraint("list_id", "piece_id"),)

    id: int | None = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="piece_lists.id", index=True, ondelete="CASCADE")
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    sort_order: int = Field(default=0)
    added_at: datetime = Field(default_factory=now_utc)
