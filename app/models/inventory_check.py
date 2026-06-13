from datetime import datetime
from app.utils.dates import now_utc
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class CheckStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    FOUND = "found"
    PARTIAL = "partial"   # Hittat färre exemplar än förväntat
    MISSING = "missing"
    EXTRA = "extra"       # Hittade fler än förväntat


class InventoryCheck(SQLModel, table=True):
    """En kontroll av en specifik placering inom ett inventeringstillfälle.

    Senaste posten för en given (inventory_session_id, placement_id) gäller -
    inga unique-constraints så historiken bevaras om någon checkar om.
    """

    __tablename__ = "inventory_checks"

    id: int | None = Field(default=None, primary_key=True)
    inventory_session_id: int = Field(
        foreign_key="inventory_sessions.id", index=True, ondelete="CASCADE"
    )
    placement_id: int = Field(foreign_key="piece_placements.id", index=True)
    status: CheckStatus = Field(default=CheckStatus.NOT_CHECKED, sa_type=String)
    actual_copies: int | None = None
    notes: str | None = None
    checked_at: datetime = Field(default_factory=now_utc)
    checked_by: int | None = Field(default=None, foreign_key="users.id")
