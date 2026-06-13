from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.utils.dates import now_utc

from app.deps import (
    get_session,
    require_auth,
    require_kiosk_session,
    verify_csrf,
)
from app.models import (
    ContributorRole,
    Loan,
    Piece,
    PieceImage,
    PiecePlacement,
    PiecePsalmRef,
    PieceTag,
    PsalmBook,
    StorageLocation,
    StorageUnit,
    Tag,
    User,
)
from app.services.people import (
    collect_contributors,
)
from app.templates_setup import flash, render

from app.routes.pieces._routers import public_router, kiosk_router
from app.routes.pieces.helpers import (
    _kiosk_borrower,
    _kiosk_context,
    _kiosk_location_unit_ids,
)


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
    kiosk.last_activity_at = now_utc()
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
    request.session["kiosk_borrower_last_active"] = now_utc().isoformat()
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
    request.session["kiosk_borrower_last_active"] = now_utc().isoformat()
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

    now = now_utc()
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

    from app.services.storage import unit_path as _unit_path

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
                "path": _unit_path(session, unit) if unit else "Okänd plats",
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
            .order_by(Tag.kind, Tag.sort_order, Tag.name)
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

    # Om inventeringsläge är aktivt på kiosken: registrera piecen som FOUND
    # på alla placeringar inom kioskens plats.
    inventory_result = None
    if kiosk.active_inventory_session_id:
        from app.routes.kiosk_inventory import check_piece_for_kiosk

        inventory_result = check_piece_for_kiosk(
            session, kiosk, piece.id, borrower.id if borrower else None
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
            "inventory_result": inventory_result,
        },
        user=None,
    )
