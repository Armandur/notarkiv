from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_editor, verify_csrf
from app.models import (
    InventoryCheck,
    InventorySession,
    Piece,
    PieceImage,
    PiecePlacement,
    ScanSession,
    StorageLocation,
    StorageUnit,
    User,
)
from app.models.inventory_check import CheckStatus
from app.services.inventory import (
    append_log,
    get_active_sessions,
    get_user_default_active_session,
)
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
    actives = get_active_sessions(session)
    own_active = get_user_default_active_session(session, user.id)
    locations = session.exec(select(StorageLocation)).all()
    units = session.exec(
        select(StorageUnit).where(StorageUnit.archived == False)  # noqa: E712
    ).all()
    # Username-lookup för att visa "startad av" på varje aktiv
    starter_ids = {s.started_by for s in actives if s.started_by}
    starters = {}
    if starter_ids:
        starters = {
            u.id: u.username for u in session.exec(
                select(User).where(User.id.in_(list(starter_ids)))
            ).all()
        }
    return render(
        request,
        "inventory/list.html",
        {
            "sessions": sessions,
            "actives": actives,
            "own_active": own_active,
            "starters": starters,
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
    # Flera kan vara aktiva samtidigt (knutna till olika users). Avsluta
    # bara ev. tidigare av denna user för att inte spräcka "en per user"-regeln.
    own_active = get_user_default_active_session(session, user.id)
    if own_active:
        own_active.ended_at = datetime.utcnow()
        append_log(own_active, "Avslutad automatiskt - ny session startad", user.username)
        session.add(own_active)

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


@router.get("/{inv_id}/check")
async def check_pick_unit(
    request: Request,
    inv_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Välj en enhet att inventera (visar bara enheter som har placeringar)."""
    inv = session.get(InventorySession, inv_id)
    if not inv:
        raise HTTPException(404)

    used_unit_ids = set(
        session.exec(select(PiecePlacement.storage_unit_id).distinct()).all()
    )
    units = session.exec(
        select(StorageUnit).where(StorageUnit.id.in_(used_unit_ids))
    ).all() if used_unit_ids else []
    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units_by_id = {u.id: u for u in session.exec(select(StorageUnit)).all()}

    options = []
    for u in units:
        # Bygg sökväg
        parts = [u.name]
        cur = u
        while cur.parent_id:
            cur = units_by_id.get(cur.parent_id)
            if not cur:
                break
            parts.append(cur.name)
        loc = locations.get(u.location_id)
        if loc:
            parts.append(loc.name)
        # Räkna placeringar
        count = session.exec(
            select(PiecePlacement).where(PiecePlacement.storage_unit_id == u.id)
        ).all()
        options.append(
            {"id": u.id, "label": " > ".join(reversed(parts)), "count": len(count)}
        )
    options.sort(key=lambda o: o["label"])

    return render(
        request, "inventory/check_pick.html", {"inv": inv, "options": options}, user=user
    )


@router.get("/{inv_id}/check/{unit_id}")
async def check_unit(
    request: Request,
    inv_id: int,
    unit_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InventorySession, inv_id)
    unit = session.get(StorageUnit, unit_id)
    if not inv or not unit:
        raise HTTPException(404)

    placements = session.exec(
        select(PiecePlacement)
        .where(PiecePlacement.storage_unit_id == unit_id)
        .order_by(PiecePlacement.id)
    ).all()

    piece_ids = [p.piece_id for p in placements]
    pieces = {
        p.id: p
        for p in session.exec(select(Piece).where(Piece.id.in_(piece_ids))).all()
    } if piece_ids else {}

    # Senaste check per placering (i denna inv-session)
    checks_rows = session.exec(
        select(InventoryCheck)
        .where(InventoryCheck.inventory_session_id == inv_id)
        .where(InventoryCheck.placement_id.in_([p.id for p in placements]))
        .order_by(InventoryCheck.checked_at)
    ).all() if placements else []
    latest_check: dict[int, InventoryCheck] = {}
    for c in checks_rows:
        latest_check[c.placement_id] = c  # senare skriver över

    # Cover för thumbnail (första PieceImage per piece)
    covers: dict[int, str] = {}
    if piece_ids:
        for img in session.exec(
            select(PieceImage)
            .where(PieceImage.piece_id.in_(piece_ids))
            .order_by(PieceImage.piece_id, PieceImage.sort_order)
        ).all():
            covers.setdefault(img.piece_id, img.image_path)

    items = []
    for pl in placements:
        piece = pieces.get(pl.piece_id)
        if not piece:
            continue
        check = latest_check.get(pl.id)
        items.append(
            {
                "placement": pl,
                "piece": piece,
                "check": check,
                "thumb": covers.get(pl.piece_id),
            }
        )

    # Sammanfattning
    summary = {
        "total": len(items),
        "found": sum(1 for i in items if i["check"] and i["check"].status == "found"),
        "partial": sum(1 for i in items if i["check"] and i["check"].status == "partial"),
        "missing": sum(1 for i in items if i["check"] and i["check"].status == "missing"),
        "extra": sum(1 for i in items if i["check"] and i["check"].status == "extra"),
    }
    summary["not_checked"] = summary["total"] - sum(
        [summary["found"], summary["partial"], summary["missing"], summary["extra"]]
    )

    return render(
        request,
        "inventory/check.html",
        {"inv": inv, "unit": unit, "items": items, "summary": summary},
        user=user,
    )


@router.post(
    "/{inv_id}/check/{unit_id}/items/{placement_id}",
    dependencies=[Depends(verify_csrf)],
)
async def check_item(
    request: Request,
    inv_id: int,
    unit_id: int,
    placement_id: int,
    status_val: str = Form(..., alias="status"),
    actual_copies: str | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    inv = session.get(InventorySession, inv_id)
    placement = session.get(PiecePlacement, placement_id)
    if not inv or not placement or placement.storage_unit_id != unit_id:
        raise HTTPException(404)
    try:
        status_enum = CheckStatus(status_val)
    except ValueError:
        raise HTTPException(400, "Ogiltig status")

    actual = (
        int(actual_copies)
        if actual_copies and actual_copies.isdigit()
        else None
    )

    check = InventoryCheck(
        inventory_session_id=inv_id,
        placement_id=placement_id,
        status=status_enum,
        actual_copies=actual,
        notes=(notes or "").strip() or None,
        checked_by=user.id,
    )
    session.add(check)

    # Logga i sessionens logg
    log_label = {
        CheckStatus.FOUND: "hittad",
        CheckStatus.PARTIAL: f"avvikande antal ({actual} ex)",
        CheckStatus.MISSING: "SAKNAS",
        CheckStatus.EXTRA: f"extra ({actual} ex)",
        CheckStatus.NOT_CHECKED: "återställd",
    }.get(status_enum, status_enum.value)
    piece = session.get(Piece, placement.piece_id)
    if piece:
        append_log(inv, f"check: '{piece.title}' -> {log_label}", user.username)
        session.add(inv)
    session.commit()

    # HTMX: returnera bara raden uppdaterad
    if request.headers.get("HX-Request"):
        thumb_row = session.exec(
            select(PieceImage)
            .where(PieceImage.piece_id == placement.piece_id)
            .order_by(PieceImage.sort_order)
        ).first()
        return render(
            request,
            "inventory/_check_row.html",
            {
                "item": {
                    "placement": placement,
                    "piece": piece,
                    "check": check,
                    "thumb": thumb_row.image_path if thumb_row else None,
                },
                "inv": inv,
                "unit_id": unit_id,
            },
            user=user,
        )

    return RedirectResponse(f"/inventory/{inv_id}/check/{unit_id}", status.HTTP_302_FOUND)


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
