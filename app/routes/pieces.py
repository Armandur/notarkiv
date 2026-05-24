from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from app.deps import current_user, get_session, require_auth
from app.models import Piece, PiecePlacement, StorageLocation, StorageUnit, UnitKind, User
from app.templates_setup import render
from app.utils.images import thumbnail_url_path

router = APIRouter(prefix="/pieces", tags=["pieces"])


@router.get("")
async def list_pieces(
    request: Request,
    q: str | None = None,
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
        pieces = session.exec(select(Piece).order_by(Piece.created_at.desc()).limit(100)).all()

    return render(
        request,
        "pieces/list.html",
        {"pieces": pieces, "q": q or "", "thumb": thumbnail_url_path},
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

    return render(
        request,
        "pieces/detail.html",
        {
            "piece": piece,
            "placements": placement_views,
            "cover_url": f"/images/{piece.cover_image_path}" if piece.cover_image_path else None,
        },
        user=user,
    )
