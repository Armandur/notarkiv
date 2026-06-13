"""Hjälpare för aktiva inventeringstillfällen."""

from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Session, select

from app.models import InventorySession


def get_active_session(session: Session) -> InventorySession | None:
    """Senast startade aktiva inventering (oavsett user). Behållen för
    bakåtkompabilitet med kod som inte tar hänsyn till user-koppling."""
    return session.exec(
        select(InventorySession)
        .where(InventorySession.ended_at.is_(None))
        .order_by(InventorySession.started_at.desc())
    ).first()


def get_active_sessions(session: Session) -> list[InventorySession]:
    """Alla aktiva inventeringar (icke-avslutade). Flera kan vara igång
    samtidigt - en per user är typisk användning."""
    return list(
        session.exec(
            select(InventorySession)
            .where(InventorySession.ended_at.is_(None))
            .order_by(InventorySession.started_at.desc())
        ).all()
    )


def get_user_active_sessions(
    session: Session, user_id: int
) -> list[InventorySession]:
    """Användarens egna aktiva inventeringar."""
    return list(
        session.exec(
            select(InventorySession)
            .where(InventorySession.ended_at.is_(None))
            .where(InventorySession.started_by == user_id)
            .order_by(InventorySession.started_at.desc())
        ).all()
    )


def get_user_default_active_session(
    session: Session, user_id: int
) -> InventorySession | None:
    """Användarens senaste aktiva - default för scan-koppling när inget
    annat val gjorts."""
    sessions = get_user_active_sessions(session, user_id)
    return sessions[0] if sessions else None


def append_log(inv: InventorySession, text: str, user_label: str | None = None) -> None:
    """Lägg till en tidsstämplad rad till loggen. Mutera direkt på instansen."""
    stamp = now_utc().strftime("%Y-%m-%d %H:%M")
    prefix = f"[{stamp}]"
    if user_label:
        prefix += f" {user_label}:"
    line = f"{prefix} {text}".rstrip()
    inv.log = f"{inv.log}\n{line}" if inv.log else line
