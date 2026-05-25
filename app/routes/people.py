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
    fetch_wikipedia_summary,
    get_client,
    get_wikipedia_url,
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
    has_mbid: str | None = None,
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
    if has_mbid == "yes":
        stmt = stmt.where(Person.musicbrainz_artist_id.is_not(None))
    elif has_mbid == "no":
        stmt = stmt.where(Person.musicbrainz_artist_id.is_(None))
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

    return render(
        request,
        "people/list.html",
        {
            "people": people,
            "counts": counts,
            "q": q or "",
            "active_roles": set(role or []),
            "active_countries": set(country or []),
            "active_has_mbid": has_mbid or "",
            "country_options": country_options,
            "country_display": country_display,
            "roles": roles,
        },
        user=user,
    )


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
    person.biography = (biography or "").strip() or None
    person.musicbrainz_artist_id = (musicbrainz_artist_id or "").strip() or None
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


@router.get("/{person_id}/musicbrainz")
async def person_mb_modal(
    request: Request,
    person_id: int,
    q_name: str | None = None,
    skip_search: int = 0,
    return_to: str | None = None,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    search_name = (q_name if q_name is not None else person.name).strip()

    results = []
    error = None
    searched = False
    if not skip_search and search_name:
        searched = True
        try:
            client = get_client()
            results = await client.search_artist(search_name)
        except Exception as exc:
            error = str(exc)

    return render(
        request,
        "people/_musicbrainz_modal.html",
        {
            "person": person,
            "results": results,
            "error": error,
            "search_name": search_name,
            "searched": searched,
            "return_to": return_to or "",
        },
        user=user,
    )


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

    wiki_url = await get_wikipedia_url(artist)
    wiki_bio = await fetch_wikipedia_summary(wiki_url) if wiki_url else None

    # Ladda ned porträtt från MB:s image-relation (oftast Commons-fil)
    if not person.portrait_image_path:
        image_page_url = extract_image_url(artist)
        if image_page_url:
            thumb_url = commons_file_to_thumb_url(image_page_url, width=600)
            if thumb_url:
                img_bytes = await download_image_bytes(thumb_url)
                if img_bytes:
                    try:
                        rel_path = save_uploaded_cover(img_bytes)
                        person.portrait_image_path = rel_path
                        person.portrait_source_url = image_page_url
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
