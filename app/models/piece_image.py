from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class PieceImageKind(StrEnum):
    COVER = "cover"
    BACK = "back"
    TITLE_PAGE = "title_page"
    INSIDE = "inside"
    OTHER = "other"


class PieceImage(SQLModel, table=True):
    """Flera bilder per not - framsida, baksida, försättsblad m.m.

    Den med lägst sort_order är "primär" (visas i listor som thumbnail).
    """

    __tablename__ = "piece_images"

    id: int | None = Field(default=None, primary_key=True)
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    image_path: str  # Relativ mot IMAGES_PATH
    kind: PieceImageKind = Field(default=PieceImageKind.COVER, sa_type=String)
    label: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
