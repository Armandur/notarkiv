from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_auth, require_editor, verify_csrf
from app.models import (
    ContributorRole,
    Piece,
    PieceImage,
    PiecePlacement,
    StorageLocation,
    StorageUnit,
    UnitKind,
    User,
)
from app.models.piece_image import PieceImageKind
from app.services.people import collect_contributors
from app.templates_setup import flash, render
from app.utils.images import (
    delete_saved_image,
    rotate_saved_image,
    save_uploaded_cover,
    thumbnail_url_path,
)

router = APIRouter(prefix="/pieces", tags=["pieces"])


def _covers_by_piece(session: Session, piece_ids: list[int]) -> dict[int, PieceImage]:
    """Returnera mappning piece_id -> första bilden (sort_order asc) för en lista pieces."""
    if not piece_ids:
        return {}
    rows = session.exec(
        select(PieceImage)
        .where(PieceImage.piece_id.in_(piece_ids))
        .order_by(PieceImage.piece_id, PieceImage.sort_order, PieceImage.id)
    ).all()
    out: dict[int, PieceImage] = {}
    for img in rows:
        if img.piece_id not in out:
            out[img.piece_id] = img
    return out


@router.get("")
async def list_pieces(
    request: Request,
    q: str | None = None,
    view: str = "grid",
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    if q:
        from sqlalchemy import text

        rows = session.exec(
            text(
                "SELECT id FROM pieces_fts JOIN pieces ON pieces.id = pieces_fts.rowid "
                "WHERE pieces_fts MATCH :q ORDER BY rank LIMIT 100"
            ),
            params={"q": q + "*"},
        ).all()
        ids = [r[0] for r in rows]
        pieces = (
            session.exec(select(Piece).where(Piece.id.in_(ids))).all() if ids else []
        )
    else:
        pieces = session.exec(
            select(Piece).order_by(Piece.created_at.desc()).limit(100)
        ).all()

    covers = _covers_by_piece(session, [p.id for p in pieces])

    # Räkna placeringar per piece för list-vyn
    from sqlalchemy import func as sqlf

    placement_counts: dict[int, int] = {}
    if pieces:
        rows = session.exec(
            select(PiecePlacement.piece_id, sqlf.count(PiecePlacement.id))
            .where(PiecePlacement.piece_id.in_([p.id for p in pieces]))
            .group_by(PiecePlacement.piece_id)
        ).all()
        placement_counts = dict(rows)

    def cover_thumb(piece_id: int) -> str | None:
        cover = covers.get(piece_id)
        return thumbnail_url_path(cover.image_path) if cover else None

    return render(
        request,
        "pieces/list.html",
        {
            "pieces": pieces,
            "q": q or "",
            "view": "list" if view == "list" else "grid",
            "cover_thumb": cover_thumb,
            "placement_counts": placement_counts,
        },
        user=user,
    )


@router.get("/{piece_id}")
async def piece_detail(
    request: Request,
    piece_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    images = session.exec(
        select(PieceImage)
        .where(PieceImage.piece_id == piece_id)
        .order_by(PieceImage.sort_order, PieceImage.id)
    ).all()

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece_id)
    ).all()

    placement_views = []
    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units = {u.id: u for u in session.exec(select(StorageUnit)).all()}
    kinds = {k.id: k.name for k in session.exec(select(UnitKind)).all()}

    for p in placements:
        unit = units.get(p.storage_unit_id)
        if not unit:
            continue
        parts = [unit.name]
        current = unit
        while current.parent_id:
            current = units.get(current.parent_id)
            if not current:
                break
            parts.append(current.name)
        loc = locations.get(unit.location_id)
        if loc:
            parts.append(loc.name)
        placement_views.append(
            {
                "placement": p,
                "unit": unit,
                "location": loc,
                "path": " > ".join(reversed(parts)),
                "kind_name": kinds.get(unit.kind_id),
            }
        )

    contributors = collect_contributors(session, piece_id)
    return render(
        request,
        "pieces/detail.html",
        {
            "piece": piece,
            "images": images,
            "placements": placement_views,
            "contributors": contributors,
            "composer_role": ContributorRole.COMPOSER,
            "arranger_role": ContributorRole.ARRANGER,
            "lyricist_role": ContributorRole.LYRICIST,
            "image_kinds": [k.value for k in PieceImageKind],
        },
        user=user,
    )


@router.post("/{piece_id}/images", dependencies=[Depends(verify_csrf)])
async def add_image(
    request: Request,
    piece_id: int,
    image: UploadFile = File(...),
    kind: str = Form("other"),
    label: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    content = await image.read()
    if not content:
        flash(request, "Tom fil", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    try:
        relative_path = save_uploaded_cover(content)
    except Exception:
        flash(request, "Kunde inte läsa bilden", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    last_order = session.exec(
        select(PieceImage.sort_order)
        .where(PieceImage.piece_id == piece_id)
        .order_by(PieceImage.sort_order.desc())
    ).first()
    next_order = (last_order or 0) + 1

    try:
        kind_enum = PieceImageKind(kind)
    except ValueError:
        kind_enum = PieceImageKind.OTHER

    session.add(
        PieceImage(
            piece_id=piece_id,
            image_path=relative_path,
            kind=kind_enum,
            label=(label or "").strip() or None,
            sort_order=next_order,
        )
    )
    session.commit()
    flash(request, "Bild tillagd", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/images/{image_id}/rotate", dependencies=[Depends(verify_csrf)])
async def rotate_image(
    request: Request,
    piece_id: int,
    image_id: int,
    angle: int = Form(90),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    img = session.get(PieceImage, image_id)
    if not img or img.piece_id != piece_id:
        raise HTTPException(404)
    if angle not in {90, 180, 270, -90}:
        raise HTTPException(400, "Endast 90, 180, 270, -90 stöds")

    rotate_saved_image(img.image_path, angle)
    flash(request, "Bilden roterad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/images/{image_id}/promote", dependencies=[Depends(verify_csrf)])
async def promote_image(
    request: Request,
    piece_id: int,
    image_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Gör en bild till primärbild (sort_order = 0, övriga skiftas upp)."""
    target = session.get(PieceImage, image_id)
    if not target or target.piece_id != piece_id:
        raise HTTPException(404)

    others = session.exec(
        select(PieceImage)
        .where(PieceImage.piece_id == piece_id)
        .where(PieceImage.id != image_id)
        .order_by(PieceImage.sort_order, PieceImage.id)
    ).all()
    target.sort_order = 0
    session.add(target)
    for i, o in enumerate(others, start=1):
        o.sort_order = i
        session.add(o)
    session.commit()
    flash(request, "Sätt som primärbild", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/images/{image_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_image(
    request: Request,
    piece_id: int,
    image_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    img = session.get(PieceImage, image_id)
    if not img or img.piece_id != piece_id:
        raise HTTPException(404)

    count = session.exec(
        select(PieceImage).where(PieceImage.piece_id == piece_id)
    ).all()
    if len(count) <= 1:
        flash(request, "Kan inte ta bort sista bilden för en not", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    delete_saved_image(img.image_path)
    session.delete(img)
    session.commit()
    flash(request, "Bilden raderad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)
