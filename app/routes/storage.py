from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import current_user, get_session, require_admin, require_editor, verify_csrf
from app.models import PiecePlacement, StorageLocation, StorageUnit, User
from app.models.storage import LocationKind
from app.templates_setup import flash, render

router = APIRouter(prefix="/storage", tags=["storage"])


def _load_tree(session: Session) -> list[dict]:
    """Hämta locations med nästlade units. Returnerar en lista dicts redo för template."""
    locations = session.exec(select(StorageLocation).order_by(StorageLocation.sort_order)).all()
    all_units = session.exec(
        select(StorageUnit)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order)
    ).all()

    units_by_parent: dict[tuple[int, int | None], list[StorageUnit]] = {}
    for unit in all_units:
        key = (unit.location_id, unit.parent_id)
        units_by_parent.setdefault(key, []).append(unit)

    def build_children(location_id: int, parent_id: int | None) -> list[dict]:
        children = units_by_parent.get((location_id, parent_id), [])
        return [
            {"unit": c, "children": build_children(location_id, c.id)}
            for c in children
        ]

    return [
        {"location": loc, "units": build_children(loc.id, None)}
        for loc in locations
    ]


@router.get("")
async def storage_index(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    tree = _load_tree(session)
    return render(request, "storage/manage.html", {"tree": tree}, user=user)


@router.post("/locations", dependencies=[Depends(verify_csrf)])
async def create_location(
    request: Request,
    name: str = Form(...),
    kind: LocationKind = Form(...),
    description: str | None = Form(None),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    existing = session.exec(select(StorageLocation).where(StorageLocation.name == name)).first()
    if existing:
        flash(request, f"Lagringsplats '{name}' finns redan", "danger")
        return RedirectResponse("/storage", status.HTTP_302_FOUND)

    location = StorageLocation(name=name, kind=kind, description=description or None)
    session.add(location)
    session.commit()
    flash(request, f"Skapade '{name}'", "success")
    return RedirectResponse("/storage", status.HTTP_302_FOUND)


@router.post("/locations/{location_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_location(
    request: Request,
    location_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    location = session.get(StorageLocation, location_id)
    if not location:
        raise HTTPException(404)

    has_units = session.exec(
        select(StorageUnit).where(StorageUnit.location_id == location_id).limit(1)
    ).first()
    if has_units:
        flash(request, "Lagringsplatsen har enheter, ta bort dem först", "danger")
        return RedirectResponse("/storage", status.HTTP_302_FOUND)

    session.delete(location)
    session.commit()
    flash(request, f"Raderade '{location.name}'", "success")
    return RedirectResponse("/storage", status.HTTP_302_FOUND)


@router.post("/units", dependencies=[Depends(verify_csrf)])
async def create_unit(
    request: Request,
    location_id: int = Form(...),
    name: str = Form(...),
    kind: str | None = Form(None),
    url: str | None = Form(None),
    parent_id: int | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    location = session.get(StorageLocation, location_id)
    if not location:
        raise HTTPException(400, "Ogiltig lagringsplats")
    if parent_id:
        parent = session.get(StorageUnit, parent_id)
        if not parent or parent.location_id != location_id:
            raise HTTPException(400, "Ogiltig förälder")

    unit = StorageUnit(
        location_id=location_id,
        parent_id=parent_id,
        name=name,
        kind=kind or None,
        url=url or None,
        notes=notes or None,
    )
    session.add(unit)
    session.commit()
    flash(request, f"Skapade '{name}'", "success")

    if request.headers.get("HX-Request"):
        tree = _load_tree(session)
        return render(request, "storage/_tree.html", {"tree": tree}, user=user)
    return RedirectResponse("/storage", status.HTTP_302_FOUND)


@router.post("/units/{unit_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_unit(
    request: Request,
    unit_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)

    has_children = session.exec(
        select(StorageUnit).where(StorageUnit.parent_id == unit_id).limit(1)
    ).first()
    has_placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.storage_unit_id == unit_id).limit(1)
    ).first()

    if has_children:
        flash(request, "Enheten har under-enheter, ta bort dem först", "danger")
    elif has_placements:
        unit.archived = True
        session.add(unit)
        session.commit()
        flash(request, f"Arkiverade '{unit.name}' (innehåller noter)", "info")
    else:
        session.delete(unit)
        session.commit()
        flash(request, f"Raderade '{unit.name}'", "success")

    return RedirectResponse("/storage", status.HTTP_302_FOUND)


@router.get("/units/new-form")
async def new_unit_form(
    request: Request,
    location_id: int,
    parent_id: int | None = None,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-fragment: formulär för att skapa ny enhet under given location/parent."""
    location = session.get(StorageLocation, location_id)
    if not location:
        raise HTTPException(404)
    parent = session.get(StorageUnit, parent_id) if parent_id else None
    return render(
        request,
        "storage/_unit_form.html",
        {"location": location, "parent": parent},
        user=user,
    )
