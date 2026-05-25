from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from datetime import datetime

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import (
    ContributorRole,
    Loan,
    Person,
    Piece,
    PieceContributor,
    PieceImage,
    PiecePlacement,
    PiecePsalmRef,
    PieceTag,
    PieceUserNote,
    PsalmBook,
    PsalmEntry,
    ScanSession,
    ScanSessionImage,
    StorageLocation,
    StorageUnit,
    Tag,
    UnitKind,
    User,
)
from app.models.piece_image import PieceImageKind
from app.models.tag import TagKind
from app.services.musicbrainz import (
    commons_file_to_thumb_url,
    download_image_bytes,
    extract_image_url,
    fetch_wikipedia_summary,
    first_composer_from_work,
    get_client,
    get_wikipedia_url,
    to_suggestions,
)
from app.services.people import (
    all_people_for_autocomplete,
    all_people_names,
    collect_contributors,
    enrich_person_from_mb,
    find_or_create_person,
    parse_names_field,
    parse_sort_field,
    replace_contributors,
)
from app.templates_setup import flash, render
from app.utils.images import (
    delete_saved_image,
    rotate_saved_image,
    save_uploaded_cover,
    thumbnail_url_path,
)  # noqa: F401 - save_uploaded_cover används också för MB-portrait-import

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
    view: str = "list",
    tag: list[str] | None = Query(default=None),
    voicing: list[str] | None = Query(default=None),
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

    from datetime import datetime, timedelta, timezone

    period_cutoff: datetime | None = None
    if period in {"7", "30", "90"}:
        period_cutoff = datetime.now(timezone.utc) - timedelta(days=int(period))

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
            stmt = _apply_filters(stmt, session, tag, voicing, language, unit, include_subunits)
            if period_cutoff is not None:
                stmt = stmt.where(Piece.created_at >= period_cutoff)
            stmt = apply_sort(stmt)
            pieces = list(session.exec(stmt).all())
    else:
        stmt = select(Piece)
        stmt = _apply_filters(stmt, session, tag, voicing, language, unit, include_subunits)
        if period_cutoff is not None:
            stmt = stmt.where(Piece.created_at >= period_cutoff)
        stmt = apply_sort(stmt)
        pieces = list(session.exec(stmt.limit(200)).all())

    covers = _covers_by_piece(session, [p.id for p in pieces])

    placement_summary = _placement_summaries(session, [p.id for p in pieces])
    voicings_by_piece = _voicings_by_piece(session, [p.id for p in pieces])

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

    # Voicings = taggar med kind=voicing (sorterade på sort_order)
    voicings = [
        t.name for t in session.exec(
            select(Tag).where(Tag.kind == "voicing").order_by(Tag.sort_order, Tag.name)
        ).all()
    ]
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

    return render(
        request,
        "pieces/list.html",
        {
            "pieces": pieces,
            "q": q or "",
            "view": "grid" if view == "grid" else "list",
            "cover_thumb": cover_thumb,
            "placement_summary": placement_summary,
            "voicings_by_piece": voicings_by_piece,
            "tags_by_kind": tags_by_kind,
            "active_tags": set(tag or []),
            "voicings": sorted(voicings),
            "active_voicings": set(voicing or []),
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


async def _list_tree(request: Request, user: User, session: Session) -> Response:
    """Trädvy: lagringsplatser och enheter hierarkiskt, med antal placeringar."""
    from sqlalchemy import func as sqlf

    locations = session.exec(
        select(StorageLocation).order_by(StorageLocation.sort_order, StorageLocation.name)
    ).all()
    units = session.exec(
        select(StorageUnit)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order, StorageUnit.name)
    ).all()

    # Räkna placeringar per unit
    counts: dict[int, int] = {}
    rows = session.exec(
        select(PiecePlacement.storage_unit_id, sqlf.count(PiecePlacement.id))
        .group_by(PiecePlacement.storage_unit_id)
    ).all()
    counts = dict(rows)

    # Bygg träd
    units_by_parent: dict[tuple[int, int | None], list[StorageUnit]] = {}
    for u in units:
        units_by_parent.setdefault((u.location_id, u.parent_id), []).append(u)

    def build_subtree(location_id: int, parent_id: int | None) -> list[dict]:
        children = units_by_parent.get((location_id, parent_id), [])
        result = []
        for c in children:
            subtree = build_subtree(location_id, c.id)
            # Aggregera count (egen + alla under)
            total = counts.get(c.id, 0) + sum(s["total"] for s in subtree)
            result.append({"unit": c, "own": counts.get(c.id, 0), "total": total, "children": subtree})
        return result

    tree = []
    for loc in locations:
        subtree = build_subtree(loc.id, None)
        total = sum(s["total"] for s in subtree)
        tree.append({"location": loc, "total": total, "units": subtree})

    return render(request, "pieces/list_tree.html", {"tree": tree, "view": "tree"}, user=user)


@router.get("/print")
async def print_list(
    request: Request,
    q: str | None = None,
    tag: list[str] | None = Query(default=None),
    voicing: list[str] | None = Query(default=None),
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
        stmt = _apply_filters(stmt, session, tag, voicing, language, unit, include_subunits)
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


def _descendant_unit_ids(session: Session, root_id: int) -> list[int]:
    """Returnera root_id plus alla rekursiva barn-ID:n. In-memory BFS."""
    all_units = session.exec(select(StorageUnit.id, StorageUnit.parent_id)).all()
    children_map: dict[int, list[int]] = {}
    for uid, parent in all_units:
        if parent is not None:
            children_map.setdefault(parent, []).append(uid)

    result = [root_id]
    queue = [root_id]
    while queue:
        cur = queue.pop()
        for child in children_map.get(cur, []):
            result.append(child)
            queue.append(child)
    return result


def _language_options(codes: list[str]) -> list[dict]:
    """Bygg lista med kod + display-namn (med flagga) för filterval."""
    from app.utils.languages import language_display, language_name_sv

    out = []
    for c in codes:
        out.append({"code": c, "label": language_display(c) or c, "name": language_name_sv(c)})
    out.sort(key=lambda r: r["name"])
    return out


def _apply_filters(stmt, session, tags, voicings, languages, unit=None, include_subunits=False):
    if tags:
        tag_ids = list(
            session.exec(select(Tag.id).where(Tag.name.in_(tags))).all()
        )
        if tag_ids:
            piece_ids_with_tag = list(
                session.exec(
                    select(PieceTag.piece_id)
                    .where(PieceTag.tag_id.in_(tag_ids))
                    .distinct()
                ).all()
            )
            if piece_ids_with_tag:
                stmt = stmt.where(Piece.id.in_(piece_ids_with_tag))
            else:
                stmt = stmt.where(Piece.id == -1)
        else:
            stmt = stmt.where(Piece.id == -1)
    if voicings:
        valid = [v for v in voicings if v]
        if valid:
            voicing_tag_ids = list(
                session.exec(
                    select(Tag.id)
                    .where(Tag.kind == "voicing")
                    .where(Tag.name.in_(valid))
                ).all()
            )
            if voicing_tag_ids:
                piece_ids_with_voicing = list(
                    session.exec(
                        select(PieceTag.piece_id)
                        .where(PieceTag.tag_id.in_(voicing_tag_ids))
                        .distinct()
                    ).all()
                )
                if piece_ids_with_voicing:
                    stmt = stmt.where(Piece.id.in_(piece_ids_with_voicing))
                else:
                    stmt = stmt.where(Piece.id == -1)
            else:
                stmt = stmt.where(Piece.id == -1)
    if languages:
        valid = [l for l in languages if l]
        if valid:
            stmt = stmt.where(Piece.language.in_(valid))
    if unit:
        if include_subunits:
            unit_ids = _descendant_unit_ids(session, unit)
        else:
            unit_ids = [unit]
        piece_ids_in_unit = list(
            session.exec(
                select(PiecePlacement.piece_id)
                .where(PiecePlacement.storage_unit_id.in_(unit_ids))
                .distinct()
            ).all()
        )
        if piece_ids_in_unit:
            stmt = stmt.where(Piece.id.in_(piece_ids_in_unit))
        else:
            stmt = stmt.where(Piece.id == -1)
    return stmt


@router.get("/new")
async def new_piece_form(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    from app.utils.languages import all_languages

    return render(
        request,
        "pieces/new.html",
        {
            "unit_options": _unit_path_options(session),
            "people_names": all_people_names(session),
            "people_options": all_people_for_autocomplete(session),
            "language_options": all_languages(),
            "voicing_tags": session.exec(
                select(Tag).where(Tag.kind == "voicing")
                .order_by(Tag.sort_order, Tag.name)
            ).all(),
            "accompaniment_tags": session.exec(
                select(Tag).where(Tag.kind == "accompaniment")
                .order_by(Tag.sort_order, Tag.name)
            ).all(),
        },
        user=user,
    )


@router.post("/new", dependencies=[Depends(verify_csrf)])
async def new_piece_save(
    request: Request,
    title: str = Form(...),
    original_title: str | None = Form(None),
    composer: str | None = Form(None),
    arranger: str | None = Form(None),
    lyricist: str | None = Form(None),
    composer_sort: str | None = Form(None),
    arranger_sort: str | None = Form(None),
    lyricist_sort: str | None = Form(None),
    language: str | None = Form(None),
    publisher: str | None = Form(None),
    edition_number: str | None = Form(None),
    notes: str | None = Form(None),
    musicbrainz_work_id: str | None = Form(None),
    spotify_url: str | None = Form(None),
    placement_unit_id: str | None = Form(None),
    placement_copies: str | None = Form(None),
    placement_notes: str | None = Form(None),
    voicing_tag_id: list[int] = Form(default=[]),
    accompaniment_tag_id: list[int] = Form(default=[]),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = Piece(
        title=title.strip(),
        original_title=(original_title or "").strip() or None,
        language=(language or "").strip() or None,
        publisher=(publisher or "").strip() or None,
        edition_number=(edition_number or "").strip() or None,
        notes=(notes or "").strip() or None,
        musicbrainz_work_id=(musicbrainz_work_id or "").strip() or None,
        spotify_url=(spotify_url or "").strip() or None,
        created_by=user.id,
        updated_at=datetime.utcnow(),
    )
    session.add(piece)
    session.flush()

    cache = replace_contributors(
        session,
        piece.id,
        composers=parse_names_field(composer),
        arrangers=parse_names_field(arranger),
        lyricists=parse_names_field(lyricist),
        composer_sorts=parse_sort_field(composer_sort),
        arranger_sorts=parse_sort_field(arranger_sort),
        lyricist_sorts=parse_sort_field(lyricist_sort),
    )
    piece.contributors_cache = cache or None
    session.add(piece)

    if placement_unit_id and placement_unit_id.isdigit():
        unit = session.get(StorageUnit, int(placement_unit_id))
        if unit:
            session.add(
                PiecePlacement(
                    piece_id=piece.id,
                    storage_unit_id=unit.id,
                    copies=(
                        int(placement_copies)
                        if placement_copies and placement_copies.isdigit()
                        else None
                    ),
                    notes=(placement_notes or "").strip() or None,
                )
            )

    _set_kind_tags(session, piece.id, "voicing", voicing_tag_id)
    _set_kind_tags(session, piece.id, "accompaniment", accompaniment_tag_id)

    session.commit()

    flash(request, f"Skapade '{piece.title}'", "success")
    return RedirectResponse(f"/pieces/{piece.id}", status.HTTP_302_FOUND)


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

    # Aktiva utlån per placering
    placement_loans: dict[int, list[Loan]] = {}
    if placements:
        active_loans = session.exec(
            select(Loan)
            .where(Loan.placement_id.in_([p.id for p in placements]))
            .where(Loan.returned_at.is_(None))
            .order_by(Loan.borrowed_at.desc())
        ).all()
        for loan in active_loans:
            placement_loans.setdefault(loan.placement_id, []).append(loan)

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
        loans_here = placement_loans.get(p.id, [])
        out_count = sum(loan.copies for loan in loans_here)
        placement_views.append(
            {
                "placement": p,
                "unit": unit,
                "location": loc,
                "path": " > ".join(reversed(parts)),
                "kind_name": kinds.get(unit.kind_id),
                "loans": loans_here,
                "out_count": out_count,
            }
        )

    contributors = collect_contributors(session, piece_id)

    # Bidragsgivare utan MBID - för "slå upp i MB"-banner
    contributors_without_mbid = []
    for role, people_list in contributors.items():
        for p in people_list:
            if not p.musicbrainz_artist_id:
                contributors_without_mbid.append({"person": p, "role": str(role)})

    # Taggar
    tag_rows = session.exec(
        select(Tag)
        .join(PieceTag, PieceTag.tag_id == Tag.id)
        .where(PieceTag.piece_id == piece_id)
        .order_by(Tag.kind, Tag.name)
    ).all()

    # Användaranteckningar - min egen + andras
    user_notes_with_user = session.exec(
        select(PieceUserNote, User)
        .join(User, User.id == PieceUserNote.user_id)
        .where(PieceUserNote.piece_id == piece_id)
        .order_by(PieceUserNote.updated_at.desc())
    ).all()
    my_note = next(
        (note for note, u in user_notes_with_user if u.id == user.id), None
    )
    others_notes = [
        (note, u) for note, u in user_notes_with_user if u.id != user.id
    ]

    # Användarlista för låntagar-dropdown i utlåningsmodalen
    loan_users = session.exec(select(User).order_by(User.username)).all()

    # Psalm-referenser till denna not
    psalm_refs = session.exec(
        select(PiecePsalmRef, PsalmBook)
        .join(PsalmBook, PsalmBook.id == PiecePsalmRef.book_id)
        .where(PiecePsalmRef.piece_id == piece_id)
        .order_by(PsalmBook.sort_order, PsalmBook.name, PiecePsalmRef.number)
    ).all()

    return render(
        request,
        "pieces/detail.html",
        {
            "piece": piece,
            "images": images,
            "placements": placement_views,
            "contributors": contributors,
            "contributors_without_mbid": contributors_without_mbid,
            "tags": tag_rows,
            "my_note": my_note,
            "others_notes": others_notes,
            "composer_role": ContributorRole.COMPOSER,
            "arranger_role": ContributorRole.ARRANGER,
            "lyricist_role": ContributorRole.LYRICIST,
            "image_kinds": [k.value for k in PieceImageKind],
            "loan_users": loan_users,
            "psalm_refs": psalm_refs,
        },
        user=user,
    )


def _format_contributor_list(contributors: dict[ContributorRole, list[Person]], role: ContributorRole) -> str:
    """Bygg tillbaka en sträng som kan editeras: 'Felix Mendelssohn; Hugo Distler'."""
    people = contributors.get(role, [])
    return "; ".join(p.name for p in people)


def _format_contributor_sorts(contributors: dict[ContributorRole, list[Person]], role: ContributorRole) -> str:
    """Bygg motsvarande sort_name-sträng - används för förifyllning i edit-form."""
    people = contributors.get(role, [])
    return "; ".join(p.sort_name or "" for p in people)


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
    images = session.exec(
        select(PieceImage)
        .where(PieceImage.piece_id == piece_id)
        .order_by(PieceImage.sort_order, PieceImage.id)
    ).all()

    # Placeringar för redigering
    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece_id)
    ).all()
    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units = {u.id: u for u in session.exec(select(StorageUnit)).all()}
    placement_views = []
    for p in placements:
        unit = units.get(p.storage_unit_id)
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
        placement_views.append(
            {
                "placement": p,
                "path": " > ".join(reversed(parts)),
                "location_kind": loc.kind if loc else "",
            }
        )

    from app.utils.languages import all_languages

    return render(
        request,
        "pieces/edit.html",
        {
            "piece": piece,
            "composers_str": _format_contributor_list(contributors, ContributorRole.COMPOSER),
            "arrangers_str": _format_contributor_list(contributors, ContributorRole.ARRANGER),
            "lyricists_str": _format_contributor_list(contributors, ContributorRole.LYRICIST),
            "composer_sorts_str": _format_contributor_sorts(contributors, ContributorRole.COMPOSER),
            "arranger_sorts_str": _format_contributor_sorts(contributors, ContributorRole.ARRANGER),
            "lyricist_sorts_str": _format_contributor_sorts(contributors, ContributorRole.LYRICIST),
            "images": images,
            "image_kinds": [k.value for k in PieceImageKind],
            "people_names": all_people_names(session),
            "people_options": all_people_for_autocomplete(session),
            "placements": placement_views,
            "unit_options": _unit_path_options(session),
            "unit_tree": _unit_picker_tree(session),
            "language_options": all_languages(),
            "psalm_refs": session.exec(
                select(PiecePsalmRef, PsalmBook)
                .join(PsalmBook, PsalmBook.id == PiecePsalmRef.book_id)
                .where(PiecePsalmRef.piece_id == piece_id)
                .order_by(PsalmBook.sort_order, PsalmBook.name, PiecePsalmRef.number)
            ).all(),
            "psalm_books": session.exec(
                select(PsalmBook).order_by(PsalmBook.sort_order, PsalmBook.name)
            ).all(),
            "voicing_tags": session.exec(
                select(Tag).where(Tag.kind == "voicing")
                .order_by(Tag.sort_order, Tag.name)
            ).all(),
            "accompaniment_tags": session.exec(
                select(Tag).where(Tag.kind == "accompaniment")
                .order_by(Tag.sort_order, Tag.name)
            ).all(),
            "selected_voicing_ids": set(
                session.exec(
                    select(PieceTag.tag_id)
                    .join(Tag, Tag.id == PieceTag.tag_id)
                    .where(PieceTag.piece_id == piece_id)
                    .where(Tag.kind == "voicing")
                ).all()
            ),
            "selected_accompaniment_ids": set(
                session.exec(
                    select(PieceTag.tag_id)
                    .join(Tag, Tag.id == PieceTag.tag_id)
                    .where(PieceTag.piece_id == piece_id)
                    .where(Tag.kind == "accompaniment")
                ).all()
            ),
            # Övriga taggar (inte voicing/accompaniment) grupperade per kind
            "other_tags_by_kind": _other_tags_grouped(session),
            "selected_other_tag_ids": set(
                session.exec(
                    select(PieceTag.tag_id)
                    .join(Tag, Tag.id == PieceTag.tag_id)
                    .where(PieceTag.piece_id == piece_id)
                    .where(Tag.kind.not_in(["voicing", "accompaniment"]))
                ).all()
            ),
        },
        user=user,
    )


def _other_tags_grouped(session: Session) -> dict[str, list]:
    """Hämta alla taggar utom voicing/accompaniment grupperade per kind."""
    rows = session.exec(
        select(Tag)
        .where(Tag.kind.not_in(["voicing", "accompaniment"]))
        .order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()
    out: dict[str, list] = {}
    for t in rows:
        out.setdefault(t.kind, []).append(t)
    return out


@router.post("/{piece_id}/edit", dependencies=[Depends(verify_csrf)])
async def edit_piece_save(
    request: Request,
    piece_id: int,
    title: str = Form(...),
    original_title: str | None = Form(None),
    composer: str | None = Form(None),
    arranger: str | None = Form(None),
    lyricist: str | None = Form(None),
    composer_sort: str | None = Form(None),
    arranger_sort: str | None = Form(None),
    lyricist_sort: str | None = Form(None),
    language: str | None = Form(None),
    publisher: str | None = Form(None),
    edition_number: str | None = Form(None),
    notes: str | None = Form(None),
    musicbrainz_work_id: str | None = Form(None),
    spotify_url: str | None = Form(None),
    voicing_tag_id: list[int] = Form(default=[]),
    accompaniment_tag_id: list[int] = Form(default=[]),
    tag_id: list[int] = Form(default=[]),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    piece.title = title.strip()
    piece.original_title = (original_title or "").strip() or None
    piece.language = (language or "").strip() or None
    piece.publisher = (publisher or "").strip() or None
    piece.edition_number = (edition_number or "").strip() or None
    piece.notes = (notes or "").strip() or None
    piece.musicbrainz_work_id = (musicbrainz_work_id or "").strip() or None
    piece.spotify_url = (spotify_url or "").strip() or None
    piece.updated_at = datetime.utcnow()

    cache = replace_contributors(
        session,
        piece_id,
        composers=parse_names_field(composer),
        arrangers=parse_names_field(arranger),
        lyricists=parse_names_field(lyricist),
        composer_sorts=parse_sort_field(composer_sort),
        arranger_sorts=parse_sort_field(arranger_sort),
        lyricist_sorts=parse_sort_field(lyricist_sort),
    )
    piece.contributors_cache = cache or None
    session.add(piece)

    _set_kind_tags(session, piece_id, "voicing", voicing_tag_id)
    _set_kind_tags(session, piece_id, "accompaniment", accompaniment_tag_id)

    # Övriga taggar (liturgical/occasion/free) - rensa befintliga + sätt nya
    existing_other = session.exec(
        select(PieceTag, Tag)
        .join(Tag, Tag.id == PieceTag.tag_id)
        .where(PieceTag.piece_id == piece_id)
        .where(Tag.kind.not_in(["voicing", "accompaniment"]))
    ).all()
    for pt, _t in existing_other:
        session.delete(pt)
    session.flush()
    for tid in tag_id:
        t = session.get(Tag, tid)
        if t and t.kind not in ("voicing", "accompaniment"):
            session.add(PieceTag(piece_id=piece_id, tag_id=t.id))

    session.commit()

    flash(request, "Sparat", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/musicbrainz-id/clear", dependencies=[Depends(verify_csrf)])
async def clear_piece_mb_work_id(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    piece.musicbrainz_work_id = None
    piece.updated_at = datetime.utcnow()
    session.add(piece)
    session.commit()
    flash(request, "Rensade MusicBrainz-koppling", "success")
    return RedirectResponse(f"/pieces/{piece_id}/edit", status.HTTP_302_FOUND)


@router.post("/{piece_id}/re-ocr", dependencies=[Depends(verify_csrf)])
async def re_ocr_piece(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Skapa en ny ScanSession från piecens primärbild och kö:a OCR-jobb.
    Användare granskar resultatet via /scan/queue → /scan/{id}/review.
    Original-piecen påverkas inte förrän användaren sparar granskningen."""
    from app.config import settings as cfg
    from app.models.scan_session import ScanStatus
    from app.services.app_settings import get_ocr_provider
    from app.tasks import get_pool

    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    primary = session.exec(
        select(PieceImage)
        .where(PieceImage.piece_id == piece_id)
        .order_by(PieceImage.sort_order, PieceImage.id)
    ).first()
    if not primary:
        flash(request, "Ingen bild kopplad till noten - kan inte köra OCR", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    scan = ScanSession(
        image_path=primary.image_path,
        status=ScanStatus.PENDING,
        ocr_provider=get_ocr_provider(),
        user_id=user.id,
        target_piece_id=piece_id,
    )
    session.add(scan)
    session.commit()
    session.refresh(scan)

    try:
        pool = await get_pool()
        await pool.enqueue_job("extract_metadata_job", scan.id)
    except Exception as exc:
        from loguru import logger as _log

        _log.warning("Kunde inte kö:a re-OCR-jobb: {}", exc)
        flash(request, f"Kunde inte starta jobbet: {exc}", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    flash(
        request,
        f"OCR körs i bakgrunden. När den är klar dyker den upp i granskningskön (#{scan.id}).",
        "success",
    )
    return RedirectResponse(f"/scan/{scan.id}", status.HTTP_302_FOUND)


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

    # Samla person-IDs som var kopplade till denna piece innan delete
    contributor_person_ids = {
        pc.person_id for pc in session.exec(
            select(PieceContributor).where(PieceContributor.piece_id == piece_id)
        ).all()
    }

    # Lossa skanningar som pekar på denna piece - sätt FK null
    scans = session.exec(
        select(ScanSession).where(ScanSession.resulting_piece_id == piece_id)
    ).all()
    for scan in scans:
        scan.resulting_piece_id = None
        session.add(scan)
    # Också re-OCR-skanningar som har piecen som mål
    target_scans = session.exec(
        select(ScanSession).where(ScanSession.target_piece_id == piece_id)
    ).all()
    for scan in target_scans:
        scan.target_piece_id = None
        session.add(scan)

    title = piece.title
    session.delete(piece)
    session.commit()

    # Radera bildfiler från disk - men bara de som inte refereras av
    # andra pieces (re-OCR och dubletter delar ofta samma image_path)
    for path in image_paths:
        still_used_piece = session.exec(
            select(PieceImage.id).where(PieceImage.image_path == path).limit(1)
        ).first()
        still_used_scan = session.exec(
            select(ScanSession.id).where(ScanSession.image_path == path).limit(1)
        ).first()
        if not still_used_piece and not still_used_scan:
            delete_saved_image(path)

    # Kolla om någon av personerna nu saknar kopplingar
    orphaned_ids: list[int] = []
    if contributor_person_ids:
        for pid in contributor_person_ids:
            still_linked = session.exec(
                select(PieceContributor.id)
                .where(PieceContributor.person_id == pid)
                .limit(1)
            ).first()
            if not still_linked:
                orphaned_ids.append(pid)

    if orphaned_ids:
        flash(request, f"Raderade '{title}'", "success")
        ids_param = ",".join(str(i) for i in orphaned_ids)
        return RedirectResponse(
            f"/people/orphaned?ids={ids_param}", status.HTTP_302_FOUND
        )

    flash(request, f"Raderade '{title}'", "success")
    return RedirectResponse("/pieces", status.HTTP_302_FOUND)


@router.get("/{piece_id}/enrich")
async def enrich_wizard(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Auto-sök MB för varje bidragsgivare utan MBID. Visa topp 3 per person
    så användaren accepterar eller skippar."""
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    contributors = collect_contributors(session, piece_id)
    unenriched = []
    for role, people_list in contributors.items():
        for p in people_list:
            if not p.musicbrainz_artist_id:
                unenriched.append({"person": p, "role": str(role)})

    client = get_client()
    for item in unenriched:
        try:
            results = await client.search_artist(item["person"].name)
            item["candidates"] = results[:3]
            item["error"] = None
        except Exception as exc:
            item["candidates"] = []
            item["error"] = str(exc)

    return render(
        request,
        "pieces/enrich_wizard.html",
        {"piece": piece, "unenriched": unenriched},
        user=user,
    )


@router.get("/{piece_id}/musicbrainz")
async def musicbrainz_modal(
    request: Request,
    piece_id: int,
    q_title: str | None = None,
    q_composer: str | None = None,
    skip_search: int = 0,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-fragment: visar MB-sökmodal med ev. resultat.

    Om skip_search=1 visas bara förifyllt formulär utan att anropa MB
    (för att inte slösa rate-limited anrop när användaren bara öppnar modalen).
    """
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    contributors = collect_contributors(session, piece_id)
    composers = contributors.get(ContributorRole.COMPOSER, [])
    default_composer = composers[0].name if composers else ""

    search_title = (q_title or piece.title).strip()
    search_composer = (q_composer or default_composer).strip()

    suggestions = []
    error = None
    searched = False
    if not skip_search:
        searched = True
        try:
            client = get_client()
            works = await client.search_work(search_title, search_composer or None)
            suggestions = to_suggestions(
                works, search_title, search_composer or None, threshold=40
            )
        except Exception as exc:
            error = str(exc)

    return render(
        request,
        "pieces/_musicbrainz_modal.html",
        {
            "piece": piece,
            "suggestions": suggestions,
            "error": error,
            "search_title": search_title,
            "search_composer": search_composer,
            "searched": searched,
        },
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

        # Berika kompositör-Person med MB-data (MBID, levnadsår, Wikipedia)
        if person:
            try:
                client = get_client()
                work = await client.get_work_with_rels(mbid)
                if work:
                    mb_composer = first_composer_from_work(work)
                    if mb_composer and mb_composer.get("id"):
                        artist = await client.get_artist_with_urls(mb_composer["id"])
                        if artist:
                            wiki_url = await get_wikipedia_url(artist)
                            wiki_bio = (
                                await fetch_wikipedia_summary(wiki_url)
                                if wiki_url
                                else None
                            )
                            if not person.portrait_image_path:
                                image_page_url = extract_image_url(artist)
                                if image_page_url:
                                    thumb_url = commons_file_to_thumb_url(image_page_url, 600)
                                    if thumb_url:
                                        img_bytes = await download_image_bytes(thumb_url)
                                        if img_bytes:
                                            try:
                                                person.portrait_image_path = save_uploaded_cover(img_bytes)
                                                person.portrait_source_url = image_page_url
                                            except Exception:
                                                pass
                            enrich_person_from_mb(
                                session,
                                person,
                                mb_artist=artist,
                                wikipedia_url=wiki_url,
                                biography=wiki_bio,
                            )
            except Exception as exc:
                # Berikning är best-effort - blockera inte spara
                from loguru import logger as _log

                _log.warning("MB-berikning av Person misslyckades: {}", exc)

        # Bygg om cache
        contributors = collect_contributors(session, piece_id)
        cache_parts = []
        for role, people in contributors.items():
            role_str = role.value if hasattr(role, "value") else str(role)
            for p in people:
                # Använd uppdaterat namn för composer om det är personen vi just bytt
                name = composer if role_str == ContributorRole.COMPOSER.value else p.name
                cache_parts.append(f"{name} ({role_str})")
        piece.contributors_cache = "; ".join(cache_parts) or None

    piece.updated_at = datetime.utcnow()
    session.add(piece)
    session.commit()

    flash(request, "MusicBrainz-data applicerad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


def _unit_picker_tree(session: Session) -> list[dict]:
    """Hierarkisk trädstruktur för unit-picker-modalen."""
    locations = session.exec(
        select(StorageLocation).order_by(StorageLocation.sort_order, StorageLocation.name)
    ).all()
    units = session.exec(
        select(StorageUnit)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order, StorageUnit.name)
    ).all()
    kinds = {k.id: k.name for k in session.exec(select(UnitKind)).all()}

    units_by_parent: dict[tuple[int, int | None], list[StorageUnit]] = {}
    for u in units:
        units_by_parent.setdefault((u.location_id, u.parent_id), []).append(u)

    def build(location_id: int, parent_id: int | None, ancestors: list[str]) -> list[dict]:
        out = []
        for u in units_by_parent.get((location_id, parent_id), []):
            path = ancestors + [u.name]
            out.append(
                {
                    "unit": u,
                    "kind_name": kinds.get(u.kind_id),
                    "path": path,
                    "children": build(location_id, u.id, path),
                }
            )
        return out

    return [
        {"location": loc, "units": build(loc.id, None, [])}
        for loc in locations
    ]


def _set_kind_tags(
    session: Session, piece_id: int, kind: str, tag_ids: list[int]
) -> None:
    """Sätt om PieceTag för en specifik tag-kind. Rensar befintliga och
    skapar nya. Tags med fel kind ignoreras."""
    # Hämta vilka existerande tag-ids på piecen som matchar denna kind
    existing = session.exec(
        select(PieceTag, Tag)
        .join(Tag, Tag.id == PieceTag.tag_id)
        .where(PieceTag.piece_id == piece_id)
        .where(Tag.kind == kind)
    ).all()
    for pt, _tag in existing:
        session.delete(pt)
    session.flush()
    for tag_id in tag_ids:
        t = session.get(Tag, tag_id)
        if t and t.kind == kind:
            session.add(PieceTag(piece_id=piece_id, tag_id=t.id))


def _voicings_by_piece(session: Session, piece_ids: list[int]) -> dict[int, list[str]]:
    """Returnera dict piece_id -> lista av voicing-tag-namn, sorterade på
    Tag.sort_order. Tom dict om inga pieces."""
    if not piece_ids:
        return {}
    rows = session.exec(
        select(PieceTag.piece_id, Tag.name)
        .join(Tag, Tag.id == PieceTag.tag_id)
        .where(PieceTag.piece_id.in_(piece_ids))
        .where(Tag.kind == "voicing")
        .order_by(Tag.sort_order, Tag.name)
    ).all()
    out: dict[int, list[str]] = {}
    for piece_id, name in rows:
        out.setdefault(piece_id, []).append(name)
    return out


def _placement_summaries(session: Session, piece_ids: list[int]) -> dict[int, dict]:
    """Per piece: antal placeringar, summa fysiska exemplar, antal digitala
    placeringar, samt rader för tooltip (path + copies/digital-markör)."""
    if not piece_ids:
        return {}

    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units_by_id = {u.id: u for u in session.exec(select(StorageUnit)).all()}

    def path_for(unit_id: int) -> str:
        unit = units_by_id.get(unit_id)
        if not unit:
            return "?"
        parts = [unit.name]
        cur = unit
        while cur.parent_id:
            cur = units_by_id.get(cur.parent_id)
            if not cur:
                break
            parts.append(cur.name)
        loc = locations.get(unit.location_id)
        if loc:
            parts.append(loc.name)
        return " › ".join(reversed(parts))

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id.in_(piece_ids))
    ).all()

    result: dict[int, dict] = {}
    for pl in placements:
        unit = units_by_id.get(pl.storage_unit_id)
        loc = locations.get(unit.location_id) if unit else None
        is_digital = bool(loc and loc.kind == "digital")

        s = result.setdefault(
            pl.piece_id,
            {"count": 0, "copies": 0, "digital": 0, "items": []},
        )
        s["count"] += 1
        if is_digital:
            s["digital"] += 1
        elif pl.copies:
            s["copies"] += pl.copies

        s["items"].append(
            {
                "path": path_for(pl.storage_unit_id),
                "copies": pl.copies,
                "digital": is_digital,
            }
        )

    for s in result.values():
        s["items"].sort(key=lambda i: i["path"])

    return result


def _unit_path_options(session: Session) -> list[dict]:
    """Flat lista av icke-arkiverade units med full sökväg som label."""
    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units = session.exec(
        select(StorageUnit).where(StorageUnit.archived == False)  # noqa: E712
    ).all()
    units_by_id = {u.id: u for u in units}

    def path_for(unit: StorageUnit) -> str:
        parts = [unit.name]
        cur = unit
        while cur.parent_id:
            cur = units_by_id.get(cur.parent_id)
            if not cur:
                break
            parts.append(cur.name)
        loc = locations.get(unit.location_id)
        if loc:
            parts.append(loc.name)
        return " > ".join(reversed(parts))

    options = []
    for u in units:
        loc = locations.get(u.location_id)
        if not loc:
            continue
        options.append({"id": u.id, "label": path_for(u), "location_kind": loc.kind})
    options.sort(key=lambda o: o["label"])
    return options


@router.post("/{piece_id}/placements", dependencies=[Depends(verify_csrf)])
async def add_placement(
    request: Request,
    piece_id: int,
    storage_unit_id: int = Form(...),
    copies: str | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    unit = session.get(StorageUnit, storage_unit_id)
    if not piece or not unit:
        raise HTTPException(404)

    existing = session.exec(
        select(PiecePlacement)
        .where(PiecePlacement.piece_id == piece_id)
        .where(PiecePlacement.storage_unit_id == storage_unit_id)
    ).first()
    if existing:
        flash(request, "Placering på den här platsen finns redan - använd redigera istället", "warning")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    copies_int = int(copies) if copies and copies.isdigit() else None
    session.add(
        PiecePlacement(
            piece_id=piece_id,
            storage_unit_id=storage_unit_id,
            copies=copies_int,
            notes=(notes or "").strip() or None,
        )
    )
    session.commit()
    flash(request, "Placering tillagd", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post(
    "/{piece_id}/placements/{placement_id}/update",
    dependencies=[Depends(verify_csrf)],
)
async def update_placement(
    request: Request,
    piece_id: int,
    placement_id: int,
    storage_unit_id: int = Form(...),
    copies: str | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    placement = session.get(PiecePlacement, placement_id)
    if not placement or placement.piece_id != piece_id:
        raise HTTPException(404)
    new_unit = session.get(StorageUnit, storage_unit_id)
    if not new_unit:
        raise HTTPException(400, "Ogiltig enhet")

    # Om unit ändras: kolla om en placering redan finns på den nya enheten
    # (vi slår ihop då - addera copies, ta bort gamla raden)
    if storage_unit_id != placement.storage_unit_id:
        other = session.exec(
            select(PiecePlacement)
            .where(PiecePlacement.piece_id == piece_id)
            .where(PiecePlacement.storage_unit_id == storage_unit_id)
            .where(PiecePlacement.id != placement_id)
        ).first()
        if other:
            new_copies = (
                int(copies) if copies and copies.isdigit() else placement.copies or 0
            )
            other.copies = (other.copies or 0) + new_copies
            if notes:
                other.notes = ((other.notes or "") + "\n" + notes.strip()).strip()
            session.add(other)
            session.delete(placement)
            session.commit()
            flash(request, "Sammanfogad med befintlig placering på den enheten", "success")
            return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    placement.storage_unit_id = storage_unit_id
    placement.copies = int(copies) if copies and copies.isdigit() else None
    placement.notes = (notes or "").strip() or None
    session.add(placement)
    session.commit()
    flash(request, "Placering uppdaterad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post(
    "/{piece_id}/placements/{placement_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
async def delete_placement(
    request: Request,
    piece_id: int,
    placement_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    placement = session.get(PiecePlacement, placement_id)
    if not placement or placement.piece_id != piece_id:
        raise HTTPException(404)
    session.delete(placement)
    session.commit()
    flash(request, "Placering borttagen", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/user-notes", dependencies=[Depends(verify_csrf)])
async def upsert_user_note(
    request: Request,
    piece_id: int,
    text: str = Form(...),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Skapa eller uppdatera min egen anteckning på en not."""
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    text = text.strip()
    existing = session.exec(
        select(PieceUserNote)
        .where(PieceUserNote.piece_id == piece_id)
        .where(PieceUserNote.user_id == user.id)
    ).first()

    if not text:
        if existing:
            session.delete(existing)
            session.commit()
            flash(request, "Din anteckning är borttagen", "info")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    if existing:
        existing.text = text
        existing.updated_at = datetime.utcnow()
        session.add(existing)
    else:
        session.add(
            PieceUserNote(piece_id=piece_id, user_id=user.id, text=text)
        )
    session.commit()
    flash(request, "Din anteckning sparad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.get("/psalmrefs/lookup")
async def psalmref_lookup(
    request: Request,
    book_id: int,
    number: int,
    edition: str | None = None,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-fragment: slå upp PsalmEntry för given bok + nummer (+ ev. utgåva)
    och returnera en liten preview-rad ('Bereden väg för Herran · Advent').
    Tom respons om ingen match - då vet användaren att numret är okänt."""
    edition_val = (edition or "").strip() or None

    stmt = (
        select(PsalmEntry)
        .where(PsalmEntry.book_id == book_id)
        .where(PsalmEntry.number == number)
    )
    if edition_val:
        stmt = stmt.where(PsalmEntry.edition == edition_val)

    entry = session.exec(stmt).first()
    if not entry and edition_val:
        # Fall back: prova utan utgåva-filter om vi inte hittade exakt match
        entry = session.exec(
            select(PsalmEntry)
            .where(PsalmEntry.book_id == book_id)
            .where(PsalmEntry.number == number)
        ).first()

    return render(
        request,
        "pieces/_psalmref_lookup.html",
        {"entry": entry, "number": number},
        user=user,
    )


@router.post("/{piece_id}/psalmrefs", dependencies=[Depends(verify_csrf)])
async def add_psalmref(
    request: Request,
    piece_id: int,
    book_id: int = Form(...),
    edition: str | None = Form(None),
    number: int = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    book = session.get(PsalmBook, book_id)
    if not piece or not book:
        raise HTTPException(404)

    edition_val = (edition or "").strip() or None
    existing = session.exec(
        select(PiecePsalmRef)
        .where(PiecePsalmRef.piece_id == piece_id)
        .where(PiecePsalmRef.book_id == book_id)
        .where(PiecePsalmRef.edition == edition_val)
        .where(PiecePsalmRef.number == number)
    ).first()
    if existing:
        flash(request, "Den referensen finns redan", "info")
    else:
        session.add(
            PiecePsalmRef(
                piece_id=piece_id,
                book_id=book_id,
                edition=edition_val,
                number=number,
            )
        )
        session.commit()
        flash(request, f"Lade till {book.name}:{edition_val or '-'}:{number}", "success")

    return RedirectResponse(f"/pieces/{piece_id}/edit", status.HTTP_302_FOUND)


@router.post("/{piece_id}/psalmrefs/{ref_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_psalmref(
    request: Request,
    piece_id: int,
    ref_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    ref = session.get(PiecePsalmRef, ref_id)
    if not ref or ref.piece_id != piece_id:
        raise HTTPException(404)
    session.delete(ref)
    session.commit()
    flash(request, "Psalmreferens borttagen", "success")
    return RedirectResponse(f"/pieces/{piece_id}/edit", status.HTTP_302_FOUND)


@router.get("/{piece_id}/tags")
async def tag_modal(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    all_tags = session.exec(
        select(Tag).order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()
    active_ids = set(
        session.exec(
            select(PieceTag.tag_id).where(PieceTag.piece_id == piece_id)
        ).all()
    )

    by_kind: dict[str, list[dict]] = {}
    for t in all_tags:
        by_kind.setdefault(t.kind, []).append({"tag": t, "active": t.id in active_ids})

    return render(
        request,
        "pieces/_tag_modal.html",
        {"piece": piece, "by_kind": by_kind},
        user=user,
    )


@router.post("/{piece_id}/tags/{tag_id}/toggle", dependencies=[Depends(verify_csrf)])
async def toggle_tag(
    request: Request,
    piece_id: int,
    tag_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    tag = session.get(Tag, tag_id)
    if not piece or not tag:
        raise HTTPException(404)

    existing = session.exec(
        select(PieceTag)
        .where(PieceTag.piece_id == piece_id)
        .where(PieceTag.tag_id == tag_id)
    ).first()

    if existing:
        session.delete(existing)
        active = False
    else:
        session.add(PieceTag(piece_id=piece_id, tag_id=tag_id))
        active = True
    session.commit()

    if request.headers.get("HX-Request"):
        return render(
            request,
            "pieces/_tag_pill.html",
            {"piece": piece, "tag": tag, "active": active},
            user=user,
        )
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{piece_id}/tags/new", dependencies=[Depends(verify_csrf)])
async def create_and_attach_tag(
    request: Request,
    piece_id: int,
    name: str = Form(...),
    kind: str = Form("free"),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    name = name.strip()
    if not name:
        flash(request, "Tomt namn", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    existing = session.exec(select(Tag).where(Tag.name == name)).first()
    if existing:
        tag = existing
    else:
        try:
            kind_enum = TagKind(kind)
        except ValueError:
            kind_enum = TagKind.FREE
        tag = Tag(name=name, kind=kind_enum)
        session.add(tag)
        session.flush()

    already = session.exec(
        select(PieceTag)
        .where(PieceTag.piece_id == piece_id)
        .where(PieceTag.tag_id == tag.id)
    ).first()
    if not already:
        session.add(PieceTag(piece_id=piece_id, tag_id=tag.id))
    session.commit()

    flash(request, f"Tagg '{tag.name}' tillagd", "success")
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
