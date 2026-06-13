"""Kiosk-enheter: en fysisk dator/skärm med sin egen identitet och plats.

Att modellera kiosken som en separat entitet (inte som en User) gör att:
- Kioskdatorn behöver inte vara inloggad som någon person
- Den faktiska låntagaren autentiserar sig per session (PIN/QR-token)
- Kioskens lagringsplats styr vilka noter som kan lånas härifrån
- Flera kiosker kan finnas (en per fysisk plats) utan role-mixar
"""

import secrets
from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Field, SQLModel


def _new_access_token() -> str:
    return secrets.token_hex(16)


class Kiosk(SQLModel, table=True):
    __tablename__ = "kiosks"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: str | None = None
    # Optional: knyt kiosken till en lagringsplats. Bara noter med
    # placering inom platsens hierarki kan lånas härifrån.
    location_id: int | None = Field(default=None, foreign_key="storage_locations.id")
    # Token för att aktivera kiosken på en fysisk enhet. Besökande
    # /kiosk/activate?token=X sätter session-cookie permanent.
    access_token: str = Field(
        default_factory=_new_access_token, unique=True, index=True
    )
    # Om satt: kioskens skanningar registreras som inventering-checks
    # på sessionen. Editor sätter via /kiosk/inventory/start.
    active_inventory_session_id: int | None = Field(
        default=None, foreign_key="inventory_sessions.id"
    )
    created_at: datetime = Field(default_factory=now_utc)
    last_activity_at: datetime | None = None
