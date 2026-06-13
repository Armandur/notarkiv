from datetime import datetime
from app.utils.dates import now_utc
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class ScanStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    ENRICHING = "enriching"
    DONE = "done"
    FAILED = "failed"


class ScanSession(SQLModel, table=True):
    __tablename__ = "scan_sessions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    image_path: str
    ocr_provider: str
    status: ScanStatus = Field(default=ScanStatus.PENDING, sa_type=String)
    raw_response: str | None = None
    musicbrainz_suggestion: str | None = None
    error_message: str | None = None
    # För-noterad placering från snabbskannings-läget; appliceras vid spara om review-formuläret inte ändrar den
    pre_placement_unit_id: int | None = Field(default=None, foreign_key="storage_units.id")
    pre_placement_copies: int | None = None
    inventory_session_id: int | None = Field(
        default=None, foreign_key="inventory_sessions.id"
    )
    resulting_piece_id: int | None = Field(default=None, foreign_key="pieces.id")
    # Om satt: denna scan är en omkörning av en befintlig piece. Spara
    # uppdaterar målpiecen istället för att skapa en ny.
    target_piece_id: int | None = Field(default=None, foreign_key="pieces.id")
    discarded: bool = Field(default=False)
    discarded_at: datetime | None = None
    discard_reason: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime | None = None
