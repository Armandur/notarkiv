from datetime import datetime

from sqlmodel import Field, SQLModel


class StorageUnitImage(SQLModel, table=True):
    """Flera foton per lagringsenhet - t.ex. pärmens framsida + rygg så
    användaren kan identifiera vilken fysisk pärm/hylla som avses vid
    inventering. Den med lägst sort_order är "primär"."""

    __tablename__ = "storage_unit_images"

    id: int | None = Field(default=None, primary_key=True)
    storage_unit_id: int = Field(
        foreign_key="storage_units.id", index=True, ondelete="CASCADE"
    )
    image_path: str  # Relativ mot IMAGES_PATH
    label: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
