from datetime import datetime

from sqlmodel import Field, SQLModel


class InventorySession(SQLModel, table=True):
    """Ett inventeringstillfälle - en grupp skanningar gjorda i ett bestämt sammanhang.

    Modellen är medvetet enkel: en aktiv session i taget globalt (säkerställs av
    applikationen vid create), och alla `quick`-skanningar under sessionen knyts
    automatiskt till den.
    """

    __tablename__ = "inventory_sessions"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    description: str | None = None
    planned_location_id: int | None = Field(
        default=None, foreign_key="storage_locations.id"
    )
    planned_unit_id: int | None = Field(default=None, foreign_key="storage_units.id")
    log: str | None = None  # Append-only fritext med datumstämplar
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    started_by: int | None = Field(default=None, foreign_key="users.id")
