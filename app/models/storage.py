from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel, UniqueConstraint


class LocationKind(StrEnum):
    PHYSICAL = "physical"
    DIGITAL = "digital"


class StorageLocation(SQLModel, table=True):
    __tablename__ = "storage_locations"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    kind: LocationKind = Field(default=LocationKind.PHYSICAL, sa_type=String)
    description: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UnitKind(SQLModel, table=True):
    """Typ av förvaringsenhet, t.ex. hylla, pärm, låda, mapp.

    Användare skapar nya kinds genom att skriva i autocomplete-fältet.
    """

    __tablename__ = "unit_kinds"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class StorageUnit(SQLModel, table=True):
    __tablename__ = "storage_units"

    id: int | None = Field(default=None, primary_key=True)
    location_id: int = Field(foreign_key="storage_locations.id", index=True)
    parent_id: int | None = Field(default=None, foreign_key="storage_units.id", index=True)
    name: str
    kind_id: int | None = Field(default=None, foreign_key="unit_kinds.id")
    sort_order: int = Field(default=0)
    archived: bool = Field(default=False)
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PiecePlacement(SQLModel, table=True):
    __tablename__ = "piece_placements"
    __table_args__ = (UniqueConstraint("piece_id", "storage_unit_id"),)

    id: int | None = Field(default=None, primary_key=True)
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    storage_unit_id: int = Field(foreign_key="storage_units.id", index=True)
    copies: int | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
