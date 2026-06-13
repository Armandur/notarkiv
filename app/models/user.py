import secrets
from datetime import datetime
from app.utils.dates import now_utc
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


def _new_kiosk_token() -> str:
    return secrets.token_hex(16)


class Role(StrEnum):
    READER = "reader"
    EDITOR = "editor"
    ADMIN = "admin"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str | None = None
    password_hash: str
    role: Role = Field(default=Role.READER, sa_type=String)
    must_change_password: bool = Field(default=False)
    # Hashad PIN för kiosk-autentisering (4-8 siffror). None = ej satt.
    pin_hash: str | None = None
    # Token för QR-baserad kiosk-auth. Slumpat hex, syns på /profile.
    # QR-innehåll: "u:<token>" så kioskens scanner-input kan skilja det
    # från piece-QR (rena hex utan prefix).
    kiosk_token: str | None = Field(
        default_factory=_new_kiosk_token, unique=True, index=True
    )
    created_at: datetime = Field(default_factory=now_utc)
    last_login_at: datetime | None = None

    @property
    def can_edit(self) -> bool:
        return self.role in (Role.EDITOR, Role.ADMIN)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN
