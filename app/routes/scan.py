import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from sqlalchemy import func

from app.deps import get_session, require_editor, verify_csrf
from app.services.app_settings import get_ocr_provider
from app.services.duplicates import find_duplicates
from app.services.inventory import append_log, get_active_session
from app.services.musicbrainz import (
    commons_file_to_thumb_url,
    download_image_bytes,
    extract_image_url,
    fetch_wikipedia_summary,
    get_client,
    get_wikipedia_url,
    to_suggestions,
)
from app.services.people import (
    all_people_for_autocomplete,
    all_people_names,
    collect_contributors,
    derive_sort_name,
    enrich_person_from_mb,
    find_or_create_person,
    parse_names_field,
    parse_sort_field,
    replace_contributors,
)
from app.models import (
    InventorySession,
    Person,
    Piece,
    PieceImage,
    PiecePlacement,
    PieceTag,
    ScanSession,
    ScanSessionImage,
    StorageLocation,
    StorageUnit,
    Tag,
    UnitKind,
    User,
)
from app.models.piece_image import PieceImageKind
from app.models.scan_session import ScanStatus
from app.models.tag import TagKind
from app.tasks import get_pool
from app.templates_setup import flash, render
from app.utils.images import save_uploaded_cover

router = APIRouter(prefix="/scan", tags=["scan"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


@router.get("")
async def scan_index(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    recent = session.exec(
        select(ScanSession).order_by(ScanSession.created_at.desc()).limit(10)
    ).all()
    pending_count = _count_pending(session)
    return render(
        request,
        "scan/capture.html",
        {
            "recent": recent,
            "ocr_provider": get_ocr_provider(),
            "pending_count": pending_count,
        },
        user=user,
    )


@router.get("/quick")
async def quick_scan_page(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Mobil-anpassad snabbskanning: tar bild + valfri placering, ingen granskning."""
    today_count = session.exec(
        select(func.count(ScanSession.id))
        .where(ScanSession.user_id == user.id)
        .where(func.date(ScanSession.created_at) == func.date(func.current_timestamp()))
    ).one()
    unit_options = _load_unit_options(session)
    pending_count = _count_pending(session)
    active_inv = get_active_session(session)
    return render(
        request,
        "scan/quick.html",
        {
            "today_count": today_count,
            "unit_options": unit_options,
            "ocr_provider": get_ocr_provider(),
            "pending_count": pending_count,
            "active_inventory": active_inv,
        },
        user=user,
    )


@router.post("/quick", dependencies=[Depends(verify_csrf)])
async def quick_scan_upload(
    request: Request,
    images: list[UploadFile] = File(...),
    placement_unit_id: str | None = Form(None),
    placement_copies: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    if not images:
        flash(request, "Inga bilder uppladdade", "danger")
        return RedirectResponse("/scan/quick", status.HTTP_302_FOUND)

    saved_paths: list[str] = []
    for upload in images:
        content = await upload.read()
        if not content or len(content) > MAX_UPLOAD_BYTES:
            flash(request, f"Bild '{upload.filename}' saknas eller är för stor", "danger")
            return RedirectResponse("/scan/quick", status.HTTP_302_FOUND)
        try:
            saved_paths.append(save_uploaded_cover(content))
        except Exception:
            flash(request, f"Kunde inte läsa '{upload.filename}'", "danger")
            return RedirectResponse("/scan/quick", status.HTTP_302_FOUND)

    primary_path = saved_paths[0]
    extra_paths = saved_paths[1:]

    unit_id = (
        int(placement_unit_id)
        if placement_unit_id and placement_unit_id.isdigit()
        else None
    )
    copies = (
        int(placement_copies) if placement_copies and placement_copies.isdigit() else None
    )

    active_inv = get_active_session(session)
    scan = ScanSession(
        user_id=user.id,
        image_path=primary_path,
        ocr_provider=get_ocr_provider(),
        status=ScanStatus.PENDING,
        pre_placement_unit_id=unit_id,
        pre_placement_copies=copies,
        inventory_session_id=active_inv.id if active_inv else None,
    )
    session.add(scan)
    session.commit()
    session.refresh(scan)

    for i, extra in enumerate(extra_paths):
        session.add(
            ScanSessionImage(
                scan_session_id=scan.id, image_path=extra, sort_order=i + 1
            )
        )
    session.commit()

    pool = await get_pool()
    await pool.enqueue_job("extract_metadata_job", scan.id)

    if active_inv:
        suffix = f" ({len(images)} bilder)" if len(images) > 1 else ""
        append_log(active_inv, f"Skanning #{scan.id} sparad{suffix}", user.username)
        session.add(active_inv)
        session.commit()

    flash(
        request,
        f"Skanning sparad ({len(images)} bild{'er' if len(images) > 1 else ''}) - i kö för granskning",
        "success",
    )
    return RedirectResponse("/scan/quick", status.HTTP_302_FOUND)


@router.get("/queue")
async def scan_queue(
    request: Request,
    view: str = "grid",
    show_discarded: bool = False,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    stmt = select(ScanSession).where(ScanSession.resulting_piece_id.is_(None))
    if not show_discarded:
        stmt = stmt.where(ScanSession.discarded == False)  # noqa: E712
    pending = session.exec(stmt.order_by(ScanSession.created_at)).all()
    discarded_count = session.exec(
        select(func.count(ScanSession.id))
        .where(ScanSession.resulting_piece_id.is_(None))
        .where(ScanSession.discarded == True)  # noqa: E712
    ).one()
    return render(
        request,
        "scan/queue.html",
        {
            "items": pending,
            "view": "list" if view == "list" else "grid",
            "show_discarded": show_discarded,
            "discarded_count": discarded_count,
        },
        user=user,
    )


@router.post("/{scan_id}/discard", dependencies=[Depends(verify_csrf)])
async def discard_scan(
    request: Request,
    scan_id: int,
    reason: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)
    if scan.resulting_piece_id:
        flash(request, "Skanningen är redan sparad som not", "info")
        return RedirectResponse("/scan/queue", status.HTTP_302_FOUND)

    scan.discarded = True
    scan.discarded_at = datetime.utcnow()
    scan.discard_reason = (reason or "").strip() or None
    session.add(scan)

    # Logga i ev. aktiv inventeringssession
    if scan.inventory_session_id:
        inv = session.get(InventorySession, scan.inventory_session_id)
        if inv and not inv.ended_at:
            append_log(inv, f"Skanning #{scan.id} avvisad", user.username)
            session.add(inv)

    session.commit()
    flash(request, f"Skanning #{scan.id} avvisad", "info")
    return RedirectResponse("/scan/queue", status.HTTP_302_FOUND)


@router.post("/{scan_id}/restore", dependencies=[Depends(verify_csrf)])
async def restore_scan(
    request: Request,
    scan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)
    scan.discarded = False
    scan.discarded_at = None
    scan.discard_reason = None
    session.add(scan)
    session.commit()
    flash(request, f"Skanning #{scan.id} återställd", "success")
    return RedirectResponse("/scan/queue?show_discarded=1", status.HTTP_302_FOUND)


def _count_pending(session: Session) -> int:
    return session.exec(
        select(func.count(ScanSession.id))
        .where(ScanSession.resulting_piece_id.is_(None))
        .where(ScanSession.discarded == False)  # noqa: E712
    ).one()


@router.post("/upload", dependencies=[Depends(verify_csrf)])
async def upload_scan(
    request: Request,
    image: UploadFile = File(...),
    provider: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    if provider not in {"claude_vision", "tesseract", "hybrid"}:
        raise HTTPException(400, "Okänd OCR-provider")

    content = await image.read()
    if not content:
        flash(request, "Tom fil uppladdad", "danger")
        return RedirectResponse("/scan", status.HTTP_302_FOUND)
    if len(content) > MAX_UPLOAD_BYTES:
        flash(request, f"Filen är för stor (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB)", "danger")
        return RedirectResponse("/scan", status.HTTP_302_FOUND)

    try:
        relative_path = save_uploaded_cover(content)
    except Exception:
        flash(request, "Kunde inte läsa bilden - är det en giltig bildfil?", "danger")
        return RedirectResponse("/scan", status.HTTP_302_FOUND)

    active_inv = get_active_session(session)
    scan = ScanSession(
        user_id=user.id,
        image_path=relative_path,
        ocr_provider=provider,
        status=ScanStatus.PENDING,
        inventory_session_id=active_inv.id if active_inv else None,
    )
    session.add(scan)
    session.commit()
    session.refresh(scan)

    pool = await get_pool()
    await pool.enqueue_job("extract_metadata_job", scan.id)

    return RedirectResponse(f"/scan/{scan.id}", status.HTTP_302_FOUND)


@router.get("/{scan_id}")
async def scan_status_page(
    request: Request,
    scan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)

    if scan.status == ScanStatus.DONE:
        return RedirectResponse(f"/scan/{scan_id}/review", status.HTTP_302_FOUND)

    return render(request, "scan/processing.html", {"scan": scan}, user=user)


@router.post("/{scan_id}/retry", dependencies=[Depends(verify_csrf)])
async def retry_scan(
    request: Request,
    scan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Kö om en misslyckad eller hängande skanning."""
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)
    if scan.resulting_piece_id:
        flash(request, "Skanningen är redan kopplad till en sparad not", "info")
        return RedirectResponse(f"/scan/{scan_id}", status.HTTP_302_FOUND)

    scan.status = ScanStatus.PENDING
    scan.error_message = None
    scan.completed_at = None
    session.add(scan)
    session.commit()

    pool = await get_pool()
    await pool.enqueue_job("extract_metadata_job", scan_id)
    flash(request, "Skanningen läggs i kön igen", "info")
    return RedirectResponse(f"/scan/{scan_id}", status.HTTP_302_FOUND)


@router.get("/{scan_id}/status")
async def scan_status_fragment(
    request: Request,
    scan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)
    return render(request, "scan/_status.html", {"scan": scan}, user=user)


@router.get("/{scan_id}/review")
async def review_form(
    request: Request,
    scan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)
    if scan.status != ScanStatus.DONE:
        return RedirectResponse(f"/scan/{scan_id}", status.HTTP_302_FOUND)

    extracted = json.loads(scan.raw_response or "{}")
    suggestions = json.loads(scan.musicbrainz_suggestion or "[]")
    tree = _load_unit_options(session)

    duplicates = find_duplicates(
        session,
        title=extracted.get("title"),
        composer=extracted.get("composer"),
        edition_number=extracted.get("edition_number"),
    )

    person_sections = await _build_person_sections(
        session,
        composer=extracted.get("composer") or "",
        arranger=extracted.get("arranger") or "",
        lyricist=extracted.get("lyricist") or "",
    )

    from app.routes.pieces import _find_psalm_title_matches
    from app.utils.languages import all_languages

    title_for_psalm = ((existing or {}).get("title") if existing else extracted.get("title")) or ""
    psalm_title_matches = _find_psalm_title_matches(session, title_for_psalm)

    # Voicing-taggar: fördefinierad lista, plus försök matcha OCR-extraherad
    # voicing mot dem så användaren bara behöver bekräfta innan spara
    voicing_tags = list(
        session.exec(
            select(Tag).where(Tag.kind == TagKind.VOICING)
            .order_by(Tag.sort_order, Tag.name)
        ).all()
    )
    accompaniment_tags = list(
        session.exec(
            select(Tag).where(Tag.kind == TagKind.ACCOMPANIMENT)
            .order_by(Tag.sort_order, Tag.name)
        ).all()
    )
    extracted_voicing = (extracted.get("voicing") or "").strip()
    matched_voicing_ids: set[int] = set()
    if extracted_voicing:
        for t in voicing_tags:
            if t.name.lower() == extracted_voicing.lower():
                matched_voicing_ids.add(t.id)

    # Ackompanjemang: case-insensitive matchning mot tag-namn
    extracted_accompaniment_raw = (extracted.get("accompaniment") or "").strip()
    matched_accompaniment_ids: set[int] = set()
    if extracted_accompaniment_raw:
        target = extracted_accompaniment_raw.lower()
        for t in accompaniment_tags:
            if t.name.lower() == target:
                matched_accompaniment_ids.add(t.id)

    # Re-OCR-läge: bygg "existing"-dict från målpiecen så formuläret kan
    # förifyllas med befintliga värden och OCR-extraherade värden visas
    # som applicerbara pillar per fält.
    existing = None
    if scan.target_piece_id:
        from app.models import ContributorRole, PieceContributor, Tag as _Tag

        target_piece = session.get(Piece, scan.target_piece_id)
        if target_piece:
            contribs = collect_contributors(session, target_piece.id)
            existing = {
                "piece": target_piece,
                "title": target_piece.title or "",
                "original_title": target_piece.original_title or "",
                "composer": "; ".join(p.name for p in contribs.get(ContributorRole.COMPOSER, [])),
                "arranger": "; ".join(p.name for p in contribs.get(ContributorRole.ARRANGER, [])),
                "lyricist": "; ".join(p.name for p in contribs.get(ContributorRole.LYRICIST, [])),
                "composer_sort": "; ".join(p.sort_name or "" for p in contribs.get(ContributorRole.COMPOSER, [])),
                "arranger_sort": "; ".join(p.sort_name or "" for p in contribs.get(ContributorRole.ARRANGER, [])),
                "lyricist_sort": "; ".join(p.sort_name or "" for p in contribs.get(ContributorRole.LYRICIST, [])),
                "language": target_piece.language or "",
                "publisher": target_piece.publisher or "",
                "edition_number": target_piece.edition_number or "",
                "notes": target_piece.notes or "",
                "musicbrainz_work_id": target_piece.musicbrainz_work_id or "",
            }
            # Förbocka piecens befintliga voicing/accompaniment-tags istället
            # för OCR-match
            matched_voicing_ids = set(
                session.exec(
                    select(PieceTag.tag_id)
                    .join(_Tag, _Tag.id == PieceTag.tag_id)
                    .where(PieceTag.piece_id == target_piece.id)
                    .where(_Tag.kind == TagKind.VOICING)
                ).all()
            )
            matched_accompaniment_ids = set(
                session.exec(
                    select(PieceTag.tag_id)
                    .join(_Tag, _Tag.id == PieceTag.tag_id)
                    .where(PieceTag.piece_id == target_piece.id)
                    .where(_Tag.kind == TagKind.ACCOMPANIMENT)
                ).all()
            )

    return render(
        request,
        "scan/review.html",
        {
            "scan": scan,
            "extracted": extracted,
            "existing": existing,
            "suggestions": suggestions,
            "person_sections": person_sections,
            "unit_options": tree,
            "duplicates": duplicates,
            "prefill_placement_unit_id": scan.pre_placement_unit_id,
            "prefill_placement_copies": scan.pre_placement_copies,
            "people_names": all_people_names(session),
            "people_options": all_people_for_autocomplete(session),
            "voicing_tags": voicing_tags,
            "matched_voicing_ids": matched_voicing_ids,
            "extracted_voicing_raw": extracted_voicing,
            "accompaniment_tags": accompaniment_tags,
            "matched_accompaniment_ids": matched_accompaniment_ids,
            "extracted_accompaniment_raw": extracted_accompaniment_raw,
            "psalm_title_matches": psalm_title_matches,
            "language_options": all_languages(),
        },
        user=user,
    )


async def _build_person_sections(
    session: Session,
    *,
    composer: str,
    arranger: str,
    lyricist: str,
) -> list[dict]:
    """Slå upp MB-kandidater för composer/arranger/lyricist-namn. Returnerar
    sektioner: [{label, role, entries: [{name, candidates, error, ...}]}]."""
    client = get_client()

    async def search_person(name: str) -> dict:
        if not name:
            return {"name": "", "candidates": [], "error": None}
        try:
            results = await client.search_artist(name)
        except Exception as exc:
            return {"name": name, "candidates": [], "error": str(exc)}
        candidates = []
        for r in results[:3]:
            if r.get("type") and r["type"].lower() != "person":
                continue
            candidates.append(r)
        return {"name": name, "candidates": candidates, "error": None}

    sections: list[dict] = []
    for role_label, role_key, names_field in [
        ("Kompositör", "composer", composer),
        ("Arrangör", "arranger", arranger),
        ("Textförfattare", "lyricist", lyricist),
    ]:
        names = parse_names_field(names_field)
        entries = []
        for name in names:
            existing = session.exec(
                select(Person).where(Person.name.ilike(name))
            ).first()
            res = await search_person(name)
            res["existing_person_id"] = existing.id if existing else None
            res["has_mbid"] = bool(existing and existing.musicbrainz_artist_id)
            entries.append(res)
        if entries:
            sections.append(
                {"label": role_label, "role": role_key, "entries": entries}
            )
    return sections


@router.post("/{scan_id}/add-placement/{piece_id}", dependencies=[Depends(verify_csrf)])
async def add_placement_to_existing(
    request: Request,
    scan_id: int,
    piece_id: int,
    placement_unit_id: str | None = Form(None),
    placement_copies: str | None = Form(None),
    add_image: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Markera skanningen som dublett: lägg till placering på befintlig piece
    istället för att skapa ny. Bilden kan eventuellt sparas som extra bild."""
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)

    unit_id = (
        int(placement_unit_id)
        if placement_unit_id and placement_unit_id.isdigit()
        else None
    )
    if unit_id:
        copies = (
            int(placement_copies)
            if placement_copies and placement_copies.isdigit()
            else None
        )
        # Kolla om placering redan finns - i så fall öka antal
        existing = session.exec(
            select(PiecePlacement)
            .where(PiecePlacement.piece_id == piece_id)
            .where(PiecePlacement.storage_unit_id == unit_id)
        ).first()
        if existing:
            existing.copies = (existing.copies or 0) + (copies or 1)
            session.add(existing)
        else:
            session.add(
                PiecePlacement(
                    piece_id=piece_id,
                    storage_unit_id=unit_id,
                    copies=copies,
                )
            )

    if add_image:
        # Lägg till bilden som extra på den befintliga noten
        last_order = session.exec(
            select(PieceImage.sort_order)
            .where(PieceImage.piece_id == piece_id)
            .order_by(PieceImage.sort_order.desc())
        ).first()
        session.add(
            PieceImage(
                piece_id=piece_id,
                image_path=scan.image_path,
                kind=PieceImageKind.OTHER,
                sort_order=(last_order or 0) + 1,
            )
        )

    scan.resulting_piece_id = piece_id
    session.add(scan)
    session.commit()

    flash(request, f"Lagt till placering på '{piece.title}'", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/{scan_id}/apply-person-mb", dependencies=[Depends(verify_csrf)])
async def apply_person_mb(
    request: Request,
    scan_id: int,
    name: str = Form(...),
    role: str = Form(...),
    mbid: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Hitta eller skapa Person med givet namn, applicera MB-data direkt.
    Returnerar ett litet HTMX-fragment som ersätter förslagskortet."""
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)

    name = name.strip()
    if not name:
        raise HTTPException(400, "name saknas")

    try:
        client = get_client()
        artist = await client.get_artist_with_urls(mbid)
    except Exception as exc:
        return render(
            request,
            "scan/_mb_person_result.html",
            {"name": name, "role": role, "error": f"MB-fel: {exc}", "ok": False},
            user=user,
        )

    if not artist:
        return render(
            request,
            "scan/_mb_person_result.html",
            {"name": name, "role": role, "error": "MB returnerade inget", "ok": False},
            user=user,
        )

    person = find_or_create_person(session, name, musicbrainz_artist_id=mbid)
    if not person:
        raise HTTPException(400, "kunde inte skapa Person")

    # Hämta Wikipedia + porträtt
    wiki_url = await get_wikipedia_url(artist)
    wiki_bio = await fetch_wikipedia_summary(wiki_url) if wiki_url else None

    if not person.portrait_image_path:
        image_page_url = extract_image_url(artist)
        if image_page_url:
            thumb_url = commons_file_to_thumb_url(image_page_url, width=600)
            if thumb_url:
                img_bytes = await download_image_bytes(thumb_url)
                if img_bytes:
                    try:
                        person.portrait_image_path = save_uploaded_cover(img_bytes)
                        person.portrait_source_url = image_page_url
                    except Exception:
                        pass

    # Eventuellt uppdatera namn till MB:s kanoniska form
    if artist.get("name") and artist["name"] != person.name:
        person.name = artist["name"]

    enrich_person_from_mb(
        session,
        person,
        mb_artist=artist,
        wikipedia_url=wiki_url,
        biography=wiki_bio,
    )
    session.commit()

    return render(
        request,
        "scan/_mb_person_result.html",
        {"name": person.name, "role": role, "person_id": person.id, "ok": True},
        user=user,
    )


@router.get("/{scan_id}/musicbrainz")
async def review_musicbrainz_modal(
    request: Request,
    scan_id: int,
    q_title: str = "",
    q_composer: str = "",
    skip_search: int = 0,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Manuell MB-sökning i granskningsflödet."""
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)

    search_title = q_title.strip()
    search_composer = q_composer.strip()

    suggestions = []
    error = None
    searched = False
    if not skip_search and search_title:
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
        "scan/_musicbrainz_modal.html",
        {
            "scan": scan,
            "suggestions": suggestions,
            "error": error,
            "search_title": search_title,
            "search_composer": search_composer,
            "searched": searched,
        },
        user=user,
    )


@router.post("/{scan_id}/save", dependencies=[Depends(verify_csrf)])
async def save_piece(
    request: Request,
    scan_id: int,
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
    placement_unit_id: str | None = Form(None),
    placement_copies: str | None = Form(None),
    next_in_queue: str | None = Form(None),
    voicing_tag_id: list[int] = Form(default=[]),
    accompaniment_tag_id: list[int] = Form(default=[]),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    scan = session.get(ScanSession, scan_id)
    if not scan:
        raise HTTPException(404)

    # Re-OCR-läge: uppdatera målpiecen istället för att skapa ny
    is_update = scan.target_piece_id is not None
    if is_update:
        piece = session.get(Piece, scan.target_piece_id)
        if not piece:
            flash(request, "Målpiecen för omkörning finns inte längre", "danger")
            return RedirectResponse("/scan/queue", status.HTTP_302_FOUND)
        piece.title = title
        piece.original_title = original_title or None
        piece.language = language or None
        piece.publisher = publisher or None
        piece.edition_number = edition_number or None
        piece.notes = notes or None
        piece.musicbrainz_work_id = musicbrainz_work_id or None
        piece.updated_at = datetime.utcnow()
        session.add(piece)
    else:
        piece = Piece(
            title=title,
            original_title=original_title or None,
            language=language or None,
            publisher=publisher or None,
            edition_number=edition_number or None,
            notes=notes or None,
            musicbrainz_work_id=musicbrainz_work_id or None,
            created_by=user.id,
            updated_at=datetime.utcnow(),
        )
        session.add(piece)
        session.flush()

    # Skapa/återanvänd Person-poster och länka via PieceContributor
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

    if not is_update:
        # Skapa primärbilden från skanningens huvudbild
        session.add(
            PieceImage(
                piece_id=piece.id,
                image_path=scan.image_path,
                kind=PieceImageKind.COVER,
                sort_order=0,
            )
        )

        # Överför ev. extra bilder från ScanSessionImage till PieceImage
        extras = session.exec(
            select(ScanSessionImage)
            .where(ScanSessionImage.scan_session_id == scan_id)
            .order_by(ScanSessionImage.sort_order)
        ).all()
        for i, extra in enumerate(extras, start=1):
            session.add(
                PieceImage(
                    piece_id=piece.id,
                    image_path=extra.image_path,
                    kind=PieceImageKind.OTHER,
                    sort_order=i,
                )
            )

        if placement_unit_id and placement_unit_id.isdigit():
            unit = session.get(StorageUnit, int(placement_unit_id))
            if unit:
                copies = (
                    int(placement_copies)
                    if placement_copies and placement_copies.isdigit()
                    else None
                )
                session.add(
                    PiecePlacement(
                        piece_id=piece.id,
                        storage_unit_id=unit.id,
                        copies=copies,
                    )
                )

    # Voicing- och ackompanjemangstaggar valda i review (kan vara flera)
    for tag_id in voicing_tag_id:
        tag = session.get(Tag, tag_id)
        if tag and tag.kind == TagKind.VOICING:
            session.add(PieceTag(piece_id=piece.id, tag_id=tag.id))
    for tag_id in accompaniment_tag_id:
        tag = session.get(Tag, tag_id)
        if tag and tag.kind == TagKind.ACCOMPANIMENT:
            session.add(PieceTag(piece_id=piece.id, tag_id=tag.id))

    scan.resulting_piece_id = piece.id
    session.add(scan)
    session.commit()

    if next_in_queue:
        next_scan = session.exec(
            select(ScanSession)
            .where(ScanSession.resulting_piece_id.is_(None))
            .where(ScanSession.discarded == False)  # noqa: E712
            .where(ScanSession.status == ScanStatus.DONE)
            .where(ScanSession.id != scan_id)
            .order_by(ScanSession.created_at)
        ).first()
        if next_scan:
            flash(
                request,
                f"Sparade '{piece.title}'. Nästa i kön: #{next_scan.id}",
                "success",
            )
            return RedirectResponse(
                f"/scan/{next_scan.id}/review", status.HTTP_302_FOUND
            )
        flash(request, f"Sparade '{piece.title}'. Kön är tom!", "success")
        return RedirectResponse("/scan/queue", status.HTTP_302_FOUND)

    flash(request, f"Sparade '{piece.title}'", "success")
    return RedirectResponse(f"/pieces/{piece.id}", status.HTTP_302_FOUND)


def _load_unit_options(session: Session) -> list[dict]:
    """Bygg en flat lista av alla units med full sökväg som etikett."""
    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units = session.exec(
        select(StorageUnit).where(StorageUnit.archived == False)  # noqa: E712
    ).all()
    kinds = {k.id: k.name for k in session.exec(select(UnitKind)).all()}
    units_by_id = {u.id: u for u in units}

    def path_for(unit: StorageUnit) -> str:
        parts = [unit.name]
        current = unit
        while current.parent_id:
            current = units_by_id.get(current.parent_id)
            if not current:
                break
            parts.append(current.name)
        loc = locations.get(unit.location_id)
        if loc:
            parts.append(loc.name)
        return " > ".join(reversed(parts))

    options = []
    for unit in units:
        loc = locations.get(unit.location_id)
        if not loc:
            continue
        options.append(
            {
                "id": unit.id,
                "label": path_for(unit),
                "kind": kinds.get(unit.kind_id),
                "location_kind": loc.kind,
            }
        )
    options.sort(key=lambda o: o["label"])
    return options
