from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, func, select

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import (
    ContributorRole,
    Person,
    PersonLink,
    PersonLinkKind,
    Piece,
    PieceContributor,
    User,
)
from app.services.musicbrainz import (
    commons_file_to_thumb_url,
    download_image_bytes,
    extract_image_url,
    extract_streaming_urls,
    extract_wikidata_url,
    fetch_wikipedia_summary,
    get_client,
    get_wikipedia_url,
    resolve_image_via_wikidata,
    wikidata_id_from_url,
)
from app.services.people import (
    derive_sort_name,
    enrich_person_from_mb,
    format_partial_date,
    parse_partial_date,
)
from app.templates_setup import flash, render
from app.utils.images import delete_saved_image, save_uploaded_cover

router = APIRouter(prefix="/people", tags=["people"])


@router.get("")
async def list_people(
    request: Request,
    q: str | None = None,
    role: list[str] | None = Query(default=None),
    country: list[str] | None = Query(default=None),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    from app.utils.countries import country_display

    stmt = select(Person).order_by(Person.sort_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Person.sort_name.ilike(like) | Person.name.ilike(like))
    if country:
        upper_countries = [c.upper() for c in country if c]
        if upper_countries:
            stmt = stmt.where(Person.country.in_(upper_countries))
    if role:
        valid_roles = [r for r in role if r]
        if valid_roles:
            ids_with_role = list(
                session.exec(
                    select(PieceContributor.person_id)
                    .where(PieceContributor.role.in_(valid_roles))
                    .distinct()
                ).all()
            )
            if ids_with_role:
                stmt = stmt.where(Person.id.in_(ids_with_role))
            else:
                stmt = stmt.where(Person.id == -1)

    people = session.exec(stmt).all()

    counts = dict(
        session.exec(
            select(PieceContributor.person_id, func.count(PieceContributor.piece_id))
            .group_by(PieceContributor.person_id)
        ).all()
    )

    countries = [
        c for c in session.exec(
            select(Person.country).where(Person.country.is_not(None)).distinct()
        ).all() if c
    ]
    country_options = sorted(
        [{"code": c, "label": country_display(c)} for c in countries],
        key=lambda o: o["label"],
    )

    roles = [r.value for r in ContributorRole]

    is_htmx = request.headers.get("HX-Request") == "true"
    template = "people/_list_content.html" if is_htmx else "people/list.html"
    response = render(
        request,
        template,
        {
            "people": people,
            "counts": counts,
            "q": q or "",
            "active_roles": set(role or []),
            "active_countries": set(country or []),
            "country_options": country_options,
            "country_display": country_display,
            "roles": roles,
        },
        user=user,
    )
    if is_htmx:
        response.headers["HX-Push-Url"] = str(request.url)
    return response


@router.get("/search/json")
async def search_people(
    request: Request,
    q: str = "",
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    query = q.strip()
    results: list[Person] = []
    if query:
        like = f"%{query}%"
        results = session.exec(
            select(Person)
            .where(Person.name.ilike(like) | Person.sort_name.ilike(like))
            .order_by(Person.sort_name)
            .limit(10)
        ).all()

    return render(
        request,
        "people/_search_results.html",
        {"results": results, "query": query},
        user=user,
    )


@router.get("/orphaned")
async def orphaned_people(
    request: Request,
    ids: str = "",
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    """Visa personer som blev utan kopplingar efter not-borttagning.
    Användaren kan välja vilka som ska raderas. Säkerhetscheck: bara
    personer som faktiskt saknar PieceContributor visas."""
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    except ValueError:
        id_list = []

    if not id_list:
        return RedirectResponse("/pieces", status.HTTP_302_FOUND)

    people = session.exec(select(Person).where(Person.id.in_(id_list))).all()
    confirmed_orphans = []
    for p in people:
        still_linked = session.exec(
            select(PieceContributor.id)
            .where(PieceContributor.person_id == p.id)
            .limit(1)
        ).first()
        if not still_linked:
            confirmed_orphans.append(p)

    if not confirmed_orphans:
        return RedirectResponse("/pieces", status.HTTP_302_FOUND)

    return render(
        request,
        "people/orphaned.html",
        {"people": confirmed_orphans},
        user=user,
    )


@router.post("/orphaned/delete", dependencies=[Depends(verify_csrf)])
async def delete_orphaned_people(
    request: Request,
    person_id: list[int] = Form(default=[]),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    """Ta bort valda orphaned-personer efter dubbelkoll att de faktiskt
    saknar kopplingar (annars hoppa över - aldrig ta bort en kopplad)."""
    deleted = 0
    portraits_to_delete: list[str] = []
    for pid in person_id:
        p = session.get(Person, pid)
        if not p:
            continue
        still_linked = session.exec(
            select(PieceContributor.id)
            .where(PieceContributor.person_id == pid)
            .limit(1)
        ).first()
        if still_linked:
            continue
        if p.portrait_image_path:
            portraits_to_delete.append(p.portrait_image_path)
        # Plocka bort PersonLink-rader manuellt (ingen cascade definierad)
        for link in session.exec(
            select(PersonLink).where(PersonLink.person_id == pid)
        ).all():
            session.delete(link)
        session.delete(p)
        deleted += 1
    session.commit()

    for path in portraits_to_delete:
        delete_saved_image(path)

    if deleted:
        flash(request, f"Tog bort {deleted} {'person' if deleted == 1 else 'personer'}", "success")
    return RedirectResponse("/pieces", status.HTTP_302_FOUND)


@router.get("/{person_id}")
async def person_detail(
    request: Request,
    person_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    rows = session.exec(
        select(PieceContributor, Piece)
        .join(Piece, Piece.id == PieceContributor.piece_id)
        .where(PieceContributor.person_id == person_id)
        .order_by(PieceContributor.role, Piece.title)
    ).all()

    by_role: dict[ContributorRole, list[Piece]] = {}
    for pc, piece in rows:
        by_role.setdefault(pc.role, []).append(piece)

    links = session.exec(
        select(PersonLink)
        .where(PersonLink.person_id == person_id)
        .order_by(PersonLink.sort_order, PersonLink.id)
    ).all()

    from app.utils.countries import country_display

    return render(
        request,
        "people/detail.html",
        {
            "person": person,
            "pieces_by_role": by_role,
            "links": links,
            "country_label": country_display(person.country),
        },
        user=user,
    )


@router.get("/{person_id}/edit")
async def edit_person_form(
    request: Request,
    person_id: int,
    refresh: int = 0,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    links = session.exec(
        select(PersonLink)
        .where(PersonLink.person_id == person_id)
        .order_by(PersonLink.sort_order, PersonLink.id)
    ).all()
    from app.utils.countries import all_countries

    # refresh=1: hämta nya värden från MB+Wikipedia och bygg en preview-dict
    # som templaten kan rendera "MB: X"-pillar per fält bredvid existing.
    mb_preview = None
    if refresh and person.musicbrainz_artist_id:
        try:
            client = get_client()
            artist = await client.get_artist_with_urls(person.musicbrainz_artist_id)
        except Exception as exc:
            flash(request, f"MB-fel: {exc}", "danger")
            artist = None
        if artist:
            wiki_url = await get_wikipedia_url(artist)
            wiki_bio = await fetch_wikipedia_summary(wiki_url) if wiki_url else None
            life_span = artist.get("life-span") or artist.get("life_span") or {}
            from app.services.people import parse_partial_date

            by, bm, bd = parse_partial_date(life_span.get("begin") or "")
            dy, dm, dd = parse_partial_date(life_span.get("end") or "")
            # Samla bildkandidater från båda källor - användaren får välja
            image_candidates: list[dict] = []
            mb_image = extract_image_url(artist)
            wd = extract_wikidata_url(artist)
            wd_image = await resolve_image_via_wikidata(wd) if wd else None
            seen: set[str] = set()
            for source_url, origin in (
                (mb_image, "MusicBrainz"),
                (wd_image, "Wikidata P18"),
            ):
                if source_url and source_url not in seen:
                    seen.add(source_url)
                    image_candidates.append({
                        "source_url": source_url,
                        "thumb_url": commons_file_to_thumb_url(source_url, width=200) or "",
                        "origin": origin,
                    })
            # Behåll image_page_url för bakåtkompatibel preview/auto-set: prioritera MB
            image_page_url = mb_image or wd_image

            # Spara länkar och wikidata_id direkt vid refresh - de är
            # sekundära metadata som inte kräver per-fält-godkännande.
            # Bio, namn, datum osv visas däremot som pillar för selektiv apply.
            wd_id_now = wikidata_id_from_url(wd)
            if wd_id_now and not person.wikidata_id:
                person.wikidata_id = wd_id_now
                session.add(person)
            if wiki_url:
                _ensure_link(session, person_id, PersonLinkKind.WIKIPEDIA, wiki_url)
            if wd:
                _ensure_link(session, person_id, PersonLinkKind.WIKIDATA, wd)
            for kind_name, stream_url in extract_streaming_urls(artist).items():
                try:
                    _ensure_link(
                        session, person_id, PersonLinkKind(kind_name), stream_url
                    )
                except ValueError:
                    pass
            session.commit()
            # Ladda om links för att visa nya
            links = session.exec(
                select(PersonLink)
                .where(PersonLink.person_id == person_id)
                .order_by(PersonLink.sort_order, PersonLink.id)
            ).all()

            wd_already_linked = bool(wd)
            # Commons thumb-URL för att kunna förhandsvisa bilden inline
            image_thumb_url = None
            if image_page_url:
                image_thumb_url = commons_file_to_thumb_url(image_page_url, width=200)

            # Om personen saknar porträtt: ladda ner och sätt direkt så
            # användaren inte behöver klicka separat. Befintliga porträtt
            # bevaras och visas tillsammans med MB-förslaget för manuell
            # ersättning.
            if image_page_url and not person.portrait_image_path:
                full_thumb = commons_file_to_thumb_url(image_page_url, width=600)
                if full_thumb:
                    img_bytes = await download_image_bytes(full_thumb)
                    if img_bytes:
                        try:
                            person.portrait_image_path = save_uploaded_cover(img_bytes)
                            person.portrait_source_url = image_page_url
                            person.portrait_fetched_at = datetime.utcnow()
                            session.add(person)
                            session.commit()
                        except Exception as exc:
                            from loguru import logger as _log

                            _log.warning("Kunde inte spara porträtt: {}", exc)

            mb_preview = {
                "name": artist.get("name") or "",
                "sort_name": artist.get("sort-name") or artist.get("sort_name") or "",
                "birth_date": format_partial_date(by, bm, bd),
                "death_date": format_partial_date(dy, dm, dd),
                "country": artist.get("country") or "",
                "biography": wiki_bio or "",
                "biography_source_url": wiki_url or "",
                "wikipedia_url": wiki_url or "",
                "image_page_url": image_page_url or "",
                "image_thumb_url": image_thumb_url or "",
                "image_candidates": image_candidates,
                "wikidata_url": wd or "",
                "wd_already_linked": wd_already_linked,
            }
            flash(request, "Hämtade förslag från MusicBrainz/Wikipedia - klicka pillarna för att applicera", "info")

    return render(
        request,
        "people/edit.html",
        {
            "person": person,
            "links": links,
            "link_kinds": [k.value for k in PersonLinkKind],
            "birth_date_str": format_partial_date(
                person.birth_year, person.birth_month, person.birth_day
            ),
            "death_date_str": format_partial_date(
                person.death_year, person.death_month, person.death_day
            ),
            "countries": all_countries(),
            "mb_preview": mb_preview,
        },
        user=user,
    )


@router.post("/{person_id}/portrait", dependencies=[Depends(verify_csrf)])
async def upload_portrait(
    request: Request,
    person_id: int,
    image: UploadFile = File(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    content = await image.read()
    if not content:
        flash(request, "Tom fil", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)
    try:
        relative_path = save_uploaded_cover(content)
    except Exception:
        flash(request, "Kunde inte läsa bilden", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)

    if person.portrait_image_path:
        delete_saved_image(person.portrait_image_path)

    person.portrait_image_path = relative_path
    person.updated_at = datetime.utcnow()
    session.add(person)
    session.commit()
    flash(request, "Porträtt uppdaterat", "success")
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}/portrait/delete", dependencies=[Depends(verify_csrf)])
async def delete_portrait(
    request: Request,
    person_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    if person.portrait_image_path:
        delete_saved_image(person.portrait_image_path)
        person.portrait_image_path = None
        person.updated_at = datetime.utcnow()
        session.add(person)
        session.commit()
    flash(request, "Porträtt borttaget", "info")
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}/links", dependencies=[Depends(verify_csrf)])
async def add_link(
    request: Request,
    person_id: int,
    url: str = Form(...),
    kind: str = Form("other"),
    label: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    try:
        kind_enum = PersonLinkKind(kind)
    except ValueError:
        kind_enum = PersonLinkKind.OTHER
    session.add(
        PersonLink(
            person_id=person_id,
            url=url.strip(),
            kind=kind_enum,
            label=(label or "").strip() or None,
        )
    )
    session.commit()
    flash(request, "Länk tillagd", "success")
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}/links/{link_id}/update", dependencies=[Depends(verify_csrf)])
async def update_link(
    request: Request,
    person_id: int,
    link_id: int,
    url: str = Form(...),
    kind: str = Form(...),
    label: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    link = session.get(PersonLink, link_id)
    if not link or link.person_id != person_id:
        raise HTTPException(404)
    url = url.strip()
    if not url:
        flash(request, "URL krävs", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)
    try:
        kind_enum = PersonLinkKind(kind)
    except ValueError:
        kind_enum = PersonLinkKind.OTHER
    link.url = url
    link.kind = kind_enum
    link.label = (label or "").strip() or url
    session.add(link)
    session.commit()
    flash(request, "Länk uppdaterad", "success")
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}/links/{link_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_link(
    request: Request,
    person_id: int,
    link_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    link = session.get(PersonLink, link_id)
    if not link or link.person_id != person_id:
        raise HTTPException(404)
    session.delete(link)
    session.commit()
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}", dependencies=[Depends(verify_csrf)])
async def update_person(
    request: Request,
    person_id: int,
    name: str = Form(...),
    sort_name: str | None = Form(None),
    birth_date: str | None = Form(None),
    death_date: str | None = Form(None),
    country: str | None = Form(None),
    biography: str | None = Form(None),
    musicbrainz_artist_id: str | None = Form(None),
    wikidata_id: str | None = Form(None),
    mb_bio_applied: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    person.name = name.strip()
    person.sort_name = (sort_name or "").strip() or derive_sort_name(person.name)
    by, bm, bd = parse_partial_date(birth_date)
    person.birth_year, person.birth_month, person.birth_day = by, bm, bd
    dy, dm, dd = parse_partial_date(death_date)
    person.death_year, person.death_month, person.death_day = dy, dm, dd
    person.country = ((country or "").strip() or None)
    if person.country:
        person.country = person.country.upper()[:2]
    new_bio = (biography or "").strip() or None
    if new_bio != person.biography:
        person.biography = new_bio
        if mb_bio_applied == "1":
            # Biografi just applicerades från Wikipedia-förslaget
            person.biography_fetched_at = datetime.utcnow()
    person.musicbrainz_artist_id = (musicbrainz_artist_id or "").strip() or None
    person.wikidata_id = (wikidata_id or "").strip() or None
    person.updated_at = datetime.utcnow()

    session.add(person)
    session.commit()

    # Uppdatera contributors_cache på alla noter som denna person bidrar till
    contrib_links = session.exec(
        select(PieceContributor.piece_id).where(PieceContributor.person_id == person_id)
    ).all()
    for pid in set(contrib_links):
        contrib_rows = session.exec(
            select(PieceContributor, Person)
            .join(Person, Person.id == PieceContributor.person_id)
            .where(PieceContributor.piece_id == pid)
            .order_by(PieceContributor.role, PieceContributor.sort_order)
        ).all()
        cache = "; ".join(f"{p.name} ({pc.role})" for pc, p in contrib_rows)
        piece = session.get(Piece, pid)
        if piece:
            piece.contributors_cache = cache or None
            session.add(piece)
    session.commit()

    flash(request, f"Uppdaterade {person.name}", "success")
    return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)


@router.get("/{person_id}/identity-search")
async def person_identity_search(
    request: Request,
    person_id: int,
    q_name: str | None = None,
    skip_search: int = 0,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Gemensamt sökflöde över både MusicBrainz och Wikidata. Returnerar en
    HTMX-modal med två kolumner side-by-side så användaren kan jämföra träffar
    och välja vilken källa som ska appliceras."""
    import asyncio

    from app.services.wikidata import search_persons

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    search_name = (q_name if q_name is not None else person.name).strip()
    mb_results: list[dict] = []
    wd_results: list[dict] = []
    mb_error: str | None = None
    wd_error: str | None = None

    if not skip_search and search_name:
        from app.services.wikidata import link_mb_wd_candidates

        async def _mb():
            try:
                return await get_client().search_artist(search_name), None
            except Exception as exc:
                return [], str(exc)

        async def _wd():
            try:
                return await search_persons(search_name), None
            except Exception as exc:
                return [], str(exc)

        (mb_results, mb_error), (wd_results, wd_error) = await asyncio.gather(_mb(), _wd())
        link_mb_wd_candidates(mb_results, wd_results)

    return render(
        request,
        "people/_identity_modal.html",
        {
            "person": person,
            "mb_results": mb_results,
            "wd_results": wd_results,
            "mb_error": mb_error,
            "wd_error": wd_error,
            "search_name": search_name,
        },
        user=user,
    )


@router.post("/{person_id}/apply-wikidata", dependencies=[Depends(verify_csrf)])
async def apply_wikidata(
    request: Request,
    person_id: int,
    qid: str = Form(...),
    return_to: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Hämta Wikidata-entity och fyll i biografi, portrait, datum, MBID."""
    from app.services.musicbrainz import (
        commons_file_to_thumb_url,
        download_image_bytes,
        fetch_wikipedia_summary,
    )
    from app.services.wikidata import (
        country_iso_from_qid,
        extract_birth_date,
        extract_country_qid,
        extract_death_date,
        extract_image_filename,
        extract_musicbrainz_id,
        extract_wikipedia_url,
        get_entity,
    )
    from app.utils.images import save_uploaded_cover

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    entity = await get_entity(qid.strip())
    if not entity:
        flash(request, "Kunde inte hämta från Wikidata", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)

    person.wikidata_id = qid.strip()
    _ensure_link(
        session, person_id, PersonLinkKind.WIKIDATA,
        f"https://www.wikidata.org/wiki/{person.wikidata_id}",
    )

    # MBID via P434
    mbid = extract_musicbrainz_id(entity)
    if mbid and not person.musicbrainz_artist_id:
        person.musicbrainz_artist_id = mbid

    # Födelse-/dödsdatum (fyller bara i saknade fält - skriv inte över manuellt satta)
    by, bm, bd = extract_birth_date(entity)
    if by and not person.birth_year:
        person.birth_year = by
        person.birth_month = bm
        person.birth_day = bd
    dy, dm, dd = extract_death_date(entity)
    if dy and not person.death_year:
        person.death_year = dy
        person.death_month = dm
        person.death_day = dd

    # Land via P27 → P297 (ISO-kod)
    if not person.country:
        country_qid = extract_country_qid(entity)
        iso = await country_iso_from_qid(country_qid)
        if iso:
            person.country = iso

    # Wikipedia-länk + biografi
    wiki_url = extract_wikipedia_url(entity, "sv") or extract_wikipedia_url(entity, "en")
    if wiki_url:
        _ensure_link(session, person_id, PersonLinkKind.WIKIPEDIA, wiki_url)
        if not person.biography:
            bio = await fetch_wikipedia_summary(wiki_url)
            if bio:
                person.biography = bio
                person.biography_source_url = wiki_url
                person.biography_fetched_at = datetime.utcnow()

    # Portrait via P18 om saknas
    if not person.portrait_image_path:
        filename = extract_image_filename(entity)
        if filename:
            thumb_url = commons_file_to_thumb_url(filename, 800)
            data = await download_image_bytes(thumb_url)
            if data:
                rel = save_uploaded_cover(data, "person-portrait.jpg")
                if rel:
                    person.portrait_image_path = rel
                    person.portrait_source_url = thumb_url
                    person.portrait_fetched_at = datetime.utcnow()

    person.updated_at = datetime.utcnow()
    session.add(person)
    session.commit()

    flash(request, f"Tillämpade Wikidata-data ({qid})", "success")
    target = return_to if return_to and return_to.startswith("/") else f"/people/{person_id}/edit"
    return RedirectResponse(target, status.HTTP_302_FOUND)


def _ensure_link(
    session: Session, person_id: int, kind: PersonLinkKind, url: str
) -> None:
    """Skapa eller uppdatera PersonLink med given kind så att url matchar.
    Label uppdateras till url så användaren ser tydlig URL i listan."""
    existing = session.exec(
        select(PersonLink)
        .where(PersonLink.person_id == person_id)
        .where(PersonLink.kind == kind)
    ).first()
    if existing:
        existing.url = url
        existing.label = url
        session.add(existing)
    else:
        session.add(
            PersonLink(person_id=person_id, url=url, kind=kind, label=url)
        )


@router.post("/{person_id}/musicbrainz-id/clear", dependencies=[Depends(verify_csrf)])
async def clear_mbid(
    request: Request,
    person_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    person.musicbrainz_artist_id = None
    person.updated_at = datetime.utcnow()
    session.add(person)
    session.commit()
    flash(request, "Rensade MusicBrainz-koppling", "success")
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}/wikidata-id/clear", dependencies=[Depends(verify_csrf)])
async def clear_wikidata_id(
    request: Request,
    person_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    person.wikidata_id = None
    person.updated_at = datetime.utcnow()
    session.add(person)
    session.commit()
    flash(request, "Rensade Wikidata-id", "success")
    return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)


@router.post("/{person_id}/apply-mb-portrait", dependencies=[Depends(verify_csrf)])
async def apply_mb_portrait(
    request: Request,
    person_id: int,
    image_url: str = Form(...),
    return_to_refresh: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    thumb = commons_file_to_thumb_url(image_url, width=600)
    if not thumb:
        flash(request, "Kunde inte tolka bildens URL", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)
    img_bytes = await download_image_bytes(thumb)
    if not img_bytes:
        flash(request, "Kunde inte ladda ned bilden", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)
    try:
        new_path = save_uploaded_cover(img_bytes)
    except Exception as exc:
        flash(request, f"Kunde inte spara bilden: {exc}", "danger")
        return RedirectResponse(f"/people/{person_id}/edit", status.HTTP_302_FOUND)
    old_path = person.portrait_image_path
    person.portrait_image_path = new_path
    person.portrait_source_url = image_url
    person.portrait_fetched_at = datetime.utcnow()
    person.updated_at = datetime.utcnow()
    session.add(person)
    session.commit()
    if old_path and old_path != new_path:
        delete_saved_image(old_path)
    flash(request, "Hämtade nytt porträtt från MB/Wikidata", "success")
    target = f"/people/{person_id}/edit"
    if return_to_refresh:
        target += "?refresh=1"
    return RedirectResponse(target, status.HTTP_302_FOUND)


@router.post("/{person_id}/refresh", dependencies=[Depends(verify_csrf)])
async def refresh_person_mb(
    request: Request,
    person_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Hämta om biografi, porträtt och artistmetadata från MB+Wikipedia
    för en person som redan har MBID. Skriver över befintliga värden."""
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    if not person.musicbrainz_artist_id:
        flash(
            request,
            "Personen saknar MBID - använd 'Sök i MusicBrainz' för att koppla först",
            "warning",
        )
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)

    try:
        client = get_client()
        artist = await client.get_artist_with_urls(person.musicbrainz_artist_id)
    except Exception as exc:
        flash(request, f"MB-fel: {exc}", "danger")
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)
    if not artist:
        flash(request, "MusicBrainz returnerade inget för MBID", "warning")
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)

    wiki_url = await get_wikipedia_url(artist)
    wiki_bio = await fetch_wikipedia_summary(wiki_url) if wiki_url else None

    # Rensa gamla wikipedia-länkar som inte längre stämmer
    for link in session.exec(
        select(PersonLink)
        .where(PersonLink.person_id == person_id)
        .where(PersonLink.kind == PersonLinkKind.WIKIPEDIA)
    ).all():
        if link.url != wiki_url:
            session.delete(link)
    session.flush()

    # Skriv över bio
    if wiki_bio:
        person.biography = wiki_bio
        person.biography_source_url = wiki_url
        person.biography_fetched_at = datetime.utcnow()

    # Skriv över porträtt om MB eller Wikidata har bild
    image_page_url = extract_image_url(artist)
    if not image_page_url:
        wd_url_for_image = extract_wikidata_url(artist)
        if wd_url_for_image:
            image_page_url = await resolve_image_via_wikidata(wd_url_for_image)
    if image_page_url:
        thumb_url = commons_file_to_thumb_url(image_page_url, width=600)
        if thumb_url:
            img_bytes = await download_image_bytes(thumb_url)
            if img_bytes:
                try:
                    new_path = save_uploaded_cover(img_bytes)
                    old_path = person.portrait_image_path
                    person.portrait_image_path = new_path
                    person.portrait_source_url = image_page_url
                    person.portrait_fetched_at = datetime.utcnow()
                    if old_path and old_path != new_path:
                        delete_saved_image(old_path)
                except Exception as exc:
                    from loguru import logger as _log

                    _log.warning("Kunde inte spara porträtt vid refresh: {}", exc)

    # Skriv över namn, sort_name, levnadsår, land
    if artist.get("name"):
        person.name = artist["name"]
    sort_name = artist.get("sort-name") or artist.get("sort_name")
    if sort_name:
        person.sort_name = sort_name
    life_span = artist.get("life-span") or artist.get("life_span") or {}
    from app.services.people import parse_partial_date

    by, bm, bd = parse_partial_date(life_span.get("begin") or "")
    person.birth_year = by
    person.birth_month = bm
    person.birth_day = bd
    dy, dm, dd = parse_partial_date(life_span.get("end") or "")
    person.death_year = dy
    person.death_month = dm
    person.death_day = dd
    if artist.get("country"):
        person.country = artist["country"]
    person.updated_at = datetime.utcnow()
    session.add(person)

    # Säkerställ externa länkar
    if wiki_url:
        _ensure_link(session, person_id, PersonLinkKind.WIKIPEDIA, wiki_url)
    wd_url = extract_wikidata_url(artist)
    wd_id = wikidata_id_from_url(wd_url)
    if wd_id:
        person.wikidata_id = wd_id
        session.add(person)
    if wd_url:
        _ensure_link(session, person_id, PersonLinkKind.WIKIDATA, wd_url)
    streaming = extract_streaming_urls(artist)
    for kind_name, stream_url in streaming.items():
        try:
            _ensure_link(
                session, person_id, PersonLinkKind(kind_name), stream_url
            )
        except ValueError:
            pass
    session.commit()
    flash(request, f"Hämtade om data för {person.name} från MusicBrainz", "success")
    return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)


@router.post("/{person_id}/apply-musicbrainz", dependencies=[Depends(verify_csrf)])
async def apply_person_mb(
    request: Request,
    person_id: int,
    mbid: str = Form(...),
    return_to: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    try:
        client = get_client()
        artist = await client.get_artist_with_urls(mbid)
    except Exception as exc:
        flash(request, f"Kunde inte hämta från MusicBrainz: {exc}", "danger")
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)

    if not artist:
        flash(request, "MusicBrainz returnerade inget för MBID", "warning")
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)

    # Skriv över namn och MBID även om de redan är ifyllda (användaren bekräftade)
    if artist.get("name"):
        person.name = artist["name"]
    person.musicbrainz_artist_id = artist["id"]
    wd_id_from_apply = wikidata_id_from_url(extract_wikidata_url(artist))
    if wd_id_from_apply:
        person.wikidata_id = wd_id_from_apply

    wiki_url = await get_wikipedia_url(artist)
    wiki_bio = await fetch_wikipedia_summary(wiki_url) if wiki_url else None

    # Ladda ned porträtt - prioritera MB:s image-rel, fallback till Wikidata P18
    if not person.portrait_image_path:
        candidates: list[str] = []
        mb_image = extract_image_url(artist)
        if mb_image:
            candidates.append(mb_image)
        wd_url_for_image = extract_wikidata_url(artist)
        if wd_url_for_image:
            wd_image = await resolve_image_via_wikidata(wd_url_for_image)
            if wd_image and wd_image not in candidates:
                candidates.append(wd_image)
        for image_page_url in candidates:
            thumb_url = commons_file_to_thumb_url(image_page_url, width=600)
            if not thumb_url:
                continue
            img_bytes = await download_image_bytes(thumb_url)
            if not img_bytes:
                continue
            try:
                rel_path = save_uploaded_cover(img_bytes)
                person.portrait_image_path = rel_path
                person.portrait_source_url = image_page_url
                person.portrait_fetched_at = datetime.utcnow()
                break
            except Exception as exc:
                from loguru import logger as _log

                _log.warning("Kunde inte spara porträtt: {}", exc)

    enrich_person_from_mb(
        session,
        person,
        mb_artist=artist,
        wikipedia_url=wiki_url,
        biography=wiki_bio,
    )
    if wiki_url:
        _ensure_link(session, person.id, PersonLinkKind.WIKIPEDIA, wiki_url)
    wd_url_apply = extract_wikidata_url(artist)
    if wd_url_apply:
        _ensure_link(session, person.id, PersonLinkKind.WIKIDATA, wd_url_apply)
    streaming_apply = extract_streaming_urls(artist)
    for kind_name, stream_url in streaming_apply.items():
        try:
            _ensure_link(session, person.id, PersonLinkKind(kind_name), stream_url)
        except ValueError:
            pass
    session.commit()

    flash(request, f"MusicBrainz-data applicerad på {person.name}", "success")
    target = return_to.strip() if return_to else f"/people/{person_id}"
    # Säkerhet: tillåt bara interna sökvägar
    if not target.startswith("/"):
        target = f"/people/{person_id}"
    return RedirectResponse(target, status.HTTP_302_FOUND)


@router.post("/{person_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_person(
    request: Request,
    person_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    in_use = session.exec(
        select(PieceContributor).where(PieceContributor.person_id == person_id).limit(1)
    ).first()
    if in_use:
        flash(request, f"{person.name} är kopplad till noter och kan inte raderas", "danger")
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)

    session.delete(person)
    session.commit()
    flash(request, f"Raderade {person.name}", "success")
    return RedirectResponse("/people", status.HTTP_302_FOUND)
