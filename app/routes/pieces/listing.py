from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from app.utils.dates import now_utc

from app.deps import (
    get_session,
    require_auth,
    require_editor,
)
from app.models import (
    Piece,
    PiecePlacement,
    PieceTag,
    StorageLocation,
    StorageUnit,
    Tag,
    User,
)
from app import templates_setup
from app.templates_setup import render
from app.utils.images import (
    thumbnail_url_path,
)  # noqa: F401 - save_uploaded_cover används också för MB-portrait-import

from app.routes.pieces._routers import router
from app.routes.pieces.helpers import (
    _covers_by_piece,
    _list_tree,
    _resolve_filter_tag_ids,
    _language_options,
    _apply_filters,
    _unit_picker_tree,
    _voicings_by_piece,
    _accompaniments_by_piece,
    _placement_summaries,
)


@router.get("")
async def list_pieces(
    request: Request,
    q: str | None = None,
    view: str = "list",
    tag: list[str] | None = Query(default=None),
    voicing: list[str] | None = Query(default=None),
    accompaniment: list[str] | None = Query(default=None),
    language: list[str] | None = Query(default=None),
    unit: int | None = None,
    include_subunits: bool = False,
    sort: str = "created_desc",
    period: str = "all",
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    if view == "tree":
        return await _list_tree(request, user, session)

    from datetime import datetime, timedelta

    period_cutoff: datetime | None = None
    if period in {"7", "30", "90"}:
        period_cutoff = now_utc() - timedelta(days=int(period))

    def apply_sort(stmt):
        if sort == "created_asc":
            return stmt.order_by(Piece.created_at.asc())
        if sort == "title":
            return stmt.order_by(Piece.title)
        if sort == "title_desc":
            return stmt.order_by(Piece.title.desc())
        return stmt.order_by(Piece.created_at.desc())

    # Bygg basquery - använd FTS om q givet, annars vanlig select
    if q:
        from sqlalchemy import text

        fts_rows = session.exec(
            text(
                "SELECT id FROM pieces_fts JOIN pieces ON pieces.id = pieces_fts.rowid "
                "WHERE pieces_fts MATCH :q ORDER BY rank LIMIT 300"
            ),
            params={"q": q + "*"},
        ).all()
        candidate_ids = [r[0] for r in fts_rows]
        if not candidate_ids:
            pieces = []
        else:
            stmt = select(Piece).where(Piece.id.in_(candidate_ids))
            stmt = _apply_filters(stmt, session, tag, voicing, accompaniment, language, unit, include_subunits)
            if period_cutoff is not None:
                stmt = stmt.where(Piece.created_at >= period_cutoff)
            stmt = apply_sort(stmt)
            pieces = list(session.exec(stmt).all())
    else:
        stmt = select(Piece)
        stmt = _apply_filters(stmt, session, tag, voicing, accompaniment, language, unit, include_subunits)
        if period_cutoff is not None:
            stmt = stmt.where(Piece.created_at >= period_cutoff)
        stmt = apply_sort(stmt)
        pieces = list(session.exec(stmt.limit(200)).all())

    covers = _covers_by_piece(session, [p.id for p in pieces])

    placement_summary = _placement_summaries(session, [p.id for p in pieces])
    voicings_by_piece = _voicings_by_piece(session, [p.id for p in pieces])
    accompaniments_by_piece = _accompaniments_by_piece(session, [p.id for p in pieces])

    def cover_thumb(piece_id: int) -> str | None:
        cover = covers.get(piece_id)
        return thumbnail_url_path(cover.image_path) if cover else None

    # Hämta alla taggar för filter-chipsen, grupperat per kind
    all_tags = session.exec(
        select(Tag).order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()
    tags_by_kind: dict[str, list[Tag]] = {}
    for t in all_tags:
        tags_by_kind.setdefault(t.kind, []).append(t)

    # Voicings/ackompanjemang har egna filter; resterande kinds blandas i
    # det generella tag-filtret längre ner.
    voicings = [
        t.name for t in session.exec(
            select(Tag).where(Tag.kind == "voicing").order_by(Tag.sort_order, Tag.name)
        ).all()
    ]
    accompaniments = [
        t.name for t in session.exec(
            select(Tag).where(Tag.kind == "accompaniment").order_by(Tag.sort_order, Tag.name)
        ).all()
    ]
    # tags_by_kind utan voicing/accompaniment - för tagg-filtret
    other_tags_by_kind = {
        k: v for k, v in tags_by_kind.items()
        if k not in ("voicing", "accompaniment")
    }
    languages = [
        lang for lang in session.exec(
            select(Piece.language).where(Piece.language.is_not(None)).distinct()
        ).all() if lang
    ]

    # Info om unit-filter för banner
    unit_info = None
    if unit:
        unit_obj = session.get(StorageUnit, unit)
        if unit_obj:
            parts = [unit_obj.name]
            cur = unit_obj
            while cur.parent_id:
                cur = session.get(StorageUnit, cur.parent_id)
                if not cur:
                    break
                parts.append(cur.name)
            loc = session.get(StorageLocation, unit_obj.location_id)
            if loc:
                parts.append(loc.name)
            unit_info = {"id": unit, "path": " › ".join(reversed(parts))}

    is_htmx = request.headers.get("HX-Request") == "true"
    template = "pieces/_list_content.html" if is_htmx else "pieces/list.html"
    response = render(
        request,
        template,
        {
            "pieces": pieces,
            "q": q or "",
            "view": "grid" if view == "grid" else "list",
            "cover_thumb": cover_thumb,
            "placement_summary": placement_summary,
            "voicings_by_piece": voicings_by_piece,
            "accompaniments_by_piece": accompaniments_by_piece,
            "tags_by_kind": tags_by_kind,
            "other_tags_by_kind": other_tags_by_kind,
            "active_tags": set(tag or []),
            "voicings": sorted(voicings),
            "active_voicings": set(voicing or []),
            "accompaniments": sorted(accompaniments),
            "active_accompaniments": set(accompaniment or []),
            "languages": _language_options(sorted(languages)),
            "active_languages": set(language or []),
            "active_unit": unit_info,
            "include_subunits": include_subunits,
            "unit_tree": _unit_picker_tree(session),
            "sort": sort,
            "period": period,
        },
        user=user,
    )
    if is_htmx:
        # Uppdatera URL-fältet och tillåt back/forward-knappen att fungera
        response.headers["HX-Push-Url"] = str(request.url)
    return response


@router.get("/print")
async def print_list(
    request: Request,
    q: str | None = None,
    tag: list[str] | None = Query(default=None),
    voicing: list[str] | None = Query(default=None),
    accompaniment: list[str] | None = Query(default=None),
    language: list[str] | None = Query(default=None),
    unit: int | None = None,
    include_subunits: bool = False,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Utskriftsvänlig vy med samma filter som /pieces."""
    if q:
        from sqlalchemy import text

        fts_rows = session.exec(
            text(
                "SELECT id FROM pieces_fts JOIN pieces ON pieces.id = pieces_fts.rowid "
                "WHERE pieces_fts MATCH :q ORDER BY rank LIMIT 1000"
            ),
            params={"q": q + "*"},
        ).all()
        candidate_ids = [r[0] for r in fts_rows]
        stmt = select(Piece).where(Piece.id.in_(candidate_ids)) if candidate_ids else None
    else:
        stmt = select(Piece).order_by(Piece.title)

    if stmt is None:
        pieces = []
    else:
        stmt = _apply_filters(stmt, session, tag, voicing, accompaniment, language, unit, include_subunits)
        pieces = list(session.exec(stmt.limit(2000)).all())

    # Hämta placeringar grupperade
    placement_views: dict[int, list[str]] = {}
    if pieces:
        placements = session.exec(
            select(PiecePlacement).where(PiecePlacement.piece_id.in_([p.id for p in pieces]))
        ).all()
        units = {u.id: u for u in session.exec(select(StorageUnit)).all()}
        locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
        for pl in placements:
            unit = units.get(pl.storage_unit_id)
            if not unit:
                continue
            parts = [unit.name]
            cur = unit
            while cur.parent_id:
                cur = units.get(cur.parent_id)
                if not cur:
                    break
                parts.append(cur.name)
            loc = locations.get(unit.location_id)
            if loc:
                parts.append(loc.name)
            path = " › ".join(reversed(parts))
            copies = f" ({pl.copies} ex)" if pl.copies else ""
            placement_views.setdefault(pl.piece_id, []).append(path + copies)

    return render(
        request,
        "pieces/print.html",
        {
            "pieces": pieces,
            "placements_by_piece": placement_views,
            "q": q or "",
            "active_tags": tag or [],
            "active_voicing": voicing or "",
            "active_language": language or "",
        },
        user=user,
    )


@router.get("/print.pdf")
async def print_pdf(
    request: Request,
    q: str | None = None,
    tag: list[str] | None = Query(default=None),
    voicing: list[str] | None = Query(default=None),
    accompaniment: list[str] | None = Query(default=None),
    language: list[str] | None = Query(default=None),
    unit: int | None = None,
    include_subunits: bool = False,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Renderar samma filtrerade lista som /pieces/print men som PDF
    via WeasyPrint - lämplig att skicka som körpärm eller spara som fil."""
    from datetime import datetime

    from weasyprint import HTML

    if q:
        from sqlalchemy import text

        fts_rows = session.exec(
            text(
                "SELECT id FROM pieces_fts JOIN pieces ON pieces.id = pieces_fts.rowid "
                "WHERE pieces_fts MATCH :q ORDER BY rank LIMIT 1000"
            ),
            params={"q": q + "*"},
        ).all()
        candidate_ids = [r[0] for r in fts_rows]
        stmt = select(Piece).where(Piece.id.in_(candidate_ids)) if candidate_ids else None
    else:
        stmt = select(Piece).order_by(Piece.title)

    if stmt is None:
        pieces = []
    else:
        stmt = _apply_filters(stmt, session, tag, voicing, accompaniment, language, unit, include_subunits)
        pieces = list(session.exec(stmt.limit(2000)).all())

    placement_views: dict[int, list[str]] = {}
    if pieces:
        placements = session.exec(
            select(PiecePlacement).where(PiecePlacement.piece_id.in_([p.id for p in pieces]))
        ).all()
        units = {u.id: u for u in session.exec(select(StorageUnit)).all()}
        locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
        for pl in placements:
            unit_obj = units.get(pl.storage_unit_id)
            if not unit_obj:
                continue
            parts = [unit_obj.name]
            cur = unit_obj
            while cur.parent_id:
                cur = units.get(cur.parent_id)
                if not cur:
                    break
                parts.append(cur.name)
            loc = locations.get(unit_obj.location_id)
            if loc:
                parts.append(loc.name)
            path = " › ".join(reversed(parts))
            copies = f" ({pl.copies} ex)" if pl.copies else ""
            placement_views.setdefault(pl.piece_id, []).append(path + copies)

    voicings_by_piece = _voicings_by_piece(session, [p.id for p in pieces])
    accompaniments_by_piece = _accompaniments_by_piece(session, [p.id for p in pieces])

    html_str = templates_setup.templates.get_template("pieces/pdf.html").render(
        request=request,
        pieces=pieces,
        placements_by_piece=placement_views,
        voicings_by_piece=voicings_by_piece,
        accompaniments_by_piece=accompaniments_by_piece,
        language_name_sv=templates_setup.templates.env.globals["language_name_sv"],
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        q=q or "",
        active_tags=tag or [],
        active_voicings=voicing or [],
        active_accompaniments=accompaniment or [],
        active_languages=language or [],
    )
    pdf_bytes = HTML(string=html_str).write_pdf()
    filename = f"notarkiv-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/qr-labels")
async def qr_labels(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
    tag: list[str] | None = Query(None),
    unit: int | None = Query(None),
) -> Response:
    """Utskriftsvänlig sida med QR-etiketter för pieces. Stödjer filter
    via tag och unit (samma syntax som /pieces) så man kan skriva ut
    etiketter för en delmängd."""
    stmt = select(Piece).order_by(Piece.title)
    if unit:
        piece_ids = list(
            session.exec(
                select(PiecePlacement.piece_id)
                .where(PiecePlacement.storage_unit_id == unit)
                .distinct()
            ).all()
        )
        if piece_ids:
            stmt = stmt.where(Piece.id.in_(piece_ids))
        else:
            stmt = stmt.where(Piece.id == -1)
    if tag:
        tag_ids = _resolve_filter_tag_ids(session, tag)
        if tag_ids:
            piece_ids = list(
                session.exec(
                    select(PieceTag.piece_id)
                    .where(PieceTag.tag_id.in_(tag_ids))
                    .distinct()
                ).all()
            )
            if piece_ids:
                stmt = stmt.where(Piece.id.in_(piece_ids))
            else:
                stmt = stmt.where(Piece.id == -1)
        else:
            stmt = stmt.where(Piece.id == -1)
    pieces = session.exec(stmt).all()
    return render(
        request,
        "pieces/qr_labels.html",
        {"pieces": pieces},
        user=user,
    )


@router.get("/qr-labels.pdf")
async def qr_labels_pdf(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
    tag: list[str] | None = Query(None),
    unit: int | None = Query(None),
) -> Response:
    """Samma filter som /pieces/qr-labels men genererar PDF via WeasyPrint.
    QR-bilderna embeddas som base64-data-URI så WeasyPrint kan rendera utan
    HTTP-callbacks till egna servern."""
    import base64
    import io
    import qrcode

    stmt = select(Piece).order_by(Piece.title)
    if unit:
        piece_ids = list(
            session.exec(
                select(PiecePlacement.piece_id)
                .where(PiecePlacement.storage_unit_id == unit)
                .distinct()
            ).all()
        )
        stmt = stmt.where(Piece.id.in_(piece_ids)) if piece_ids else stmt.where(Piece.id == -1)
    if tag:
        tag_ids = _resolve_filter_tag_ids(session, tag)
        if tag_ids:
            piece_ids = list(
                session.exec(
                    select(PieceTag.piece_id)
                    .where(PieceTag.tag_id.in_(tag_ids))
                    .distinct()
                ).all()
            )
            stmt = stmt.where(Piece.id.in_(piece_ids)) if piece_ids else stmt.where(Piece.id == -1)
        else:
            stmt = stmt.where(Piece.id == -1)
    pieces = session.exec(stmt).all()

    base = str(request.base_url).rstrip("/")
    items = []
    for p in pieces:
        if not p.public_id:
            continue
        img = qrcode.make(f"{base}/p/{p.public_id}", box_size=6, border=1)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        items.append({"piece": p, "qr_data_uri": data_uri})

    from weasyprint import HTML

    html = templates_setup.templates.get_template("pieces/qr_labels_pdf.html").render(
        request=request,
        items=items,
        generated_at=now_utc().strftime("%Y-%m-%d %H:%M"),
    )
    pdf_bytes = HTML(string=html).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="qr-etiketter.pdf"'},
    )


@router.get("/{piece_id}/qr.png")
async def piece_qr(
    request: Request,
    piece_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    import io
    import qrcode

    piece = session.get(Piece, piece_id)
    if not piece or not piece.public_id:
        raise HTTPException(404)

    base = str(request.base_url).rstrip("/")
    url = f"{base}/p/{piece.public_id}"
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
