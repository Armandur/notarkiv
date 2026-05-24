from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: datetime | None = None

    @property
    def can_edit(self) -> bool:
        return self.role in (Role.EDITOR, Role.ADMIN)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN
