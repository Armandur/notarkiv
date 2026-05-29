"""Kiosk-inventering: editor kan starta inventeringsläge på en kiosk så
att efterföljande piece-skanningar registreras som "found"-checks mot
den valda InventorySession."""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_editor, require_kiosk_session, verify_csrf
from app.models import (
    InventoryCheck,
    InventorySession,
    Kiosk,
    PiecePlacement,
    User,
)
from app.models.inventory_check import CheckStatus
from app.services.inventory import append_log
from app.templates_setup import flash

router = APIRouter(prefix="/kiosk/inventory", tags=["kiosk"])


@router.post("/start", dependencies=[Depends(verify_csrf)])
async def start_inventory(
    request: Request,
    inventory_session_id: int = Form(...),
    kiosk: Kiosk = Depends(require_kiosk_session),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Aktivera en pågående InventorySession i kiosken. Efterföljande
    skanningar registreras som checks."""
    inv = session.get(InventorySession, inventory_session_id)
    if not inv or inv.ended_at:
        flash(request, "Sessionen finns inte eller är redan avslutad", "danger")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)
    kiosk.active_inventory_session_id = inv.id
    kiosk.last_activity_at = datetime.utcnow()
    session.add(kiosk)
    append_log(inv, f"Inventeringsläge aktivt på kiosk {kiosk.name}", user.username)
    session.add(inv)
    session.commit()
    flash(request, f'Inventeringsläge: "{inv.name}"', "success")
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


@router.post("/stop", dependencies=[Depends(verify_csrf)])
async def stop_inventory(
    request: Request,
    kiosk: Kiosk = Depends(require_kiosk_session),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Stäng av inventeringsläget. Pågående session påverkas inte - bara
    kioskens koppling till den."""
    if kiosk.active_inventory_session_id:
        inv = session.get(InventorySession, kiosk.active_inventory_session_id)
        if inv:
            append_log(
                inv,
                f"Inventeringsläge avslutat på kiosk {kiosk.name}",
                user.username,
            )
            session.add(inv)
    kiosk.active_inventory_session_id = None
    session.add(kiosk)
    session.commit()
    flash(request, "Inventeringsläge avslutat", "info")
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


def check_piece_for_kiosk(
    session: Session,
    kiosk: Kiosk,
    piece_id: int,
    checked_by: int | None,
) -> dict:
    """Kvittera alla placeringar för en piece som ligger inom kioskens
    lagringsplats som FOUND. Returnerar en dict med statistik.

    Returnerar { "checked": int, "outside_location": int,
    "no_placement": bool }. checked = placeringar som markerats found,
    outside_location = placeringar som ligger utanför kioskens plats."""
    if not kiosk.active_inventory_session_id:
        return {"checked": 0, "outside_location": 0, "no_placement": True}
    inv = session.get(InventorySession, kiosk.active_inventory_session_id)
    if not inv or inv.ended_at:
        return {"checked": 0, "outside_location": 0, "no_placement": True}

    # Kioskens lagringsplats (None = alla)
    from app.routes.pieces import _kiosk_location_unit_ids

    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece_id)
    ).all()
    if not placements:
        return {"checked": 0, "outside_location": 0, "no_placement": True}

    checked = 0
    outside = 0
    for pl in placements:
        if allowed_unit_ids is not None and pl.storage_unit_id not in allowed_unit_ids:
            outside += 1
            continue
        check = InventoryCheck(
            inventory_session_id=inv.id,
            placement_id=pl.id,
            status=CheckStatus.FOUND,
            actual_copies=pl.copies,
            checked_by=checked_by,
        )
        session.add(check)
        checked += 1
    if checked:
        append_log(
            inv,
            f"Piece #{piece_id} kvitterad via kiosk ({checked} placering(ar))",
            f"user:{checked_by}" if checked_by else "kiosk",
        )
        session.add(inv)
    session.commit()
    return {"checked": checked, "outside_location": outside, "no_placement": False}


def get_inventory_progress(session: Session, kiosk: Kiosk) -> dict | None:
    """Statistik för det aktiva inventeringsläget på kiosken. Returnerar
    None om inget läge är aktivt."""
    if not kiosk.active_inventory_session_id:
        return None
    inv = session.get(InventorySession, kiosk.active_inventory_session_id)
    if not inv:
        return None
    # Räkna unika piece_ids som har en FOUND-check i sessionen
    from app.routes.pieces import _kiosk_location_unit_ids

    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)

    # Totalt: alla placeringar på kioskens plats
    if allowed_unit_ids is None:
        total_placements = session.exec(select(PiecePlacement)).all()
    else:
        total_placements = [
            pl for pl in session.exec(select(PiecePlacement)).all()
            if pl.storage_unit_id in allowed_unit_ids
        ]
    total = len(total_placements)

    checked_rows = session.exec(
        select(InventoryCheck)
        .where(InventoryCheck.inventory_session_id == inv.id)
        .where(InventoryCheck.status == CheckStatus.FOUND)
    ).all()
    # Senaste check per placement_id (om upprepade)
    checked_placement_ids = {c.placement_id for c in checked_rows}
    # Räkna bara de som tillhör kioskens plats
    allowed_placement_ids = {pl.id for pl in total_placements}
    checked = len(checked_placement_ids & allowed_placement_ids)

    return {
        "session": inv,
        "total": total,
        "checked": checked,
        "remaining": max(0, total - checked),
    }
