from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from datetime import datetime

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import (
    ContributorRole,
    Person,
    Piece,
    PieceContributor,
    PieceImage,
    PiecePlacement,
    ScanSession,
    StorageLocation,
    StorageUnit,
    UnitKind,
    User,
)
from app.models.piece_image import PieceImageKind
from app.services.musicbrainz import get_client, to_suggestions
from app.services.people import (
    collect_contributors,
    find_or_create_person,
    parse_names_field,
    replace_contributors,
)
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


def _format_contributor_list(contributors: dict[ContributorRole, list[Person]], role: ContributorRole) -> str:
    """Bygg tillbaka en sträng som kan editeras: 'Felix Mendelssohn; Hugo Distler'."""
    people = contributors.get(role, [])
    return "; ".join(p.name for p in people)


@router.get("/{piece_id}/edit")
async def edit_piece_form(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    contributors = collect_contributors(session, piece_id)
    return render(
        request,
        "pieces/edit.html",
        {
            "piece": piece,
            "composers_str": _format_contributor_list(contributors, ContributorRole.COMPOSER),
            "arrangers_str": _format_contributor_list(contributors, ContributorRole.ARRANGER),
            "lyricists_str": _format_contributor_list(contributors, ContributorRole.LYRICIST),
        },
        user=user,
    )


@router.post("/{piece_id}/edit", dependencies=[Depends(verify_csrf)])
async def edit_piece_save(
    request: Request,
    piece_id: int,
    title: str = Form(...),
    original_title: str | None = Form(None),
    composer: str | None = Form(None),
    arranger: str | None = Form(None),
    lyricist: str | None = Form(None),
    language: str | None = Form(None),
    voicing: str | None = Form(None),
    accompaniment: str | None = Form(None),
    publisher: str | None = Form(None),
    edition_number: str | None = Form(None),
    psalm_number: str | None = Form(None),
    notes: str | None = Form(None),
    musicbrainz_work_id: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    piece.title = title.strip()
    piece.original_title = (original_title or "").strip() or None
    piece.language = (language or "").strip() or None
    piece.voicing = (voicing or "").strip() or None
    piece.accompaniment = (accompaniment or "").strip() or None
    piece.publisher = (publisher or "").strip() or None
    piece.edition_number = (edition_number or "").strip() or None
    piece.psalm_number = (
        int(psalm_number) if psalm_number and psalm_number.isdigit() else None
    )
    piece.notes = (notes or "").strip() or None
    piece.musicbrainz_work_id = (musicbrainz_work_id or "").strip() or None
    piece.updated_at = datetime.utcnow()

    cache = replace_contributors(
        session,
        piece_id,
        composers=parse_names_field(composer),
        arrangers=parse_names_field(arranger),
        lyricists=parse_names_field(lyricist),
    )
    piece.contributors_cache = cache or None
    session.add(piece)
    session.commit()

    flash(request, "Sparat", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_piece(
    request: Request,
    piece_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    images = session.exec(
        select(PieceImage).where(PieceImage.piece_id == piece_id)
    ).all()
    image_paths = [img.image_path for img in images]

    # Lossa skanningar som pekar på denna piece - sätt FK null
    scans = session.exec(
        select(ScanSession).where(ScanSession.resulting_piece_id == piece_id)
    ).all()
    for scan in scans:
        scan.resulting_piece_id = None
        session.add(scan)

    title = piece.title
    session.delete(piece)
    session.commit()

    # Radera bildfiler från disk
    for path in image_paths:
        delete_saved_image(path)

    flash(request, f"Raderade '{title}'", "success")
    return RedirectResponse("/pieces", status.HTTP_302_FOUND)


@router.get("/{piece_id}/musicbrainz")
async def musicbrainz_modal(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-fragment: anropar MB synkront, returnerar modal med förslag."""
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    composer_str = ""
    contributors = collect_contributors(session, piece_id)
    composers = contributors.get(ContributorRole.COMPOSER, [])
    if composers:
        composer_str = composers[0].name

    suggestions = []
    error = None
    try:
        client = get_client()
        works = await client.search_work(piece.title, composer_str or None)
        suggestions = to_suggestions(works, piece.title, composer_str or None, threshold=40)
    except Exception as exc:
        error = str(exc)

    return render(
        request,
        "pieces/_musicbrainz_modal.html",
        {"piece": piece, "suggestions": suggestions, "error": error},
        user=user,
    )


@router.post("/{piece_id}/apply-musicbrainz", dependencies=[Depends(verify_csrf)])
async def apply_musicbrainz(
    request: Request,
    piece_id: int,
    mbid: str = Form(...),
    title: str = Form(...),
    composer: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    piece.musicbrainz_work_id = mbid
    if title and title != piece.title:
        piece.title = title

    if composer:
        # Hitta första kompositören och uppdatera namn till MB:s kanoniska
        existing_comp = session.exec(
            select(PieceContributor)
            .where(PieceContributor.piece_id == piece_id)
            .where(PieceContributor.role == ContributorRole.COMPOSER)
            .order_by(PieceContributor.sort_order)
        ).first()
        if existing_comp:
            person = session.get(Person, existing_comp.person_id)
            if person and person.name != composer:
                person.name = composer
                person.updated_at = datetime.utcnow()
                session.add(person)
        else:
            person = find_or_create_person(session, composer)
            if person:
                session.add(
                    PieceContributor(
                        piece_id=piece_id,
                        person_id=person.id,
                        role=ContributorRole.COMPOSER,
                    )
                )
        # Bygg om cache
        contributors = collect_contributors(session, piece_id)
        cache_parts = []
        for role, people in contributors.items():
            for p in people:
                # Använd uppdaterat namn för composer om det är personen vi just bytt
                name = composer if role == ContributorRole.COMPOSER else p.name
                cache_parts.append(f"{name} ({role.value})")
        piece.contributors_cache = "; ".join(cache_parts) or None

    piece.updated_at = datetime.utcnow()
    session.add(piece)
    session.commit()

    flash(request, "MusicBrainz-data applicerad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


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
