from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class TagKind(StrEnum):
    OCCASION = "occasion"
    VOICING = "voicing"
    ACCOMPANIMENT = "accompaniment"
    FREE = "free"


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    kind: TagKind = Field(default=TagKind.FREE, sa_type=String)
    description: str | None = None
    sort_order: int = Field(default=0)
    # Hierarki: en tagg kan ha en parent inom samma kind, t.ex.
    # "Kyrkliga handlingar" > {"Begravning", "Vigsel", "Dop"}.
    parent_id: int | None = Field(default=None, foreign_key="tags.id", index=True)


class TagAlias(SQLModel, table=True):
    """Alternativnamn för en tagg så fritextsökning hittar samma tagg
    via olika benämningar (t.ex. 'Minnesgudstjänst' → 'Allhelgona')."""

    __tablename__ = "tag_aliases"

    id: int | None = Field(default=None, primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", index=True, ondelete="CASCADE")
    name: str = Field(unique=True, index=True)


class PieceTag(SQLModel, table=True):
    __tablename__ = "piece_tags"

    piece_id: int = Field(foreign_key="pieces.id", primary_key=True, ondelete="CASCADE")
    tag_id: int = Field(foreign_key="tags.id", primary_key=True, index=True)
