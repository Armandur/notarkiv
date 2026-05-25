import io
import json

import qrcode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import (
    Piece,
    PiecePlacement,
    StorageLocation,
    StorageUnit,
    StorageUnitImage,
    UnitKind,
    User,
)
from app.models.storage import LocationKind
from app.routes.pieces import _placement_summaries
from app.templates_setup import flash, render

router = APIRouter(prefix="/storage", tags=["storage"])


def _load_tree(session: Session) -> list[dict]:
    """Hämta locations med nästlade units och deras kinds som dict-träd."""
    locations = session.exec(select(StorageLocation).order_by(StorageLocation.sort_order)).all()
    all_units = session.exec(
        select(StorageUnit)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order)
    ).all()
    kinds_by_id = {k.id: k for k in session.exec(select(UnitKind)).all()}

    units_by_parent: dict[tuple[int, int | None], list[StorageUnit]] = {}
    for unit in all_units:
        units_by_parent.setdefault((unit.location_id, unit.parent_id), []).append(unit)

    def build_children(location_id: int, parent_id: int | None) -> list[dict]:
        children = units_by_parent.get((location_id, parent_id), [])
        return [
            {
                "unit": c,
                "kind": kinds_by_id.get(c.kind_id),
                "children": build_children(location_id, c.id),
            }
            for c in children
        ]

    return [
        {"location": loc, "units": build_children(loc.id, None)} for loc in locations
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


@router.get("/locations/{location_id}/edit-form")
async def edit_location_form(
    request: Request,
    location_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    location = session.get(StorageLocation, location_id)
    if not location:
        raise HTTPException(404)
    return render(
        request,
        "storage/_location_form.html",
        {"location": location},
        user=user,
    )


@router.post("/locations/{location_id}/update", dependencies=[Depends(verify_csrf)])
async def update_location(
    request: Request,
    location_id: int,
    name: str = Form(...),
    kind: LocationKind = Form(...),
    description: str | None = Form(None),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    location = session.get(StorageLocation, location_id)
    if not location:
        raise HTTPException(404)

    new_name = name.strip()
    if new_name != location.name:
        clash = session.exec(
            select(StorageLocation)
            .where(StorageLocation.name == new_name)
            .where(StorageLocation.id != location_id)
        ).first()
        if clash:
            flash(request, f"En annan lagringsplats heter redan '{new_name}'", "danger")
            return RedirectResponse("/storage", status.HTTP_302_FOUND)

    location.name = new_name
    location.kind = kind
    location.description = (description or "").strip() or None
    session.add(location)
    session.commit()
    flash(request, f"Uppdaterade '{location.name}'", "success")
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
    kind_id: int | None = Form(None),
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
    if kind_id is not None:
        kind = session.get(UnitKind, kind_id)
        if not kind:
            raise HTTPException(400, "Ogiltig typ")

    unit = StorageUnit(
        location_id=location_id,
        parent_id=parent_id,
        name=name,
        kind_id=kind_id,
        notes=notes or None,
    )
    session.add(unit)
    session.commit()
    flash(request, f"Skapade '{name}'", "success")
    return RedirectResponse("/storage", status.HTTP_302_FOUND)


@router.get("/units/{unit_id}/edit-form")
async def edit_unit_form(
    request: Request,
    unit_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)
    location = session.get(StorageLocation, unit.location_id)
    kind = session.get(UnitKind, unit.kind_id) if unit.kind_id else None
    # Möjliga parents: alla units i samma location utom denna och dess
    # ättlingar (skulle skapa cykel)
    all_in_location = session.exec(
        select(StorageUnit).where(StorageUnit.location_id == unit.location_id)
    ).all()
    descendants: set[int] = {unit.id}
    changed = True
    while changed:
        changed = False
        for u in all_in_location:
            if u.parent_id in descendants and u.id not in descendants:
                descendants.add(u.id)
                changed = True
    valid_parents = [
        u for u in all_in_location if u.id not in descendants and not u.archived
    ]
    valid_parents.sort(key=lambda u: u.name)
    return render(
        request,
        "storage/_unit_edit_form.html",
        {
            "unit": unit,
            "location": location,
            "kind": kind,
            "valid_parents": valid_parents,
        },
        user=user,
    )


@router.post("/units/{unit_id}/update", dependencies=[Depends(verify_csrf)])
async def update_unit(
    request: Request,
    unit_id: int,
    name: str = Form(...),
    parent_id: int | None = Form(None),
    kind_id: int | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)

    if parent_id == unit_id:
        raise HTTPException(400, "En enhet kan inte vara sin egen förälder")
    if parent_id:
        parent = session.get(StorageUnit, parent_id)
        if not parent or parent.location_id != unit.location_id:
            raise HTTPException(400, "Ogiltig förälder")
        # Cykelkoll: gå uppåt och kolla att vi själva inte är ancestor
        cur = parent
        seen = set()
        while cur and cur.id not in seen:
            seen.add(cur.id)
            if cur.id == unit_id:
                raise HTTPException(400, "Skulle skapa cykel i hierarkin")
            cur = session.get(StorageUnit, cur.parent_id) if cur.parent_id else None

    if kind_id is not None:
        kind_obj = session.get(UnitKind, kind_id)
        if not kind_obj:
            raise HTTPException(400, "Ogiltig typ")

    unit.name = name.strip()
    unit.parent_id = parent_id
    unit.kind_id = kind_id
    unit.notes = (notes or "").strip() or None
    session.add(unit)
    session.commit()
    flash(request, f"Uppdaterade '{unit.name}'", "success")
    return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)


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
        from app.utils.images import delete_saved_image

        image_paths = [
            i.image_path
            for i in session.exec(
                select(StorageUnitImage).where(
                    StorageUnitImage.storage_unit_id == unit_id
                )
            ).all()
        ]
        session.delete(unit)
        session.commit()
        for p in image_paths:
            delete_saved_image(p)
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


@router.get("/unit-kinds/search")
async def search_unit_kinds(
    request: Request,
    q: str = "",
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    query = q.strip()
    results: list[UnitKind] = []
    exact_match = False

    if query:
        stmt = (
            select(UnitKind)
            .where(UnitKind.name.ilike(f"%{query}%"))
            .order_by(UnitKind.name)
            .limit(10)
        )
        results = list(session.exec(stmt).all())
        exact_match = any(k.name.lower() == query.lower() for k in results)
    else:
        results = list(session.exec(select(UnitKind).order_by(UnitKind.name).limit(10)).all())

    can_create = bool(query) and not exact_match
    return render(
        request,
        "storage/_kind_results.html",
        {"results": results, "query": query, "can_create": can_create},
        user=user,
    )


@router.post("/unit-kinds", dependencies=[Depends(verify_csrf)])
async def create_unit_kind(
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    name = name.strip()
    if not name:
        raise HTTPException(400, "Namnet får inte vara tomt")

    existing = session.exec(select(UnitKind).where(UnitKind.name == name)).first()
    if existing:
        # Returnera den befintliga - inte ett fel, för UX:t blir samma
        kind = existing
    else:
        kind = UnitKind(name=name)
        session.add(kind)
        session.commit()
        session.refresh(kind)

    response = Response(status_code=204)
    response.headers["HX-Trigger"] = json.dumps(
        {"kindSelected": {"id": kind.id, "name": kind.name}}
    )
    return response


def _unit_full_path(session: Session, unit: StorageUnit) -> str:
    parts = [unit.name]
    cur = unit
    while cur.parent_id:
        parent = session.get(StorageUnit, cur.parent_id)
        if not parent:
            break
        parts.append(parent.name)
        cur = parent
    loc = session.get(StorageLocation, unit.location_id)
    if loc:
        parts.append(loc.name)
    return " › ".join(reversed(parts))


@router.get("/units/{unit_id}")
async def unit_detail(
    request: Request,
    unit_id: int,
    view: str = "list",
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Detaljvy för en enhet. Använder samma kort/lista-rendering som /pieces."""
    from app.routes.pieces import _covers_by_piece
    from app.utils.images import thumbnail_url_path

    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)

    location = session.get(StorageLocation, unit.location_id)
    kind = session.get(UnitKind, unit.kind_id) if unit.kind_id else None
    path = _unit_full_path(session, unit)

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.storage_unit_id == unit_id)
    ).all()
    pieces = []
    if placements:
        pieces = list(
            session.exec(
                select(Piece)
                .where(Piece.id.in_([pl.piece_id for pl in placements]))
                .order_by(Piece.title)
            ).all()
        )

    covers = _covers_by_piece(session, [p.id for p in pieces])

    def cover_thumb(piece_id: int):
        cover = covers.get(piece_id)
        return thumbnail_url_path(cover.image_path) if cover else None

    placement_summary = _placement_summaries(session, [p.id for p in pieces])

    children = session.exec(
        select(StorageUnit)
        .where(StorageUnit.parent_id == unit_id)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order, StorageUnit.name)
    ).all()

    images = session.exec(
        select(StorageUnitImage)
        .where(StorageUnitImage.storage_unit_id == unit_id)
        .order_by(StorageUnitImage.sort_order, StorageUnitImage.id)
    ).all()

    return render(
        request,
        "storage/unit_detail.html",
        {
            "unit": unit,
            "location": location,
            "kind": kind,
            "path": path,
            "pieces": pieces,
            "view": "grid" if view == "grid" else "list",
            "cover_thumb": cover_thumb,
            "placement_summary": placement_summary,
            "children": children,
            "images": images,
        },
        user=user,
    )


@router.post("/units/{unit_id}/images", dependencies=[Depends(verify_csrf)])
async def add_unit_image(
    request: Request,
    unit_id: int,
    image: UploadFile = File(...),
    label: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    from app.utils.images import save_uploaded_cover

    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)

    content = await image.read()
    if not content:
        flash(request, "Tom fil", "danger")
        return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)

    try:
        relative_path = save_uploaded_cover(content)
    except Exception:
        flash(request, "Kunde inte läsa bilden", "danger")
        return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)

    last_order = session.exec(
        select(StorageUnitImage.sort_order)
        .where(StorageUnitImage.storage_unit_id == unit_id)
        .order_by(StorageUnitImage.sort_order.desc())
    ).first()
    next_order = (last_order or 0) + 1

    session.add(
        StorageUnitImage(
            storage_unit_id=unit_id,
            image_path=relative_path,
            label=(label or "").strip() or None,
            sort_order=next_order,
        )
    )
    session.commit()
    flash(request, "Bild tillagd", "success")
    return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)


@router.post("/units/{unit_id}/images/{image_id}/rotate", dependencies=[Depends(verify_csrf)])
async def rotate_unit_image(
    request: Request,
    unit_id: int,
    image_id: int,
    angle: int = Form(90),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    from app.utils.images import rotate_saved_image

    img = session.get(StorageUnitImage, image_id)
    if not img or img.storage_unit_id != unit_id:
        raise HTTPException(404)
    if angle not in {90, 180, 270, -90}:
        raise HTTPException(400, "Endast 90, 180, 270, -90 stöds")

    rotate_saved_image(img.image_path, angle)
    flash(request, "Bilden roterad", "success")
    return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)


@router.post("/units/{unit_id}/images/{image_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_unit_image(
    request: Request,
    unit_id: int,
    image_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    from app.utils.images import delete_saved_image

    img = session.get(StorageUnitImage, image_id)
    if not img or img.storage_unit_id != unit_id:
        raise HTTPException(404)

    path = img.image_path
    session.delete(img)
    session.commit()
    delete_saved_image(path)
    flash(request, "Bild borttagen", "success")
    return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)


@router.get("/units/{unit_id}/qr.png")
async def unit_qr(
    request: Request,
    unit_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)

    base = str(request.base_url).rstrip("/")
    url = f"{base}/storage/units/{unit_id}"

    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/qr-labels")
async def qr_labels(
    request: Request,
    location_id: int | None = None,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Utskriftsvänlig sida med QR-etiketter för enheter."""
    stmt = select(StorageUnit).where(StorageUnit.archived == False)  # noqa: E712
    if location_id:
        stmt = stmt.where(StorageUnit.location_id == location_id)
    units = session.exec(stmt.order_by(StorageUnit.location_id, StorageUnit.name)).all()

    units_with_path = [
        {
            "unit": u,
            "path": _unit_full_path(session, u),
        }
        for u in units
    ]
    locations = session.exec(select(StorageLocation).order_by(StorageLocation.name)).all()

    return render(
        request,
        "storage/qr_labels.html",
        {
            "units": units_with_path,
            "locations": locations,
            "selected_location_id": location_id,
        },
        user=user,
    )
