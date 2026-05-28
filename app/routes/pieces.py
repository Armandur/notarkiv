from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from datetime import datetime

from app.deps import (
    get_session,
    require_admin,
    require_auth,
    require_editor,
    require_kiosk_session,
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
from app import templates_setup
from app.templates_setup import flash, render
from app.utils.images import (
    delete_saved_image,
    rotate_saved_image,
    save_uploaded_cover,
    thumbnail_url_path,
)  # noqa: F401 - save_uploaded_cover används också för MB-portrait-import

router = APIRouter(prefix="/pieces", tags=["pieces"])

# Separat router för /p/{public_id} - landningssidan QR-koder pekar på.
public_router = APIRouter(tags=["pieces"])
# Separat router för kiosk-vyn - skannerinput → piece.
kiosk_router = APIRouter(prefix="/kiosk", tags=["pieces"])


@public_router.get("/p/{public_id}")
async def by_public_id(
    request: Request,
    public_id: str,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Slå upp piece via stabil UUID (från QR-kod) och redirecta till detalj."""
    piece = session.exec(select(Piece).where(Piece.public_id == public_id)).first()
    if not piece:
        raise HTTPException(404, "Hittade ingen not med den koden")

    # Kiosk-läge: om query-param ?kiosk=1, redirecta till kiosk-vy istället
    if request.query_params.get("kiosk"):
        return RedirectResponse(f"/kiosk/{public_id}", status.HTTP_302_FOUND)
    return RedirectResponse(f"/pieces/{piece.id}", status.HTTP_302_FOUND)


def _kiosk_borrower(request: Request, session: Session) -> User | None:
    """Den autentiserade låntagaren för aktuell kiosk-session (via PIN)."""
    bid = request.session.get("kiosk_borrower_id")
    if not bid:
        return None
    user = session.get(User, bid)
    return user


def _kiosk_context(request: Request, session: Session, kiosk) -> dict:
    """Hämta cart-batch + items + auth-state för kiosk-vyn.

    Cart är knuten till den autentiserade låntagaren (via PIN), inte till
    kiosken. Det gör att kioskdatorn kan delas av många användare
    utan att korgar blandas."""
    from app.models import Loan, LoanBatch, LoanBatchStatus
    from app.routes.loans import _enrich_loans, _get_or_create_cart, _unit_path

    borrower = _kiosk_borrower(request, session)
    cart = None
    cart_items: list = []
    active_solo: list = []
    active_batches: list = []

    kiosk_location = (
        session.get(StorageLocation, kiosk.location_id) if kiosk.location_id else None
    )
    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)

    def _annotate(items: list[dict]) -> None:
        for it in items:
            it["in_kiosk_location"] = (
                allowed_unit_ids is None
                or (it["unit"] and it["unit"].id in allowed_unit_ids)
            )
            it["path"] = _unit_path(session, it["unit"]) if it["unit"] else "Okänd plats"

    if borrower:
        cart = _get_or_create_cart(session, borrower.id)
        cart_loans = session.exec(
            select(Loan).where(Loan.batch_id == cart.id).order_by(Loan.id)
        ).all()
        cart_items = _enrich_loans(session, cart_loans)

        from sqlalchemy import or_

        # "Mina lån" = där jag är låntagare ELLER där jag registrerade utlånet.
        # Senare täcker externa lån som låntagaren själv registrerat via kiosken.
        solo_loans = session.exec(
            select(Loan)
            .where(
                or_(
                    Loan.borrower_user_id == borrower.id,
                    Loan.registered_by == borrower.id,
                )
            )
            .where(Loan.returned_at.is_(None))
            .where(Loan.batch_id.is_(None))
        ).all()
        active_solo = _enrich_loans(session, solo_loans)
        _annotate(active_solo)

        # Aktiva batches: jag är låntagare eller jag skapade den
        batches = session.exec(
            select(LoanBatch)
            .where(
                or_(
                    LoanBatch.borrower_user_id == borrower.id,
                    LoanBatch.created_by == borrower.id,
                )
            )
            .where(LoanBatch.status == LoanBatchStatus.ACTIVE)
            .order_by(LoanBatch.borrowed_at.desc())
        ).all()
        for batch in batches:
            batch_loans = session.exec(
                select(Loan)
                .where(Loan.batch_id == batch.id)
                .where(Loan.returned_at.is_(None))
            ).all()
            enriched = _enrich_loans(session, batch_loans)
            _annotate(enriched)
            here_count = sum(1 for it in enriched if it["in_kiosk_location"])
            active_batches.append(
                {
                    "batch": batch,
                    "entries": enriched,
                    "total_remaining": len(enriched),
                    "here_count": here_count,
                }
            )

    return {
        "borrower": borrower,
        "kiosk": kiosk,
        "kiosk_location": kiosk_location,
        "cart": cart,
        "cart_items": cart_items,
        "cart_total": len(cart_items),
        "active_solo": active_solo,
        "active_batches": active_batches,
    }


@kiosk_router.get("/activate")
async def kiosk_activate(
    request: Request,
    token: str = "",
    session: Session = Depends(get_session),
) -> Response:
    """Aktivera kioskenheten på denna webbläsare. Sätter session-cookie
    permanent och rensar ev. user-login - kioskdatorn ska inte vara
    inloggad som en person."""
    from app.models import Kiosk

    kiosk = session.exec(select(Kiosk).where(Kiosk.access_token == token.strip())).first() if token else None
    if not kiosk:
        return render(request, "pieces/kiosk_activate_failed.html", {}, user=None, status_code=403)

    request.session["kiosk_id"] = kiosk.id
    request.session.pop("kiosk_borrower_id", None)
    # Rensa INTE user_id - admin behöver kunna fortsätta vara inloggad
    # för att navigera tillbaka till t.ex. /admin/kiosks. På en delad
    # produktionskiosk loggar admin ut själv när hen är klar.
    kiosk.last_activity_at = datetime.utcnow()
    session.add(kiosk)
    session.commit()
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


@kiosk_router.post("/deactivate", dependencies=[Depends(verify_csrf)])
async def kiosk_deactivate(request: Request) -> Response:
    """Avaktivera kiosk-läget. Sessionen återgår till en vanlig (utloggad)."""
    request.session.pop("kiosk_id", None)
    request.session.pop("kiosk_borrower_id", None)
    return RedirectResponse("/login", status.HTTP_302_FOUND)


@kiosk_router.get("")
async def kiosk_input(
    request: Request,
    kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """Kioskstartvy: pinkod-input om ej autentiserad, annars scanner+cart."""
    ctx = _kiosk_context(request, session, kiosk)
    return render(request, "pieces/kiosk.html", ctx, user=None)


@kiosk_router.post("/auth", dependencies=[Depends(verify_csrf)])
async def kiosk_auth(
    request: Request,
    username: str = Form(...),
    pin: str = Form(...),
    kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """Autentisera en låntagare via användarnamn + PIN. Sätter session-
    state så efterföljande kiosk-anrop kopplas till den användaren.
    Rate-limit: max 5 fel/15 min per IP, sen 5 min lockout."""
    from app.auth import verify_pin
    from app.utils.ratelimit import check_kiosk_attempts, record_kiosk_failure, reset_kiosk_attempts

    ip = request.client.host if request.client else "unknown"
    if not check_kiosk_attempts(ip):
        flash(request, "För många misslyckade försök - vänta några minuter", "danger")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    clean_pin = pin.strip()
    target = session.exec(select(User).where(User.username == username.strip())).first()
    if not target or not target.pin_hash or not verify_pin(clean_pin, target.pin_hash):
        record_kiosk_failure(ip)
        flash(request, "Fel användarnamn eller PIN", "danger")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    reset_kiosk_attempts(ip)
    request.session["kiosk_borrower_id"] = target.id
    flash(request, f"Inloggad som {target.username}", "success")
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


@kiosk_router.post("/qr-auth", dependencies=[Depends(verify_csrf)])
async def kiosk_qr_auth(
    request: Request,
    token: str = Form(...),
    kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """Autentisera en låntagare via QR-token från deras profil. Token
    skickas utan "u:"-prefixet (JS strippar det). Rate-limit gäller."""
    from app.utils.ratelimit import check_kiosk_attempts, record_kiosk_failure, reset_kiosk_attempts

    ip = request.client.host if request.client else "unknown"
    if not check_kiosk_attempts(ip):
        flash(request, "För många misslyckade försök - vänta några minuter", "danger")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    clean = token.strip()
    target = (
        session.exec(select(User).where(User.kiosk_token == clean)).first()
        if clean
        else None
    )
    if not target:
        record_kiosk_failure(ip)
        flash(request, "Ogiltig QR-kod", "danger")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    reset_kiosk_attempts(ip)
    request.session["kiosk_borrower_id"] = target.id
    flash(request, f"Inloggad som {target.username}", "success")
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


@kiosk_router.post("/logout", dependencies=[Depends(verify_csrf)])
async def kiosk_logout(
    request: Request,
    kiosk = Depends(require_kiosk_session),
) -> Response:
    """Logga ut låntagaren från kiosken (kiosk-sessionen påverkas inte)."""
    request.session.pop("kiosk_borrower_id", None)
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


@kiosk_router.post("/checkout", dependencies=[Depends(verify_csrf)])
async def kiosk_checkout(
    request: Request,
    name: str | None = Form(None),
    expected_return: str | None = Form(None),
    external_name: str | None = Form(None),
    kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """Snabb-checkout för kiosken: hoppar pickup-fasen eftersom alla noter
    redan är fysiskt i handen. Auto-loggar ut låntagaren efter lyckad
    registrering så nästa person måste autentisera sig igen.

    external_name: om satt sparas det som borrower_name istället för den
    autentiserade användarens namn. Den autentiserade användaren är
    fortfarande ansvarig (registered_by)."""
    from app.models import Loan, LoanBatchStatus
    from app.routes.loans import _get_or_create_cart, _parse_date

    borrower = _kiosk_borrower(request, session)
    if not borrower:
        flash(request, "Ingen låntagare inloggad - logga in med PIN först", "danger")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    cart = _get_or_create_cart(session, borrower.id)
    loans = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    if not loans:
        flash(request, "Korgen är tom", "warning")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    # Avgör vem som faktiskt är låntagare. registered_by/created_by är alltid
    # den autentiserade användaren (ansvarig). external_name skickar utlånet
    # till extern person men ansvaret förblir.
    ext = (external_name or "").strip()
    if ext:
        b_user_id: int | None = None
        b_name = ext
    else:
        b_user_id = borrower.id
        b_name = borrower.username

    now = datetime.utcnow()
    expected = _parse_date(expected_return)

    # Endast 1 not i korgen → registrera som fristående lån (utan batch).
    # En batch är onödig overhead för ett enskilt lån och förorenar
    # batch-listor med "Kiosk-utlån YYYY-MM-DD HH:MM"-poster.
    if len(loans) == 1:
        loan = loans[0]
        loan.borrower_name = b_name
        loan.borrower_user_id = b_user_id
        loan.expected_return_at = expected
        loan.borrowed_at = now
        loan.picked_up_at = now
        loan.batch_id = None  # frigör från cart-batchen
        session.add(loan)
        # Behåll cart-batchen som tom CART så nästa checkout återanvänder den
        session.commit()
        # Behåll PIN-autentiseringen - användaren kan fortsätta skanna eller
        # logga ut själv via knappen i headern.
        flash(
            request,
            f"Registrerade enskilt utlån till {b_name}",
            "success",
        )
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    # Annars: vanlig batch
    clean_name = (name or "").strip() or f"Kiosk-utlån {now.strftime('%Y-%m-%d %H:%M')}"
    cart.name = clean_name
    cart.borrower_user_id = b_user_id
    cart.borrower_name = b_name
    cart.expected_return_at = expected
    cart.status = LoanBatchStatus.ACTIVE
    cart.borrowed_at = now
    cart.registered_at = now
    cart.activated_at = now
    session.add(cart)

    for loan in loans:
        loan.borrower_name = b_name
        loan.borrower_user_id = b_user_id
        loan.expected_return_at = cart.expected_return_at
        loan.borrowed_at = now
        loan.picked_up_at = now
        session.add(loan)

    session.commit()
    # Behåll PIN-autentiseringen - låntagaren kan fortsätta skanna eller
    # logga ut själv via knappen i headern.
    flash(
        request,
        f'Registrerade "{clean_name}" - {len(loans)} noter utlånade till {b_name}',
        "success",
    )
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


def _kiosk_location_unit_ids(session: Session, location_id: int | None) -> set[int] | None:
    """Hämta alla unit-IDn för en lagringsplats (inklusive nästlade barn).
    Returnerar None om location_id är None - det signalerar 'ingen filter'."""
    if not location_id:
        return None
    units = session.exec(
        select(StorageUnit).where(StorageUnit.location_id == location_id)
    ).all()
    return {u.id for u in units}


@kiosk_router.get("/search")
async def kiosk_search(
    request: Request,
    q: str = "",
    kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-sök för noter utan QR-kod. Returnerar HTML-fragment med
    träffar. Filtrerar till kioskens lagringsplats om satt."""
    q = q.strip()
    if len(q) < 2:
        return Response("", media_type="text/html")

    # SQLite ILIKE är case-insensitive bara på ASCII. För att matcha
    # ÅÄÖ filtrerar vi Python-side (snabbt för 200-1000 noter).
    q_lower = q.lower()
    all_pieces = session.exec(select(Piece).order_by(Piece.title)).all()
    pieces = [
        p for p in all_pieces
        if q_lower in (p.title or "").lower()
        or q_lower in (p.contributors_cache or "").lower()
    ][:15]

    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)
    kiosk_location_obj = (
        session.get(StorageLocation, kiosk.location_id) if kiosk.location_id else None
    )

    # Hämta placeringar för filtrering + display
    placements_by_piece: dict[int, list[PiecePlacement]] = {}
    if pieces:
        all_placements = session.exec(
            select(PiecePlacement).where(
                PiecePlacement.piece_id.in_([p.id for p in pieces])
            )
        ).all()
        for pl in all_placements:
            placements_by_piece.setdefault(pl.piece_id, []).append(pl)

    # Ladda alla units och locations en gång så path-byggande är trivialt
    all_units = {u.id: u for u in session.exec(select(StorageUnit)).all()}
    locations = {l.id: l for l in session.exec(select(StorageLocation)).all()}

    def _path(unit_id: int) -> str:
        u = all_units.get(unit_id)
        if not u:
            return "Okänd plats"
        parts = [u.name]
        cur = u
        while cur.parent_id:
            parent = all_units.get(cur.parent_id)
            if not parent:
                break
            parts.append(parent.name)
            cur = parent
        loc = locations.get(u.location_id)
        if loc:
            parts.append(loc.name)
        return " › ".join(reversed(parts))

    rows = []
    for p in pieces:
        piece_placements = placements_by_piece.get(p.id, [])
        if allowed_unit_ids is not None:
            here = [pl for pl in piece_placements if pl.storage_unit_id in allowed_unit_ids]
            elsewhere = [pl for pl in piece_placements if pl.storage_unit_id not in allowed_unit_ids]
            on_location = bool(here)
        else:
            here = piece_placements
            elsewhere = []
            on_location = True
        rows.append(
            {
                "piece": p,
                "paths_here": [_path(pl.storage_unit_id) for pl in here],
                "paths_elsewhere": [_path(pl.storage_unit_id) for pl in elsewhere],
                "on_location": on_location,
            }
        )
    # Sortera: noter på platsen först, andra noter sedan
    rows.sort(key=lambda r: (not r["on_location"], r["piece"].title))

    return render(
        request,
        "pieces/_kiosk_search_results.html",
        {"rows": rows, "q": q, "kiosk_location": kiosk_location_obj},
        user=None,
    )


@kiosk_router.get("/{public_id}")
async def kiosk_piece(
    request: Request,
    public_id: str,
    kiosk = Depends(require_kiosk_session),
    session: Session = Depends(get_session),
) -> Response:
    """Förenklad piece-vy i kiosk-läge med stora Låna/Återlämna-knappar.
    Kräver att en låntagare är autentiserad via PIN - annars redirect
    tillbaka till /kiosk för auth.

    Om kiosken är knuten till en lagringsplats filtreras placeringarna -
    bara de inom platsen kan lånas härifrån."""
    borrower = _kiosk_borrower(request, session)
    if not borrower:
        flash(request, "Logga in med PIN för att låna", "warning")
        return RedirectResponse("/kiosk", status.HTTP_302_FOUND)

    piece = session.exec(select(Piece).where(Piece.public_id == public_id)).first()
    if not piece:
        raise HTTPException(404)

    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)
    kiosk_location = (
        session.get(StorageLocation, kiosk.location_id) if kiosk.location_id else None
    )

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece.id)
    ).all()
    units = {
        u.id: u for u in session.exec(
            select(StorageUnit).where(
                StorageUnit.id.in_([pl.storage_unit_id for pl in placements])
            )
        ).all()
    } if placements else {}

    # Aktiva lån grupperade per placering
    active_loans = session.exec(
        select(Loan)
        .where(Loan.placement_id.in_([pl.id for pl in placements]))
        .where(Loan.returned_at.is_(None))
    ).all() if placements else []
    loans_by_placement: dict[int, list[Loan]] = {}
    for la in active_loans:
        loans_by_placement.setdefault(la.placement_id, []).append(la)

    # Sammanställning: totalt antal aktiva utlån för noten (alla placeringar)
    total_active_out = sum(la.copies for la in active_loans)
    distinct_borrowers = []
    seen_borrowers: set[str] = set()
    for la in active_loans:
        key = la.borrower_name or ""
        if key and key not in seen_borrowers:
            distinct_borrowers.append({"name": la.borrower_name, "since": la.borrowed_at})
            seen_borrowers.add(key)

    rows = []
    here_count = 0
    for pl in placements:
        unit = units.get(pl.storage_unit_id)
        out = sum(la.copies for la in loans_by_placement.get(pl.id, []))
        in_kiosk_location = (
            allowed_unit_ids is None or pl.storage_unit_id in allowed_unit_ids
        )
        if in_kiosk_location:
            here_count += 1
        rows.append(
            {
                "placement": pl,
                "unit": unit,
                "home": (pl.copies or 0) - out if pl.copies else None,
                "out_count": out,
                "loans": loans_by_placement.get(pl.id, []),
                "in_kiosk_location": in_kiosk_location,
            }
        )

    # Mer info för kiosk-vyn så användaren slipper en separat detaljvy
    images = list(
        session.exec(
            select(PieceImage)
            .where(PieceImage.piece_id == piece.id)
            .order_by(PieceImage.sort_order, PieceImage.id)
        ).all()
    )
    contributors = collect_contributors(session, piece.id)
    tag_rows = list(
        session.exec(
            select(Tag)
            .join(PieceTag, PieceTag.tag_id == Tag.id)
            .where(PieceTag.piece_id == piece.id)
            .order_by(Tag.kind, Tag.name)
        ).all()
    )
    psalm_refs = list(
        session.exec(
            select(PiecePsalmRef, PsalmBook)
            .join(PsalmBook, PsalmBook.id == PiecePsalmRef.book_id)
            .where(PiecePsalmRef.piece_id == piece.id)
            .order_by(PsalmBook.sort_order, PsalmBook.name, PiecePsalmRef.number)
        ).all()
    )

    # Låntagarens aktiva batches - för "+ Lägg till i pågående lån"-knappen.
    # Samma regel som i Mina lån: jag är låntagare eller jag skapade utlånet.
    from sqlalchemy import or_

    from app.models import LoanBatch, LoanBatchStatus

    active_batches = list(
        session.exec(
            select(LoanBatch)
            .where(
                or_(
                    LoanBatch.borrower_user_id == borrower.id,
                    LoanBatch.created_by == borrower.id,
                )
            )
            .where(LoanBatch.status == LoanBatchStatus.ACTIVE)
            .order_by(LoanBatch.borrowed_at.desc())
        ).all()
    )

    # Kolla om låntagaren redan har den här noten i något av sina aktiva
    # utlån (oavsett batch eller solo). Banner hjälper undvika oavsiktlig
    # dubbel-låning. Grupperar per batch (eller "Enskilt lån" om solo).
    already_in_loans: list[dict] = []
    if placements:
        my_loans = session.exec(
            select(Loan)
            .where(Loan.placement_id.in_([pl.id for pl in placements]))
            .where(Loan.returned_at.is_(None))
            .where(
                or_(
                    Loan.borrower_user_id == borrower.id,
                    Loan.registered_by == borrower.id,
                )
            )
        ).all()
        batch_ids_in_loans = {la.batch_id for la in my_loans if la.batch_id}
        batches_by_id = {
            b.id: b for b in session.exec(
                select(LoanBatch).where(LoanBatch.id.in_(list(batch_ids_in_loans)))
            ).all()
        } if batch_ids_in_loans else {}
        # Gruppera per batch (None = solo)
        grouped: dict[int | None, int] = {}
        for la in my_loans:
            grouped[la.batch_id] = grouped.get(la.batch_id, 0) + la.copies
        for batch_id, copies in grouped.items():
            if batch_id and batch_id in batches_by_id:
                already_in_loans.append(
                    {"label": batches_by_id[batch_id].name, "copies": copies, "batch_id": batch_id}
                )
            elif batch_id is None:
                already_in_loans.append(
                    {"label": "Enskilt lån", "copies": copies, "batch_id": None}
                )

    return render(
        request,
        "pieces/kiosk_piece.html",
        {
            "piece": piece,
            "placements": rows,
            "borrower": borrower,
            "kiosk": kiosk,
            "kiosk_location": kiosk_location,
            "not_at_kiosk_location": kiosk_location is not None and here_count == 0,
            "images": images,
            "contributors": contributors,
            "tags": tag_rows,
            "psalm_refs": psalm_refs,
            "active_batches": active_batches,
            "already_in_loans": already_in_loans,
            "total_active_out": total_active_out,
            "distinct_borrowers": distinct_borrowers,
            "composer_role": ContributorRole.COMPOSER,
            "arranger_role": ContributorRole.ARRANGER,
            "lyricist_role": ContributorRole.LYRICIST,
        },
        user=None,
    )


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


@router.get("/print.pdf")
async def print_pdf(
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
        stmt = _apply_filters(stmt, session, tag, voicing, language, unit, include_subunits)
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

    html_str = templates_setup.templates.get_template("pieces/pdf.html").render(
        request=request,
        pieces=pieces,
        placements_by_piece=placement_views,
        voicings_by_piece=voicings_by_piece,
        language_name_sv=templates_setup.templates.env.globals["language_name_sv"],
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        q=q or "",
        active_tags=tag or [],
        active_voicings=voicing or [],
        active_languages=language or [],
    )
    pdf_bytes = HTML(string=html_str).write_pdf()
    filename = f"notarkiv-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
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
        from app.models import TagAlias

        # Matcha tagg-namn OR tagg-alias
        tag_ids = set(session.exec(select(Tag.id).where(Tag.name.in_(tags))).all())
        tag_ids.update(
            session.exec(select(TagAlias.tag_id).where(TagAlias.name.in_(tags))).all()
        )
        tag_ids = list(tag_ids)
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
        from app.models import TagAlias
        tag_ids = set(session.exec(select(Tag.id).where(Tag.name.in_(tag))).all())
        tag_ids.update(
            session.exec(select(TagAlias.tag_id).where(TagAlias.name.in_(tag))).all()
        )
        if tag_ids:
            piece_ids = list(
                session.exec(
                    select(PieceTag.piece_id)
                    .where(PieceTag.tag_id.in_(list(tag_ids)))
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
    from datetime import datetime
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
        from app.models import TagAlias
        tag_ids = set(session.exec(select(Tag.id).where(Tag.name.in_(tag))).all())
        tag_ids.update(
            session.exec(select(TagAlias.tag_id).where(TagAlias.name.in_(tag))).all()
        )
        if tag_ids:
            piece_ids = list(
                session.exec(
                    select(PieceTag.piece_id)
                    .where(PieceTag.tag_id.in_(list(tag_ids)))
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
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
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
                "path": " > ".join(reversed(parts)),
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


def _find_psalm_title_matches(
    session: Session, title: str
) -> list[dict]:
    """Hitta PsalmEntries vars titel matchar piece-titeln case-insensitive.
    Returnerar lista av dicts med entry + bokens namn. Tom om ingen träff."""
    if not title:
        return []
    rows = session.exec(
        select(PsalmEntry, PsalmBook)
        .join(PsalmBook, PsalmBook.id == PsalmEntry.book_id)
        .where(PsalmEntry.title.ilike(title.strip()))
        .order_by(PsalmBook.sort_order, PsalmBook.name, PsalmEntry.number)
    ).all()
    return [{"entry": e, "book": b} for e, b in rows]


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
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-fragment: slå upp PsalmEntry för given bok + nummer och returnera
    preview-rad. Utgåvan kommer från PsalmBook nu - inte separat input."""
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
    number: int = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    piece = session.get(Piece, piece_id)
    book = session.get(PsalmBook, book_id)
    if not piece or not book:
        raise HTTPException(404)

    # Edition ärvs från boken så ref och book alltid är konsekventa
    edition_val = book.edition
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


def _render_tag_area(request, session: Session, user: User, piece_id: int) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    active = session.exec(
        select(Tag)
        .join(PieceTag, PieceTag.tag_id == Tag.id)
        .where(PieceTag.piece_id == piece_id)
        .where(Tag.kind.not_in(["voicing", "accompaniment"]))
        .order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()
    return render(
        request,
        "pieces/_tag_area.html",
        {"piece": piece, "active_tags": active},
        user=user,
    )


@router.get("/{piece_id}/tags/area")
async def tag_area(
    request: Request,
    piece_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    return _render_tag_area(request, session, user, piece_id)


@router.get("/{piece_id}/tags/search")
async def search_tags_for_piece(
    request: Request,
    piece_id: int,
    q: str = "",
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-fragment: lista taggar som matchar q (case-insensitive), exklusive
    voicing/accompaniment som hanteras via egna dropdowns. Markerar de som
    redan är kopplade till piecen så användaren ser om en tagg redan finns."""
    query = q.strip()
    active_ids = set(
        session.exec(
            select(PieceTag.tag_id).where(PieceTag.piece_id == piece_id)
        ).all()
    )
    stmt = select(Tag).where(Tag.kind.not_in(["voicing", "accompaniment"]))
    if query:
        stmt = stmt.where(Tag.name.ilike(f"%{query}%"))
    stmt = stmt.order_by(Tag.kind, Tag.sort_order, Tag.name).limit(15)
    results = session.exec(stmt).all()
    # Tillåt skapa ny tagg om query inte exakt matchar någon träff
    can_create = bool(query) and not any(
        t.name.lower() == query.lower() for t in results
    )
    return render(
        request,
        "pieces/_tag_search_results.html",
        {
            "piece_id": piece_id,
            "results": results,
            "active_ids": active_ids,
            "query": query,
            "can_create": can_create,
        },
        user=user,
    )


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
    else:
        session.add(PieceTag(piece_id=piece_id, tag_id=tag_id))
    session.commit()

    if request.headers.get("HX-Request"):
        return _render_tag_area(request, session, user, piece_id)
    return RedirectResponse(f"/pieces/{piece_id}/edit", status.HTTP_302_FOUND)


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

    if request.headers.get("HX-Request"):
        return _render_tag_area(request, session, user, piece_id)
    flash(request, f"Tagg '{tag.name}' tillagd", "success")
    return RedirectResponse(f"/pieces/{piece_id}/edit", status.HTTP_302_FOUND)


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
