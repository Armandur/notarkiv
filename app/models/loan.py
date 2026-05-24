from datetime import datetime

from sqlmodel import Field, SQLModel


class Loan(SQLModel, table=True):
    """En registrering av att N exemplar lånats från en specifik placering.

    Aktiva utlån har returned_at = NULL. Borrower lagras som fritext eftersom
    inte alla låntagare är användare i systemet (besökande musiker mm).
    """

    __tablename__ = "loans"

    id: int | None = Field(default=None, primary_key=True)
    placement_id: int = Field(
        foreign_key="piece_placements.id", index=True, ondelete="CASCADE"
    )
    borrower_name: str
    borrower_user_id: int | None = Field(default=None, foreign_key="users.id")
    copies: int = Field(default=1)
    notes: str | None = None
    borrowed_at: datetime = Field(default_factory=datetime.utcnow)
    expected_return_at: datetime | None = None
    returned_at: datetime | None = None
    registered_by: int | None = Field(default=None, foreign_key="users.id")
