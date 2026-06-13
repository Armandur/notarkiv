from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.utils.dates import now_utc

from app.deps import (
    get_session,
    require_admin,
    require_auth,
    require_editor,
    verify_csrf,
)
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
    ScanSession,
    StorageLocation,
    StorageUnit,
    Tag,
    UnitKind,
    User,
)
from app.models.piece_image import PieceImageKind
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
from app.services.publishers import all_publishers_for_autocomplete
from app.services.people import (
    all_people_for_autocomplete,
    all_people_names,
    collect_contributors,
    enrich_person_from_mb,
    find_or_create_person,
    replace_contributors,
)
from app.templates_setup import flash, render
from app.utils.images import (
    delete_saved_image,
    save_uploaded_cover,
)  # noqa: F401 - save_uploaded_cover används också för MB-portrait-import

from app.routes.pieces._routers import router
from app.routes.pieces.helpers import (
    _unit_picker_tree,
    _find_psalm_title_matches,
    _set_kind_tags,
    _unit_path_options,
)


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
            "publisher_options": all_publishers_for_autocomplete(session),
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
    composer: list[str] = Form(default=[]),
    arranger: list[str] = Form(default=[]),
    lyricist: list[str] = Form(default=[]),
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
    from app.services.publishers import find_or_create_publisher

    pub_clean = (publisher or "").strip() or None
    pub_entity = find_or_create_publisher(session, pub_clean)
    piece = Piece(
        title=title.strip(),
        original_title=(original_title or "").strip() or None,
        language=(language or "").strip() or None,
        publisher=pub_clean,
        publisher_id=pub_entity.id if pub_entity else None,
        edition_number=(edition_number or "").strip() or None,
        notes=(notes or "").strip() or None,
        musicbrainz_work_id=(musicbrainz_work_id or "").strip() or None,
        spotify_url=(spotify_url or "").strip() or None,
        created_by=user.id,
        updated_at=now_utc(),
    )
    session.add(piece)
    session.flush()

    cache = replace_contributors(
        session,
        piece.id,
        composers=composer,
        arrangers=arranger,
        lyricists=lyricist,
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

    # Kö MB-berikning för nya bidragsgivare utan MBID (idempotent, tål Redis-fel).
    from app.services.people import enqueue_enrich_for_piece
    await enqueue_enrich_for_piece(session, piece.id)

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

    # Aktiva utlån per placering. Hämtar även batch-namn så detalj-vyn
    # kan visa "Konsert 14 juni" bredvid låntagaren - annars omöjligt att
    # skilja flera lån av samma not till samma person åt.
    from app.models import LoanBatch

    placement_loans: dict[int, list[Loan]] = {}
    batch_by_id: dict[int, LoanBatch] = {}
    if placements:
        active_loans = session.exec(
            select(Loan)
            .where(Loan.placement_id.in_([p.id for p in placements]))
            .where(Loan.returned_at.is_(None))
            .order_by(Loan.borrowed_at.desc())
        ).all()
        for loan in active_loans:
            placement_loans.setdefault(loan.placement_id, []).append(loan)
        batch_ids = {la.batch_id for la in active_loans if la.batch_id}
        if batch_ids:
            batch_by_id = {
                b.id: b for b in session.exec(
                    select(LoanBatch).where(LoanBatch.id.in_(list(batch_ids)))
                ).all()
            }

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
                "path": " › ".join(reversed(parts)),
                "kind_name": kinds.get(unit.kind_id),
                "loans": loans_here,
                "out_count": out_count,
            }
        )

    contributors = collect_contributors(session, piece_id)

    # Bidragsgivare utan matchning mot någon extern identitetskälla.
    # En person räknas som "matchad" om hen har antingen MBID eller Wikidata-Q-id.
    contributors_without_match = []
    for role, people_list in contributors.items():
        for p in people_list:
            if not p.musicbrainz_artist_id and not p.wikidata_id:
                contributors_without_match.append({"person": p, "role": str(role)})

    # Taggar
    tag_rows = session.exec(
        select(Tag)
        .join(PieceTag, PieceTag.tag_id == Tag.id)
        .where(PieceTag.piece_id == piece_id)
        .order_by(Tag.kind, Tag.sort_order, Tag.name)
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

    # Användarens egna listor för "Lägg i lista"-dropdown + favorit-status
    from app.models import PieceList, PieceListItem
    from app.routes.lists import _ensure_favorites

    _ensure_favorites(session, user.id)
    user_lists = list(
        session.exec(
            select(PieceList)
            .where(PieceList.user_id == user.id)
            .order_by(PieceList.is_favorites.desc(), PieceList.name)
        ).all()
    )
    fav_list = next((ll for ll in user_lists if ll.is_favorites), None)
    is_favorite = False
    if fav_list:
        is_favorite = session.exec(
            select(PieceListItem)
            .where(PieceListItem.list_id == fav_list.id)
            .where(PieceListItem.piece_id == piece_id)
        ).first() is not None

    return render(
        request,
        "pieces/detail.html",
        {
            "piece": piece,
            "images": images,
            "placements": placement_views,
            "contributors": contributors,
            "contributors_without_match": contributors_without_match,
            "tags": tag_rows,
            "my_note": my_note,
            "others_notes": others_notes,
            "composer_role": ContributorRole.COMPOSER,
            "arranger_role": ContributorRole.ARRANGER,
            "lyricist_role": ContributorRole.LYRICIST,
            "image_kinds": [k.value for k in PieceImageKind],
            "loan_users": loan_users,
            "psalm_refs": psalm_refs,
            "batch_by_id": batch_by_id,
            "user_lists": user_lists,
            "is_favorite": is_favorite,
        },
        user=user,
    )


@router.get("/{piece_id}/edit")
async def edit_piece_form(
    request: Request,
    piece_id: int,
    refresh: int = 0,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    contributors = collect_contributors(session, piece_id)

    # refresh=1: hämta work från MB och bygg preview-dict
    mb_preview = None
    if refresh and piece.musicbrainz_work_id:
        try:
            client = get_client()
            work = await client.get_work_with_rels(piece.musicbrainz_work_id)
        except Exception as exc:
            flash(request, f"MB-fel: {exc}", "danger")
            work = None
        if work:
            # Plocka ut composer/lyricist/arranger från work-rels
            roles_by_kind: dict[str, list[dict]] = {
                "composer": [],
                "lyricist": [],
                "arranger": [],
            }
            for rel in work.get("relations", []):
                t = rel.get("type", "")
                artist = rel.get("artist") or {}
                if not artist.get("id"):
                    continue
                # MB-rel-typer: 'composer', 'lyricist', 'arranger'
                if t in roles_by_kind:
                    roles_by_kind[t].append({
                        "mbid": artist.get("id"),
                        "name": artist.get("name") or "",
                        "sort_name": artist.get("sort-name") or "",
                    })
            mb_preview = {
                "title": work.get("title") or "",
                "language": work.get("language") or "",
                "composers": roles_by_kind["composer"],
                "lyricists": roles_by_kind["lyricist"],
                "arrangers": roles_by_kind["arranger"],
                "mb_work_url": f"https://musicbrainz.org/work/{piece.musicbrainz_work_id}",
            }
            flash(
                request,
                "Hämtade förslag från MusicBrainz - klicka pillarna för att applicera",
                "info",
            )
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
                "path": " › ".join(reversed(parts)),
                "location_kind": loc.kind if loc else "",
            }
        )

    from app.utils.languages import all_languages

    return render(
        request,
        "pieces/edit.html",
        {
            "piece": piece,
            "selected_composers": [p.name for p in contributors.get(ContributorRole.COMPOSER, [])],
            "selected_arrangers": [p.name for p in contributors.get(ContributorRole.ARRANGER, [])],
            "selected_lyricists": [p.name for p in contributors.get(ContributorRole.LYRICIST, [])],
            "images": images,
            "image_kinds": [k.value for k in PieceImageKind],
            "people_names": all_people_names(session),
            "people_options": all_people_for_autocomplete(session),
            "publisher_options": all_publishers_for_autocomplete(session),
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
            "psalm_title_matches": [
                m for m in _find_psalm_title_matches(session, piece.title)
                if not session.exec(
                    select(PiecePsalmRef.id)
                    .where(PiecePsalmRef.piece_id == piece_id)
                    .where(PiecePsalmRef.book_id == m["entry"].book_id)
                    .where(PiecePsalmRef.number == m["entry"].number)
                    .limit(1)
                ).first()
            ],
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
            # Aktiva non-voicing/non-accompaniment-taggar för tag-area
            "piece_active_other_tags": session.exec(
                select(Tag)
                .join(PieceTag, PieceTag.tag_id == Tag.id)
                .where(PieceTag.piece_id == piece_id)
                .where(Tag.kind.not_in(["voicing", "accompaniment"]))
                .order_by(Tag.kind, Tag.sort_order, Tag.name)
            ).all(),
            "mb_preview": mb_preview,
        },
        user=user,
    )


@router.post("/{piece_id}/edit", dependencies=[Depends(verify_csrf)])
async def edit_piece_save(
    request: Request,
    piece_id: int,
    title: str = Form(...),
    original_title: str | None = Form(None),
    composer: list[str] = Form(default=[]),
    arranger: list[str] = Form(default=[]),
    lyricist: list[str] = Form(default=[]),
    language: str | None = Form(None),
    publisher: str | None = Form(None),
    edition_number: str | None = Form(None),
    notes: str | None = Form(None),
    musicbrainz_work_id: str | None = Form(None),
    spotify_url: str | None = Form(None),
    voicing_tag_id: list[int] = Form(default=[]),
    accompaniment_tag_id: list[int] = Form(default=[]),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    from app.services.publishers import find_or_create_publisher

    piece.title = title.strip()
    piece.original_title = (original_title or "").strip() or None
    piece.language = (language or "").strip() or None
    piece.publisher = (publisher or "").strip() or None
    pub_entity = find_or_create_publisher(session, piece.publisher)
    piece.publisher_id = pub_entity.id if pub_entity else None
    piece.edition_number = (edition_number or "").strip() or None
    piece.notes = (notes or "").strip() or None
    piece.musicbrainz_work_id = (musicbrainz_work_id or "").strip() or None
    piece.spotify_url = (spotify_url or "").strip() or None
    piece.updated_at = now_utc()

    cache = replace_contributors(
        session,
        piece_id,
        composers=composer,
        arrangers=arranger,
        lyricists=lyricist,
    )
    piece.contributors_cache = cache or None
    session.add(piece)

    _set_kind_tags(session, piece_id, "voicing", voicing_tag_id)
    _set_kind_tags(session, piece_id, "accompaniment", accompaniment_tag_id)

    # Övriga taggar (liturgical/occasion/free) sätts inte här - de togglas
    # direkt via HTMX i tag-area och sparas omedelbart vid klick.

    session.commit()

    # Kö MB-berikning för nya bidragsgivare utan MBID (idempotent, tål Redis-fel).
    from app.services.people import enqueue_enrich_for_piece
    await enqueue_enrich_for_piece(session, piece_id)

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
    piece.updated_at = now_utc()
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
    """Auto-sök MB och Wikidata för varje bidragsgivare utan identitetsmatch
    (saknar både MBID och Wikidata-Q-id). Visa topp 3 per källa."""
    import asyncio

    from app.services.wikidata import search_persons

    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    contributors = collect_contributors(session, piece_id)
    unenriched = []
    for role, people_list in contributors.items():
        for p in people_list:
            if not p.musicbrainz_artist_id and not p.wikidata_id:
                unenriched.append({"person": p, "role": str(role)})

    client = get_client()
    for item in unenriched:
        name = item["person"].name

        async def _mb():
            try:
                return (await client.search_artist(name))[:3], None
            except Exception as exc:
                return [], str(exc)

        async def _wd():
            try:
                return (await search_persons(name, limit=3)), None
            except Exception as exc:
                return [], str(exc)

        (mb_results, mb_error), (wd_results, wd_error) = await asyncio.gather(_mb(), _wd())
        item["candidates"] = mb_results
        item["wd_candidates"] = wd_results
        item["error"] = mb_error
        item["wd_error"] = wd_error

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
                person.updated_at = now_utc()
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
                                            except Exception as e:
                                                from loguru import logger
                                                logger.warning("Kunde inte spara porträtt för {}: {}", person.name, e)
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

    piece.updated_at = now_utc()
    session.add(piece)
    session.commit()

    flash(request, "MusicBrainz-data applicerad", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)
