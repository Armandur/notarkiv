from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


class PieceUserNote(SQLModel, table=True):
    """En anteckning per användare per not.

    Tänkt för körledarens egna kommentarer om tonart, tempo, repetitionsnoter
    eller liknande som inte hör hemma i den gemensamma notes-texten.
    """

    __tablename__ = "piece_user_notes"
    __table_args__ = (UniqueConstraint("piece_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    text: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
