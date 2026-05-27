from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class LoanBatchStatus(StrEnum):
    """Livscykel för en grupperad utlåning."""

    CART = "cart"          # Användarens kundvagn - ännu inte registrerad
    PICKING = "picking"    # Registrerad, noter ska hämtas fysiskt
    ACTIVE = "active"      # Alla noter hämtade, lånet pågår
    RETURNED = "returned"  # Alla noter återlämnade


class LoanBatch(SQLModel, table=True):
    """Grupp av Loan-poster som lånats ut tillsammans.

    En cart-batch per användare fungerar som kundvagn innan registrering.
    När användaren bekräftar flippas status till picking och alla
    metadata-fält (borrower, datum, namn) blir obligatoriska.
    """

    __tablename__ = "loan_batches"

    id: int | None = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id", index=True)
    status: str = Field(default=LoanBatchStatus.CART, index=True)

    name: str | None = None  # Obligatoriskt vid checkout, null i cart
    borrower_name: str | None = None
    borrower_user_id: int | None = Field(default=None, foreign_key="users.id")

    borrowed_at: datetime | None = None  # Sätts vid övergång till picking
    expected_return_at: datetime | None = None
    notes: str | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    registered_at: datetime | None = None  # cart -> picking
    activated_at: datetime | None = None   # picking -> active
    returned_at: datetime | None = None    # active -> returned
