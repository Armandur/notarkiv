from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Field, SQLModel


class AppSetting(SQLModel, table=True):
    """Enkel key-value-store för inställningar som ska gå att ändra via admin-UI:t.

    Värden lagras som text. Hemligheter (API-nycklar) lagras i klartext - skydda
    SQLite-filen via filperms och separat backup-disciplin.
    """

    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: str | None = None
    updated_at: datetime = Field(default_factory=now_utc)
    updated_by: int | None = Field(default=None, foreign_key="users.id")
