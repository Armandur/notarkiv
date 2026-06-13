from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Field, SQLModel


class ScanSessionImage(SQLModel, table=True):
    """Extra bilder kopplade till en skanning - skannas tillsammans men separat
    från huvudbilden som OCR:as. När piece skapas blir alla till PieceImage.
    """

    __tablename__ = "scan_session_images"

    id: int | None = Field(default=None, primary_key=True)
    scan_session_id: int = Field(
        foreign_key="scan_sessions.id", index=True, ondelete="CASCADE"
    )
    image_path: str
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=now_utc)
