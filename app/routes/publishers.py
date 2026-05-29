"""Routes för Publisher-entiteter - lista, detalj, redigera, radera."""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import Piece, Publisher, PublisherLink, User
from app.models.publisher import PublisherLinkKind
from app.templates_setup import flash, render
from app.utils.countries import all_countries

router = APIRouter(prefix="/publishers", tags=["publishers"])


@router.get("")
async def list_publishers(
    request: Request,
    q: str = "",
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    stmt = select(Publisher)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            (Publisher.name.ilike(like)) | (Publisher.sort_name.ilike(like))
        )
    pubs = list(session.exec(stmt.order_by(Publisher.sort_name)).all())
    # Räkna antal noter per publisher
    counts: dict[int, int] = {}
    for p in pubs:
        counts[p.id] = len(
            session.exec(select(Piece).where(Piece.publisher_id == p.id)).all()
        )
    # Pieces med fritext-publisher men inte kopplade - kandidater för bulk-match
    unmatched_count = len(
        session.exec(
            select(Piece)
            .where(Piece.publisher.is_not(None))
            .where(Piece.publisher_id.is_(None))
        ).all()
    )
    return render(
        request,
        "publishers/list.html",
        {
            "publishers": pubs,
            "counts": counts,
            "q": q,
            "unmatched_count": unmatched_count,
        },
        user=user,
    )


@router.get("/{publisher_id}/edit")
async def edit_publisher(
    request: Request,
    publisher_id: int,
    refresh: bool = False,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Helsidesvy för att redigera publisher. Likt /people/{id}/edit.
    Om ?refresh=1 och publishern är kopplad till MB - hämta aktuell
    data från MB + Wikipedia och visa som klickbara förslag."""
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    links = list(
        session.exec(
            select(PublisherLink)
            .where(PublisherLink.publisher_id == publisher_id)
            .order_by(PublisherLink.sort_order, PublisherLink.id)
        ).all()
    )
    mb_preview: dict | None = None
    if refresh and pub.musicbrainz_label_id:
        from app.services.musicbrainz import (
            extract_wikidata_url,
            fetch_wikipedia_summary,
            get_client,
            get_wikipedia_url,
        )

        try:
            client = get_client()
            label_data = await client.get_label_with_urls(pub.musicbrainz_label_id)
        except Exception:
            label_data = None
        if label_data:
            wiki_url = await get_wikipedia_url(label_data)
            wiki_summary = (
                await fetch_wikipedia_summary(wiki_url) if wiki_url else None
            )
            wd_url = extract_wikidata_url(label_data)
            existing_urls = {l.url for l in links}
            mb_preview = {
                "name": (label_data.get("name") or "").strip(),
                "sort_name": (label_data.get("sort-name") or "").strip(),
                "country": label_data.get("country") or "",
                "description": wiki_summary,
                "wikipedia_url": wiki_url,
                "wikidata_url": wd_url,
                "wd_already_linked": wd_url in existing_urls if wd_url else True,
            }
    return render(
        request,
        "publishers/edit.html",
        {
            "pub": pub,
            "links": links,
            "mb_preview": mb_preview,
            "countries": all_countries(),
            "link_kinds": [k.value for k in PublisherLinkKind],
        },
        user=user,
    )


@router.post(
    "/{publisher_id}/links/{link_id}/update", dependencies=[Depends(verify_csrf)]
)
async def update_link(
    request: Request,
    publisher_id: int,
    link_id: int,
    url: str = Form(...),
    kind: str = Form("other"),
    label: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    link = session.get(PublisherLink, link_id)
    if not link or link.publisher_id != publisher_id:
        raise HTTPException(404)
    link.url = url.strip() or link.url
    try:
        link.kind = PublisherLinkKind(kind)
    except ValueError:
        pass
    link.label = (label or "").strip() or None
    session.add(link)
    session.commit()
    flash(request, "Länk uppdaterad", "success")
    return RedirectResponse(
        f"/publishers/{publisher_id}/edit", status.HTTP_302_FOUND
    )


@router.get("/{publisher_id}")
async def publisher_detail(
    request: Request,
    publisher_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    pieces = list(
        session.exec(
            select(Piece)
            .where(Piece.publisher_id == publisher_id)
            .order_by(Piece.title)
        ).all()
    )
    # Andra publishers att eventuellt slå ihop med
    other_pubs = list(
        session.exec(
            select(Publisher)
            .where(Publisher.id != publisher_id)
            .order_by(Publisher.sort_name)
        ).all()
    )
    links = list(
        session.exec(
            select(PublisherLink)
            .where(PublisherLink.publisher_id == publisher_id)
            .order_by(PublisherLink.sort_order, PublisherLink.id)
        ).all()
    )
    return render(
        request,
        "publishers/detail.html",
        {
            "pub": pub,
            "pieces": pieces,
            "other_pubs": other_pubs,
            "links": links,
        },
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_publisher(
    request: Request,
    name: str = Form(...),
    sort_name: str | None = Form(None),
    country: str | None = Form(None),
    website_url: str | None = Form(None),
    description: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    clean = name.strip()
    if not clean:
        flash(request, "Namnet får inte vara tomt", "danger")
        return RedirectResponse("/publishers", status.HTTP_302_FOUND)
    existing = session.exec(select(Publisher).where(Publisher.name == clean)).first()
    if existing:
        flash(request, f'Förlag "{clean}" finns redan', "warning")
        return RedirectResponse(f"/publishers/{existing.id}", status.HTTP_302_FOUND)
    pub = Publisher(
        name=clean,
        sort_name=(sort_name or "").strip() or clean,
        country=(country or "").strip() or None,
        website_url=(website_url or "").strip() or None,
        description=(description or "").strip() or None,
    )
    session.add(pub)
    session.commit()
    session.refresh(pub)
    flash(request, f'Förlag "{clean}" skapad', "success")
    return RedirectResponse(f"/publishers/{pub.id}", status.HTTP_302_FOUND)


@router.post("/{publisher_id}/update", dependencies=[Depends(verify_csrf)])
async def update_publisher(
    request: Request,
    publisher_id: int,
    name: str = Form(...),
    sort_name: str | None = Form(None),
    country: str | None = Form(None),
    website_url: str | None = Form(None),
    description: str | None = Form(None),
    musicbrainz_label_id: str | None = Form(None),
    wikidata_id: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    clean = name.strip()
    if not clean:
        flash(request, "Namnet får inte vara tomt", "danger")
        return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)
    if clean != pub.name:
        clash = session.exec(
            select(Publisher)
            .where(Publisher.name == clean)
            .where(Publisher.id != publisher_id)
        ).first()
        if clash:
            flash(request, f'Ett annat förlag heter redan "{clean}"', "warning")
            return RedirectResponse(
                f"/publishers/{publisher_id}", status.HTTP_302_FOUND
            )
    pub.name = clean
    pub.sort_name = (sort_name or "").strip() or clean
    pub.country = (country or "").strip() or None
    pub.website_url = (website_url or "").strip() or None
    pub.description = (description or "").strip() or None
    pub.musicbrainz_label_id = (musicbrainz_label_id or "").strip() or None
    pub.wikidata_id = (wikidata_id or "").strip() or None
    pub.updated_at = datetime.utcnow()
    session.add(pub)
    session.commit()
    flash(request, "Förlag uppdaterat", "success")
    return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)


@router.post("/{source_id}/merge", dependencies=[Depends(verify_csrf)])
async def merge_publishers(
    request: Request,
    source_id: int,
    target_id: int = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Slå ihop två publishers. source raderas, alla pieces flyttas till
    target. Target behåller sin metadata (mbid, beskrivning etc.) - bara
    pieces flyttas över."""
    if source_id == target_id:
        flash(request, "Kan inte slå ihop med sig själv", "warning")
        return RedirectResponse(f"/publishers/{source_id}", status.HTTP_302_FOUND)
    source = session.get(Publisher, source_id)
    target = session.get(Publisher, target_id)
    if not source or not target:
        raise HTTPException(404)
    pieces = list(
        session.exec(select(Piece).where(Piece.publisher_id == source_id)).all()
    )
    for p in pieces:
        p.publisher_id = target_id
        session.add(p)
    source_name = source.name
    session.delete(source)
    session.commit()
    flash(
        request,
        f'Slog ihop "{source_name}" → "{target.name}" ({len(pieces)} not(er) flyttade)',
        "success",
    )
    return RedirectResponse(f"/publishers/{target_id}", status.HTTP_302_FOUND)


@router.post("/match-existing", dependencies=[Depends(verify_csrf)])
async def match_existing(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Bulk-koppla pieces.publisher (fritext) mot Publisher-entiteten.

    Loopar alla pieces som har publisher-text men inte publisher_id satt
    och kör find_or_create_publisher per stycke. Returnerar statistik
    om matchade vs nyskapade."""
    from app.services.publishers import find_or_create_publisher

    unmatched = list(
        session.exec(
            select(Piece)
            .where(Piece.publisher.is_not(None))
            .where(Piece.publisher_id.is_(None))
        ).all()
    )
    if not unmatched:
        flash(request, "Alla noter med förlag är redan kopplade", "info")
        return RedirectResponse("/publishers", status.HTTP_302_FOUND)

    existing_before = {p.id for p in session.exec(select(Publisher)).all()}
    pieces_updated = 0
    publishers_created = 0
    for piece in unmatched:
        pub = find_or_create_publisher(session, piece.publisher)
        if pub:
            piece.publisher_id = pub.id
            session.add(piece)
            pieces_updated += 1
            if pub.id not in existing_before:
                publishers_created += 1
                existing_before.add(pub.id)
    session.commit()

    parts = [f"{pieces_updated} not(er) kopplade"]
    if publishers_created:
        parts.append(f"{publishers_created} nytt/nya förlag skapade")
    flash(request, " · ".join(parts), "success")
    return RedirectResponse("/publishers", status.HTTP_302_FOUND)


@router.get("/{publisher_id}/musicbrainz")
async def search_mb_labels(
    request: Request,
    publisher_id: int,
    q: str = "",
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-modal som söker MusicBrainz Labels. q tomt = sök på publisher.name."""
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    query = (q or "").strip() or pub.name
    candidates: list[dict] = []
    try:
        from app.services.musicbrainz import get_client

        client = get_client()
        results = await client.search_label(query)
        for r in results[:8]:
            candidates.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "sort_name": r.get("sort-name"),
                "country": r.get("country"),
                "type": r.get("type"),
                "score": r.get("score"),
                "disambiguation": r.get("disambiguation"),
            })
    except Exception as exc:
        from loguru import logger

        logger.warning("MB label-sökning misslyckades: {}", exc)
    return render(
        request,
        "publishers/_mb_modal.html",
        {"pub": pub, "query": query, "candidates": candidates},
        user=user,
    )


@router.post("/{publisher_id}/apply-musicbrainz", dependencies=[Depends(verify_csrf)])
async def apply_musicbrainz(
    request: Request,
    publisher_id: int,
    mbid: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Hämta MB label-detaljer + Wikipedia-beskrivning, applicera på publisher."""
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    from app.services.musicbrainz import (
        fetch_wikipedia_summary,
        get_client,
        get_wikipedia_url,
    )
    from app.services.publishers import enrich_publisher_from_mb

    client = get_client()
    try:
        label_data = await client.get_label_with_urls(mbid.strip())
    except Exception as exc:
        from loguru import logger

        logger.warning("MB get_label misslyckades: {}", exc)
        label_data = None
    if not label_data:
        flash(request, "Kunde inte hämta data från MusicBrainz", "danger")
        return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)

    wiki_url = await get_wikipedia_url(label_data)
    wiki_summary = await fetch_wikipedia_summary(wiki_url) if wiki_url else None

    changes = enrich_publisher_from_mb(
        session, pub, mb_label=label_data, wikipedia_url=wiki_url,
        description=wiki_summary,
    )
    session.commit()
    msg_parts = [f'Kopplad till MusicBrainz: {label_data.get("name")}']
    if "old_name" in changes:
        msg_parts.append(f'Namn ändrat från "{changes["old_name"]}"')
    flash(request, " · ".join(msg_parts), "success")
    return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)


@router.post("/{publisher_id}/musicbrainz-id/clear", dependencies=[Depends(verify_csrf)])
async def clear_musicbrainz(
    request: Request,
    publisher_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    pub.musicbrainz_label_id = None
    pub.updated_at = datetime.utcnow()
    session.add(pub)
    session.commit()
    flash(request, "MusicBrainz-koppling rensad", "info")
    return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)


@router.post("/{publisher_id}/links/add", dependencies=[Depends(verify_csrf)])
async def add_link(
    request: Request,
    publisher_id: int,
    url: str = Form(...),
    kind: str = Form("other"),
    label: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    clean_url = url.strip()
    if not clean_url:
        flash(request, "Tom URL", "danger")
        return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)
    from app.models.publisher import PublisherLinkKind

    try:
        kind_enum = PublisherLinkKind(kind)
    except ValueError:
        kind_enum = PublisherLinkKind.OTHER
    session.add(PublisherLink(
        publisher_id=publisher_id,
        url=clean_url,
        kind=kind_enum,
        label=(label or "").strip() or None,
    ))
    session.commit()
    flash(request, "Länk tillagd", "success")
    return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)


@router.post(
    "/{publisher_id}/links/{link_id}/delete", dependencies=[Depends(verify_csrf)]
)
async def delete_link(
    request: Request,
    publisher_id: int,
    link_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    link = session.get(PublisherLink, link_id)
    if not link or link.publisher_id != publisher_id:
        raise HTTPException(404)
    session.delete(link)
    session.commit()
    flash(request, "Länk borttagen", "info")
    return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)


@router.post("/{publisher_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_publisher(
    request: Request,
    publisher_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    count = len(
        session.exec(select(Piece).where(Piece.publisher_id == publisher_id)).all()
    )
    if count > 0:
        flash(
            request,
            f'Kan inte radera "{pub.name}" - {count} not(er) refererar till förlaget',
            "warning",
        )
        return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)
    name = pub.name
    session.delete(pub)
    session.commit()
    flash(request, f'Förlag "{name}" raderat', "info")
    return RedirectResponse("/publishers", status.HTTP_302_FOUND)
