"""Kiosk-inventering: editor kan starta inventeringsläge på en kiosk så
att efterföljande piece-skanningar registreras som "found"-checks mot
den valda InventorySession."""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_kiosk_editor, require_kiosk_session, verify_csrf
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
    user: User = Depends(require_kiosk_editor),
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
    user: User = Depends(require_kiosk_editor),
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


def _status_for_actual(actual: int, expected: int | None) -> CheckStatus:
    """Räkna ut check-status baserat på hittat antal vs förväntat."""
    exp = expected or 0
    if actual <= 0:
        return CheckStatus.MISSING
    if exp == 0:
        # Digital placering eller okänt antal - bara registrera att den
        # hittats (FOUND)
        return CheckStatus.FOUND
    if actual < exp:
        return CheckStatus.PARTIAL
    if actual > exp:
        return CheckStatus.EXTRA
    return CheckStatus.FOUND


def _latest_actual(session: Session, inv_id: int, placement_id: int) -> int:
    """Senaste actual_copies för en placement i denna session - 0 om
    aldrig kvitterad."""
    last = session.exec(
        select(InventoryCheck)
        .where(InventoryCheck.inventory_session_id == inv_id)
        .where(InventoryCheck.placement_id == placement_id)
        .order_by(InventoryCheck.checked_at.desc())
    ).first()
    return last.actual_copies or 0 if last else 0


def check_piece_for_kiosk(
    session: Session,
    kiosk: Kiosk,
    piece_id: int,
    checked_by: int | None,
    delta: int = 1,
) -> dict:
    """Kvittera piece i aktivt inventeringsläge. Räknar upp varje
    placering inom kioskens plats med `delta` (default +1 per skanning).

    Returnerar { "checks": [{placement_id, actual, expected, status,
    path}], "outside_location": int, "no_placement": bool }."""
    if not kiosk.active_inventory_session_id:
        return {"checks": [], "outside_location": 0, "no_placement": True}
    inv = session.get(InventorySession, kiosk.active_inventory_session_id)
    if not inv or inv.ended_at:
        return {"checks": [], "outside_location": 0, "no_placement": True}

    from app.routes.pieces import _kiosk_location_unit_ids
    from app.services.storage import unit_path as _path

    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece_id)
    ).all()
    if not placements:
        return {"checks": [], "outside_location": 0, "no_placement": True}

    from app.models import StorageUnit

    checks_info = []
    outside = 0
    for pl in placements:
        if allowed_unit_ids is not None and pl.storage_unit_id not in allowed_unit_ids:
            outside += 1
            continue
        previous = _latest_actual(session, inv.id, pl.id)
        new_actual = max(0, previous + delta)
        status = _status_for_actual(new_actual, pl.copies)
        session.add(InventoryCheck(
            inventory_session_id=inv.id,
            placement_id=pl.id,
            status=status,
            actual_copies=new_actual,
            checked_by=checked_by,
        ))
        unit = session.get(StorageUnit, pl.storage_unit_id)
        checks_info.append({
            "placement_id": pl.id,
            "actual": new_actual,
            "expected": pl.copies,
            "status": status.value,
            "path": _path(session, unit) if unit else "Okänd plats",
        })
    if checks_info:
        delta_label = f"+{delta}" if delta >= 0 else str(delta)
        append_log(
            inv,
            f"Piece #{piece_id} kvitterad via kiosk ({delta_label} på {len(checks_info)} placering(ar))",
            f"user:{checked_by}" if checked_by else "kiosk",
        )
        session.add(inv)
    session.commit()
    return {"checks": checks_info, "outside_location": outside, "no_placement": False}


@router.post("/adjust/{piece_public_id}", dependencies=[Depends(verify_csrf)])
async def adjust_piece(
    request: Request,
    piece_public_id: str,
    delta: int = Form(0),
    kiosk: Kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """Manuell +/- på piecens kvittering. Används från knapparna i
    kiosk_piece-vyn för att rätta efter automatiska +1."""
    from app.models import Piece

    piece = session.exec(select(Piece).where(Piece.public_id == piece_public_id)).first()
    if not piece:
        raise HTTPException(404)
    # Använd kioskens nuvarande borrower som "checked_by"
    borrower_id = request.session.get("kiosk_borrower_id")
    check_piece_for_kiosk(session, kiosk, piece.id, borrower_id, delta=delta)
    return RedirectResponse(f"/kiosk/{piece_public_id}", status.HTTP_302_FOUND)


def get_inventory_progress(session: Session, kiosk: Kiosk) -> dict | None:
    """Statistik för det aktiva inventeringsläget på kiosken. Returnerar
    None om inget läge är aktivt."""
    if not kiosk.active_inventory_session_id:
        return None
    inv = session.get(InventorySession, kiosk.active_inventory_session_id)
    if not inv:
        return None

    from app.models import StorageLocation
    from app.routes.pieces import _kiosk_location_unit_ids

    location = (
        session.get(StorageLocation, kiosk.location_id)
        if kiosk.location_id
        else None
    )
    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)

    # Alla placeringar på kioskens plats
    if allowed_unit_ids is None:
        total_placements = list(session.exec(select(PiecePlacement)).all())
    else:
        total_placements = [
            pl for pl in session.exec(select(PiecePlacement)).all()
            if pl.storage_unit_id in allowed_unit_ids
        ]
    total_placements_count = len(total_placements)
    total_pieces = len({pl.piece_id for pl in total_placements})
    total_expected_copies = sum((pl.copies or 0) for pl in total_placements)

    # Räkna ALLA check-status (inte bara FOUND) - vi vill kunna visa
    # delvis/saknas/extra också. Senaste check per placement gäller.
    checked_rows = session.exec(
        select(InventoryCheck)
        .where(InventoryCheck.inventory_session_id == inv.id)
        .order_by(InventoryCheck.checked_at.desc())
    ).all()
    latest_per_placement: dict[int, InventoryCheck] = {}
    for c in checked_rows:
        if c.placement_id not in latest_per_placement:
            latest_per_placement[c.placement_id] = c

    allowed_placement_ids = {pl.id for pl in total_placements}
    placements_checked = 0
    pieces_checked: set[int] = set()
    actual_copies_total = 0
    counts = {"found": 0, "partial": 0, "missing": 0, "extra": 0}
    for pl in total_placements:
        c = latest_per_placement.get(pl.id)
        if c and c.status != CheckStatus.NOT_CHECKED:
            placements_checked += 1
            pieces_checked.add(pl.piece_id)
            actual_copies_total += c.actual_copies or 0
            counts[c.status.value] = counts.get(c.status.value, 0) + 1

    return {
        "session": inv,
        "location": location,
        "total_pieces": total_pieces,
        "pieces_checked": len(pieces_checked),
        "total_placements": total_placements_count,
        "placements_checked": placements_checked,
        "total_expected_copies": total_expected_copies,
        "actual_copies_total": actual_copies_total,
        "counts": counts,
    }
