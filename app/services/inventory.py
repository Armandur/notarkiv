"""Hjälpare för aktiva inventeringstillfällen."""

from datetime import datetime

from sqlmodel import Session, select

from app.models import InventorySession


def get_active_session(session: Session) -> InventorySession | None:
    """Returnera nuvarande aktiva inventeringstillfälle, eller None."""
    return session.exec(
        select(InventorySession)
        .where(InventorySession.ended_at.is_(None))
        .order_by(InventorySession.started_at.desc())
    ).first()


def append_log(inv: InventorySession, text: str, user_label: str | None = None) -> None:
    """Lägg till en tidsstämplad rad till loggen. Mutera direkt på instansen."""
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    prefix = f"[{stamp}]"
    if user_label:
        prefix += f" {user_label}:"
    line = f"{prefix} {text}".rstrip()
    inv.log = f"{inv.log}\n{line}" if inv.log else line
