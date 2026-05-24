from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_editor, verify_csrf
from app.models import InventorySession, ScanSession, StorageLocation, StorageUnit, User
from app.services.inventory import append_log, get_active_session
from app.templates_setup import flash, render

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
async def list_sessions(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    sessions = session.exec(
        select(InventorySession).order_by(InventorySession.started_at.desc())
    ).all()
    active = get_active_session(session)
    locations = session.exec(select(StorageLocation)).all()
    units = session.exec(
        select(StorageUnit).where(StorageUnit.archived == False)  # noqa: E712
    ).all()
    return render(
        request,
        "inventory/list.html",
        {
            "sessions": sessions,
            "active": active,
            "locations": locations,
            "units": units,
        },
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_session(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    planned_location_id: str | None = Form(None),
    planned_unit_id: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    # Avsluta ev. tidigare aktiv session - bara en åt gången
    active = get_active_session(session)
    if active:
        active.ended_at = datetime.utcnow()
        append_log(active, "Avslutad automatiskt - ny session startad", user.username)
        session.add(active)

    inv = InventorySession(
        name=name.strip(),
        description=(description or "").strip() or None,
        planned_location_id=int(planned_location_id)
        if planned_location_id and planned_location_id.isdigit()
        else None,
        planned_unit_id=int(planned_unit_id)
        if planned_unit_id and planned_unit_id.isdigit()
        else None,
        started_by=user.id,
    )
    append_log(inv, "Startad", user.username)
    session.add(inv)
    session.commit()
    session.refresh(inv)
    flash(request, f"Startade '{inv.name}'", "success")
    return RedirectResponse(f"/inventory/{inv.id}", status.HTTP_302_FOUND)


@router.get("/{inv_id}")
async def session_detail(
    request: Request,
    inv_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InventorySession, inv_id)
    if not inv:
        raise HTTPException(404)

    scans = session.exec(
        select(ScanSession)
        .where(ScanSession.inventory_session_id == inv_id)
        .order_by(ScanSession.created_at.desc())
    ).all()
    location = (
        session.get(StorageLocation, inv.planned_location_id)
        if inv.planned_location_id
        else None
    )
    unit = session.get(StorageUnit, inv.planned_unit_id) if inv.planned_unit_id else None
    return render(
        request,
        "inventory/detail.html",
        {
            "inv": inv,
            "scans": scans,
            "location": location,
            "unit": unit,
            "active": inv.ended_at is None,
            "saved_count": sum(1 for s in scans if s.resulting_piece_id),
        },
        user=user,
    )


@router.post("/{inv_id}/log", dependencies=[Depends(verify_csrf)])
async def add_log(
    request: Request,
    inv_id: int,
    text: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InventorySession, inv_id)
    if not inv:
        raise HTTPException(404)
    append_log(inv, text.strip(), user.username)
    session.add(inv)
    session.commit()
    return RedirectResponse(f"/inventory/{inv_id}", status.HTTP_302_FOUND)


@router.post("/{inv_id}/end", dependencies=[Depends(verify_csrf)])
async def end_session(
    request: Request,
    inv_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InventorySession, inv_id)
    if not inv:
        raise HTTPException(404)
    if inv.ended_at:
        flash(request, "Sessionen är redan avslutad", "info")
        return RedirectResponse(f"/inventory/{inv_id}", status.HTTP_302_FOUND)

    inv.ended_at = datetime.utcnow()
    append_log(inv, "Avslutad", user.username)
    session.add(inv)
    session.commit()
    flash(request, f"Avslutade '{inv.name}'", "success")
    return RedirectResponse(f"/inventory/{inv_id}", status.HTTP_302_FOUND)
