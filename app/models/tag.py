from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class TagKind(StrEnum):
    LITURGICAL = "liturgical"
    OCCASION = "occasion"
    FREE = "free"


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    kind: TagKind = Field(default=TagKind.FREE, sa_type=String)
    sort_order: int = Field(default=0)


class PieceTag(SQLModel, table=True):
    __tablename__ = "piece_tags"

    piece_id: int = Field(foreign_key="pieces.id", primary_key=True, ondelete="CASCADE")
    tag_id: int = Field(foreign_key="tags.id", primary_key=True, index=True)
