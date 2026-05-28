from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlmodel import Session, select

from app.deps import (
    get_session,
    require_admin,
    require_auth,
    require_cart_actor,
    require_editor,
    verify_csrf,
)
from app.models import (
    Loan,
    LoanBatch,
    LoanBatchStatus,
    Piece,
    PiecePlacement,
    StorageLocation,
    StorageUnit,
    User,
)
from app.templates_setup import flash, render

router = APIRouter(tags=["loans"])


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _cap_to_available(
    session: Session, placement_id: int, requested: int, exclude_loan_id: int | None = None
) -> tuple[int, int | None]:
    """Cap antalet exemplar mot vad som är ledigt på placeringen.

    Returnerar (faktiskt_antal, tillgängligt). tillgängligt är None för
    digitala placeringar (oändliga exemplar). Räknar alla aktiva lån
    (returned_at = NULL) - inklusive cart- och picking-poster så ingen
    rad reserverar mer än vad som finns hemma."""
    placement = session.get(PiecePlacement, placement_id)
    if not placement or placement.copies is None:
        return requested, None  # digital eller okänt placement: ingen gräns

    stmt = (
        select(Loan)
        .where(Loan.placement_id == placement_id)
        .where(Loan.returned_at.is_(None))
    )
    if exclude_loan_id:
        stmt = stmt.where(Loan.id != exclude_loan_id)
    reserved = sum(la.copies for la in session.exec(stmt).all())
    available = max(0, placement.copies - reserved)
    return min(requested, available), available


def _get_or_create_cart(session: Session, user_id: int) -> LoanBatch:
    """Hämta användarens aktiva kundvagn, skapa om saknas."""
    cart = session.exec(
        select(LoanBatch)
        .where(LoanBatch.created_by == user_id)
        .where(LoanBatch.status == LoanBatchStatus.CART)
    ).first()
    if cart:
        return cart
    cart = LoanBatch(created_by=user_id, status=LoanBatchStatus.CART)
    session.add(cart)
    session.commit()
    session.refresh(cart)
    return cart


def _enrich_loans(session: Session, loans: list[Loan]) -> list[dict]:
    """Berika loans med placement/piece/unit/location + registered_by-username
    för rendering."""
    if not loans:
        return []
    # Username för den som registrerade lånet (om finns)
    registered_ids = {la.registered_by for la in loans if la.registered_by}
    registered_users = {}
    if registered_ids:
        registered_users = {
            u.id: u.username for u in session.exec(
                select(User).where(User.id.in_(list(registered_ids)))
            ).all()
        }
    placements = {
        pl.id: pl for pl in session.exec(
            select(PiecePlacement).where(
                PiecePlacement.id.in_([loan.placement_id for loan in loans])
            )
        ).all()
    }
    pieces = {
        p.id: p for p in session.exec(
            select(Piece).where(
                Piece.id.in_([pl.piece_id for pl in placements.values()])
            )
        ).all()
    } if placements else {}
    units = {
        u.id: u for u in session.exec(
            select(StorageUnit).where(
                StorageUnit.id.in_([pl.storage_unit_id for pl in placements.values()])
            )
        ).all()
    } if placements else {}
    locs = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}

    items = []
    for loan in loans:
        placement = placements.get(loan.placement_id)
        if not placement:
            continue
        piece = pieces.get(placement.piece_id)
        unit = units.get(placement.storage_unit_id)
        loc = locs.get(unit.location_id) if unit else None
        items.append(
            {
                "loan": loan,
                "placement": placement,
                "piece": piece,
                "unit": unit,
                "location": loc,
                "path": _unit_path(session, unit) if unit else "",
                "registered_by_username": registered_users.get(loan.registered_by),
            }
        )
    return items


def _unit_path(session: Session, unit: StorageUnit | None) -> str:
    """Bygg fullständig hierarkisk sökväg till en unit (för visning)."""
    if not unit:
        return ""
    parts = [unit.name]
    cur = unit
    while cur.parent_id:
        parent = session.get(StorageUnit, cur.parent_id)
        if not parent:
            break
        parts.append(parent.name)
        cur = parent
    loc = session.get(StorageLocation, unit.location_id)
    if loc:
        parts.append(loc.name)
    return " › ".join(reversed(parts))


# ----- /loans-översikt -----------------------------------------------------


@router.get("/loans")
async def list_loans(
    request: Request,
    show_returned: bool = False,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Visa enskilda lån + grupperade batches. Cart-batchar exkluderas."""
    # Enskilda lån (utan batch_id) - aktiva först, sedan återlämnade
    stmt = (
        select(Loan)
        .where(Loan.batch_id.is_(None))
        .order_by(
            Loan.returned_at.is_not(None),
            Loan.returned_at.desc(),
            Loan.borrowed_at.desc(),
        )
    )
    if not show_returned:
        stmt = stmt.where(Loan.returned_at.is_(None))
    solo_loans = session.exec(stmt).all()
    solo_items = _enrich_loans(session, solo_loans)

    # Batches (picking/active/returned beroende på filter)
    batch_stmt = (
        select(LoanBatch)
        .where(LoanBatch.status != LoanBatchStatus.CART)
        .order_by(LoanBatch.registered_at.desc())
    )
    if not show_returned:
        batch_stmt = batch_stmt.where(LoanBatch.status != LoanBatchStatus.RETURNED)
    batches = session.exec(batch_stmt).all()

    # Username för batches' created_by (ansvarig)
    creator_ids = {b.created_by for b in batches if b.created_by}
    creators = {}
    if creator_ids:
        creators = {
            u.id: u.username for u in session.exec(
                select(User).where(User.id.in_(list(creator_ids)))
            ).all()
        }

    batch_items = []
    for batch in batches:
        loans = session.exec(
            select(Loan)
            .where(Loan.batch_id == batch.id)
            .order_by(
                Loan.returned_at.is_not(None),
                Loan.returned_at.desc(),
                Loan.borrowed_at.desc(),
                Loan.id.desc(),
            )
        ).all()
        items = _enrich_loans(session, loans)
        total = len(loans)
        picked = sum(1 for la in loans if la.picked_up_at)
        returned = sum(1 for la in loans if la.returned_at)
        batch_items.append(
            {
                "batch": batch,
                "entries": items,
                "total": total,
                "picked": picked,
                "returned": returned,
                "created_by_username": creators.get(batch.created_by),
            }
        )

    return render(
        request,
        "loans/list.html",
        {
            "solo_items": solo_items,
            "batch_items": batch_items,
            "show_returned": show_returned,
        },
        user=user,
    )


# ----- Enskilda lån (gamla flödet) -----------------------------------------


@router.post(
    "/pieces/{piece_id}/placements/{placement_id}/loans",
    dependencies=[Depends(verify_csrf)],
)
async def add_loan(
    request: Request,
    piece_id: int,
    placement_id: int,
    borrower_user_id: str | None = Form(None),
    borrower_name: str | None = Form(None),
    copies: int = Form(1),
    expected_return: str | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    placement = session.get(PiecePlacement, placement_id)
    if not placement or placement.piece_id != piece_id:
        raise HTTPException(404)

    user_id: int | None = None
    if borrower_user_id and borrower_user_id.isdigit():
        borrower_user = session.get(User, int(borrower_user_id))
        if borrower_user:
            user_id = borrower_user.id
            borrower_name = borrower_user.username

    name = (borrower_name or "").strip()
    if not name:
        flash(request, "Låntagare måste anges", "danger")
        return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)

    session.add(
        Loan(
            placement_id=placement_id,
            borrower_name=name,
            borrower_user_id=user_id,
            copies=max(1, copies),
            expected_return_at=_parse_date(expected_return),
            notes=(notes or "").strip() or None,
            registered_by=user.id,
            picked_up_at=datetime.utcnow(),  # Enskilt lån räknas direkt som hämtat
        )
    )
    session.commit()
    flash(request, f"Registrerade utlån till {name}", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/loans/{loan_id}/return", dependencies=[Depends(verify_csrf)])
async def return_loan(
    request: Request,
    loan_id: int,
    user: User = Depends(require_cart_actor),
    session: Session = Depends(get_session),
) -> Response:
    loan = session.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404)
    if loan.returned_at:
        flash(request, "Utlånet är redan markerat som återlämnat", "info")
    else:
        loan.returned_at = datetime.utcnow()
        session.add(loan)
        session.commit()
        # Om alla i batchen återlämnats - markera batchen som returned
        if loan.batch_id:
            batch = session.get(LoanBatch, loan.batch_id)
            if batch and batch.status == LoanBatchStatus.ACTIVE:
                remaining = session.exec(
                    select(Loan)
                    .where(Loan.batch_id == batch.id)
                    .where(Loan.returned_at.is_(None))
                ).first()
                if not remaining:
                    batch.status = LoanBatchStatus.RETURNED
                    batch.returned_at = datetime.utcnow()
                    session.add(batch)
                    session.commit()
        flash(request, f"Återlämnat: {loan.borrower_name}", "success")

    ref = request.headers.get("referer", "/loans")
    if not ref.startswith("/") and "://" in ref:
        from urllib.parse import urlparse
        ref = urlparse(ref).path or "/loans"
    return RedirectResponse(ref, status.HTTP_302_FOUND)


@router.post("/loans/{loan_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_loan(
    request: Request,
    loan_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    loan = session.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404)
    session.delete(loan)
    session.commit()
    flash(request, "Utlån borttaget", "info")
    ref = request.headers.get("referer", "/loans")
    if not ref.startswith("/") and "://" in ref:
        from urllib.parse import urlparse
        ref = urlparse(ref).path or "/loans"
    return RedirectResponse(ref, status.HTTP_302_FOUND)


# ----- Kundvagn -----------------------------------------------------------


@router.post("/loans/cart/add", dependencies=[Depends(verify_csrf)])
async def cart_add(
    request: Request,
    placement_id: int = Form(...),
    copies: int = Form(1),
    return_to: str | None = Form(None),
    user: User = Depends(require_cart_actor),
    session: Session = Depends(get_session),
) -> Response:
    placement = session.get(PiecePlacement, placement_id)
    if not placement:
        raise HTTPException(404)

    # I kiosk-läge ska kioskens PIN-autentiserade låntagare äga korgen,
    # inte den (fasta) kiosk-användaren som håller webbsessionen.
    effective_user_id = user.id
    is_kiosk = return_to and return_to.startswith("/kiosk")
    if is_kiosk:
        kiosk_bid = request.session.get("kiosk_borrower_id")
        if not kiosk_bid:
            flash(request, "Logga in med PIN för att låna", "warning")
            return RedirectResponse("/kiosk", status.HTTP_302_FOUND)
        effective_user_id = kiosk_bid

    cart = _get_or_create_cart(session, effective_user_id)

    # Om samma placement redan finns i korgen - addera antal istället
    existing = session.exec(
        select(Loan)
        .where(Loan.batch_id == cart.id)
        .where(Loan.placement_id == placement_id)
    ).first()
    requested = max(1, copies)

    def _redirect_back():
        # Explicit return_to har företräde - används av kiosken så lägg-i-korg
        # alltid returnerar till /kiosk istället för piece-vyn.
        target = return_to if return_to and return_to.startswith("/") else None
        if not target:
            ref = request.headers.get("referer", "/loans/cart")
            if not ref.startswith("/") and "://" in ref:
                from urllib.parse import urlparse
                ref = urlparse(ref).path or "/loans/cart"
            target = ref
        return RedirectResponse(target, status.HTTP_302_FOUND)

    if existing:
        new_total = existing.copies + requested
        capped, avail = _cap_to_available(
            session, placement_id, new_total, exclude_loan_id=existing.id
        )
        if avail is not None and new_total > capped:
            flash(request, f"Bara {capped} ex tillgängliga - antalet sattes till max", "warning")
        else:
            flash(request, "Antal uppdaterat i utlåningskorgen", "success")
        existing.copies = max(1, capped)
        session.add(existing)
    else:
        capped, avail = _cap_to_available(session, placement_id, requested)
        if avail is not None and capped == 0:
            flash(request, "Inga lediga exemplar - allt är redan utlånat eller i korg", "warning")
            return _redirect_back()
        if avail is not None and requested > capped:
            flash(request, f"Bara {capped} ex lediga - lade till så många", "warning")
        else:
            flash(request, "Lagt i utlåningskorg", "success")
        session.add(
            Loan(
                placement_id=placement_id,
                borrower_name="",
                copies=capped,
                batch_id=cart.id,
                registered_by=effective_user_id,
            )
        )
    session.commit()
    return _redirect_back()


@router.post("/storage/units/{unit_id}/add-all-to-cart", dependencies=[Depends(verify_csrf)])
async def cart_add_all_from_unit(
    request: Request,
    unit_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Lägg alla placeringar i en lagringsenhet i utlåningskorgen."""
    unit = session.get(StorageUnit, unit_id)
    if not unit:
        raise HTTPException(404)
    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.storage_unit_id == unit_id)
    ).all()
    if not placements:
        flash(request, "Inga noter på denna plats att lägga i korg", "info")
        return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)

    cart = _get_or_create_cart(session, user.id)
    added = 0
    skipped_full = 0
    for placement in placements:
        existing = session.exec(
            select(Loan)
            .where(Loan.batch_id == cart.id)
            .where(Loan.placement_id == placement.id)
        ).first()
        if existing:
            continue  # Skip dubbletter - användaren vill inte oavsiktligt dubbla
        capped, avail = _cap_to_available(session, placement.id, 1)
        if avail is not None and capped == 0:
            skipped_full += 1
            continue
        session.add(
            Loan(
                placement_id=placement.id,
                borrower_name="",
                copies=capped,
                batch_id=cart.id,
                registered_by=user.id,
            )
        )
        added += 1
    session.commit()

    if added:
        msg = f"Lade {added} noter i utlåningskorgen"
        if skipped_full:
            msg += f" ({skipped_full} hoppades över - inga lediga exemplar)"
        flash(request, msg, "success")
    elif skipped_full:
        flash(request, f"Inga lediga exemplar på {skipped_full} noter - inget tillagt", "warning")
    else:
        flash(request, "Alla noter från denna plats fanns redan i korgen", "info")
    return RedirectResponse(f"/storage/units/{unit_id}", status.HTTP_302_FOUND)


@router.get("/loans/cart")
async def cart_view(
    request: Request,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    cart = _get_or_create_cart(session, user.id)
    loans = session.exec(
        select(Loan).where(Loan.batch_id == cart.id).order_by(Loan.id)
    ).all()
    items = _enrich_loans(session, loans)

    # Berika varje rad med max-värde för UI. avail räknar redan utan den
    # egna raden, så det är direkt "max totalt jag kan ha här".
    for it in items:
        _, avail = _cap_to_available(
            session, it["loan"].placement_id, 9999, exclude_loan_id=it["loan"].id
        )
        it["max_copies"] = avail  # None för digitala (ingen gräns)

    # Gruppera per unit för plats-vyn
    by_unit: dict[int, dict] = {}
    for it in items:
        uid = it["unit"].id if it["unit"] else 0
        if uid not in by_unit:
            by_unit[uid] = {
                "unit": it["unit"],
                "location": it["location"],
                "path": _unit_path(session, it["unit"]) if it["unit"] else "Okänd plats",
                "entries": [],
            }
        by_unit[uid]["entries"].append(it)

    loan_users = session.exec(select(User).order_by(User.username)).all()

    return render(
        request,
        "loans/cart.html",
        {
            "cart": cart,
            "groups": list(by_unit.values()),
            "total": len(items),
            "loan_users": loan_users,
        },
        user=user,
    )


@router.post("/loans/cart/{loan_id}/update", dependencies=[Depends(verify_csrf)])
async def cart_update(
    request: Request,
    loan_id: int,
    copies: int = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    loan = session.get(Loan, loan_id)
    if not loan or loan.batch_id is None:
        raise HTTPException(404)
    batch = session.get(LoanBatch, loan.batch_id)
    if not batch or batch.status != LoanBatchStatus.CART or batch.created_by != user.id:
        raise HTTPException(403)
    requested = max(1, copies)
    capped, avail = _cap_to_available(
        session, loan.placement_id, requested, exclude_loan_id=loan.id
    )
    if avail is not None and requested > capped:
        flash(request, f"Bara {capped} ex tillgängliga - antalet sattes till max", "warning")
    loan.copies = max(1, capped)
    session.add(loan)
    session.commit()
    return RedirectResponse("/loans/cart", status.HTTP_302_FOUND)


@router.post("/loans/cart/{loan_id}/remove", dependencies=[Depends(verify_csrf)])
async def cart_remove(
    request: Request,
    loan_id: int,
    user: User = Depends(require_cart_actor),
    session: Session = Depends(get_session),
) -> Response:
    loan = session.get(Loan, loan_id)
    if not loan or loan.batch_id is None:
        raise HTTPException(404)
    batch = session.get(LoanBatch, loan.batch_id)
    if not batch or batch.status != LoanBatchStatus.CART or batch.created_by != user.id:
        raise HTTPException(403)
    session.delete(loan)
    session.commit()
    flash(request, "Tagit bort från korg", "info")
    return RedirectResponse("/loans/cart", status.HTTP_302_FOUND)


@router.post("/loans/cart/checkout", dependencies=[Depends(verify_csrf)])
async def cart_checkout(
    request: Request,
    name: str = Form(...),
    borrower_user_id: str | None = Form(None),
    borrower_name: str | None = Form(None),
    expected_return: str | None = Form(None),
    notes: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    cart = _get_or_create_cart(session, user.id)
    loans = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    if not loans:
        flash(request, "Korgen är tom", "warning")
        return RedirectResponse("/loans/cart", status.HTTP_302_FOUND)

    clean_name = name.strip()
    if not clean_name:
        flash(request, "Syftet med utlånet måste anges", "danger")
        return RedirectResponse("/loans/cart", status.HTTP_302_FOUND)

    bid: int | None = None
    bname = (borrower_name or "").strip()
    if borrower_user_id and borrower_user_id.isdigit():
        bu = session.get(User, int(borrower_user_id))
        if bu:
            bid = bu.id
            bname = bu.username
    if not bname:
        flash(request, "Låntagare måste anges", "danger")
        return RedirectResponse("/loans/cart", status.HTTP_302_FOUND)

    now = datetime.utcnow()
    cart.name = clean_name
    cart.borrower_name = bname
    cart.borrower_user_id = bid
    cart.expected_return_at = _parse_date(expected_return)
    cart.notes = (notes or "").strip() or None
    cart.status = LoanBatchStatus.PICKING
    cart.borrowed_at = now
    cart.registered_at = now
    session.add(cart)

    for loan in loans:
        loan.borrower_name = bname
        loan.borrower_user_id = bid
        loan.expected_return_at = cart.expected_return_at
        loan.borrowed_at = now
        session.add(loan)

    session.commit()
    flash(request, f'Utlån "{clean_name}" registrerad - hämta noterna nu', "success")
    return RedirectResponse(f"/loans/batch/{cart.id}/pickup", status.HTTP_302_FOUND)


# ----- Batch-detalj + plockning -------------------------------------------


def _load_batch_with_loans(session: Session, batch_id: int) -> tuple[LoanBatch, list[dict]]:
    batch = session.get(LoanBatch, batch_id)
    if not batch:
        raise HTTPException(404)
    # Sortering: aktiva först (returned_at NULL), sedan återlämnade nyast först.
    # Inom aktiva: senast tillagda först (id desc - approximation av borrowed_at).
    loans = session.exec(
        select(Loan)
        .where(Loan.batch_id == batch.id)
        .order_by(
            Loan.returned_at.is_not(None),  # False (aktiva) före True (returned)
            Loan.returned_at.desc(),
            Loan.borrowed_at.desc(),
            Loan.id.desc(),
        )
    ).all()
    return batch, _enrich_loans(session, loans)


def _group_by_unit(session: Session, items: list[dict]) -> list[dict]:
    by_unit: dict[int, dict] = {}
    for it in items:
        uid = it["unit"].id if it["unit"] else 0
        if uid not in by_unit:
            by_unit[uid] = {
                "unit": it["unit"],
                "location": it["location"],
                "path": _unit_path(session, it["unit"]) if it["unit"] else "Okänd plats",
                "entries": [],
            }
        by_unit[uid]["entries"].append(it)
    return list(by_unit.values())


@router.get("/loans/batch/{batch_id}")
async def batch_detail(
    request: Request,
    batch_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    batch, items = _load_batch_with_loans(session, batch_id)
    if batch.status == LoanBatchStatus.CART:
        return RedirectResponse("/loans/cart", status.HTTP_302_FOUND)
    groups = _group_by_unit(session, items)
    return render(
        request,
        "loans/batch_detail.html",
        {"batch": batch, "items": items, "groups": groups},
        user=user,
    )


@router.get("/loans/batch/{batch_id}/pickup")
async def batch_pickup_view(
    request: Request,
    batch_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    batch, items = _load_batch_with_loans(session, batch_id)
    if batch.status not in (LoanBatchStatus.PICKING, LoanBatchStatus.ACTIVE):
        raise HTTPException(404)
    groups = _group_by_unit(session, items)
    return render(
        request,
        "loans/pickup.html",
        {"batch": batch, "groups": groups, "items": items},
        user=user,
    )


@router.post("/loans/{loan_id}/pickup", dependencies=[Depends(verify_csrf)])
async def mark_picked_up(
    loan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    loan = session.get(Loan, loan_id)
    if not loan or loan.batch_id is None:
        raise HTTPException(404)
    loan.picked_up_at = datetime.utcnow()
    session.add(loan)
    session.commit()
    return RedirectResponse(f"/loans/batch/{loan.batch_id}/pickup", status.HTTP_302_FOUND)


@router.post("/loans/{loan_id}/not-found", dependencies=[Depends(verify_csrf)])
async def mark_not_found(
    loan_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    loan = session.get(Loan, loan_id)
    if not loan or loan.batch_id is None:
        raise HTTPException(404)
    batch_id = loan.batch_id
    session.delete(loan)
    session.commit()
    return RedirectResponse(f"/loans/batch/{batch_id}/pickup", status.HTTP_302_FOUND)


@router.post("/loans/batch/{batch_id}/activate", dependencies=[Depends(verify_csrf)])
async def batch_activate(
    request: Request,
    batch_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    batch = session.get(LoanBatch, batch_id)
    if not batch or batch.status != LoanBatchStatus.PICKING:
        raise HTTPException(404)
    loans = session.exec(select(Loan).where(Loan.batch_id == batch.id)).all()
    picked = [la for la in loans if la.picked_up_at]
    if not picked:
        flash(request, "Markera minst en not som hämtad innan du slutregistrerar", "warning")
        return RedirectResponse(f"/loans/batch/{batch_id}/pickup", status.HTTP_302_FOUND)

    # Radera ohämtade rader (de räknas inte som utlånade)
    for la in loans:
        if not la.picked_up_at:
            session.delete(la)

    batch.status = LoanBatchStatus.ACTIVE
    batch.activated_at = datetime.utcnow()
    session.add(batch)
    session.commit()
    flash(request, f'Utlån "{batch.name}" är nu aktivt ({len(picked)} noter)', "success")
    return RedirectResponse(f"/loans/batch/{batch_id}", status.HTTP_302_FOUND)


@router.post(
    "/loans/batch/{batch_id}/return-at-kiosk", dependencies=[Depends(verify_csrf)]
)
async def batch_return_at_kiosk(
    request: Request,
    batch_id: int,
    user: User = Depends(require_cart_actor),
    session: Session = Depends(get_session),
) -> Response:
    """Återlämna alla loans i batchen vars placering finns på aktuell
    kiosks plats. Resten kvarstår som aktiva och måste återlämnas där
    de hör hemma."""
    from app.models import Kiosk

    kiosk_id = request.session.get("kiosk_id")
    if not kiosk_id:
        raise HTTPException(403, "Bara från en aktiverad kiosk")
    kiosk = session.get(Kiosk, kiosk_id)
    if not kiosk or not kiosk.location_id:
        raise HTTPException(400, "Kiosken är inte knuten till en lagringsplats")

    batch = session.get(LoanBatch, batch_id)
    if not batch or batch.status != LoanBatchStatus.ACTIVE:
        raise HTTPException(404)

    # Hitta units i kioskens plats
    allowed_unit_ids = {
        u.id for u in session.exec(
            select(StorageUnit).where(StorageUnit.location_id == kiosk.location_id)
        ).all()
    }

    now = datetime.utcnow()
    loans = session.exec(
        select(Loan).where(Loan.batch_id == batch.id).where(Loan.returned_at.is_(None))
    ).all()
    placements = {
        p.id: p for p in session.exec(
            select(PiecePlacement).where(
                PiecePlacement.id.in_([la.placement_id for la in loans])
            )
        ).all()
    } if loans else {}

    returned_count = 0
    skipped_count = 0
    for la in loans:
        placement = placements.get(la.placement_id)
        if placement and placement.storage_unit_id in allowed_unit_ids:
            la.returned_at = now
            session.add(la)
            returned_count += 1
        else:
            skipped_count += 1

    # Om alla återlämnade -> markera batch som returned
    if skipped_count == 0:
        batch.status = LoanBatchStatus.RETURNED
        batch.returned_at = now
        session.add(batch)

    session.commit()

    msg = f'Återlämnade {returned_count} noter från "{batch.name}" till {kiosk.name}'
    if skipped_count:
        msg += f" - {skipped_count} kvar (finns på andra platser)"
    flash(request, msg, "success")
    return RedirectResponse("/kiosk", status.HTTP_302_FOUND)


@router.post("/loans/batch/{batch_id}/return-all", dependencies=[Depends(verify_csrf)])
async def batch_return_all(
    request: Request,
    batch_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    batch = session.get(LoanBatch, batch_id)
    if not batch or batch.status != LoanBatchStatus.ACTIVE:
        raise HTTPException(404)
    now = datetime.utcnow()
    loans = session.exec(
        select(Loan).where(Loan.batch_id == batch.id).where(Loan.returned_at.is_(None))
    ).all()
    for la in loans:
        la.returned_at = now
        session.add(la)
    batch.status = LoanBatchStatus.RETURNED
    batch.returned_at = now
    session.add(batch)
    session.commit()
    flash(request, f'Hela utlånet "{batch.name}" återlämnat', "success")
    return RedirectResponse(f"/loans/batch/{batch_id}", status.HTTP_302_FOUND)


@router.get("/loans/batch/{batch_id}/search-placements")
async def batch_search_placements(
    request: Request,
    batch_id: int,
    q: str = "",
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """HTMX-autocomplete: hitta placeringar matchande sökterm. Returnerar
    HTML-fragment med kandidater + 'Lägg till'-knapp per rad."""
    batch = session.get(LoanBatch, batch_id)
    if not batch or batch.status not in (LoanBatchStatus.PICKING, LoanBatchStatus.ACTIVE):
        raise HTTPException(404)

    q = q.strip()
    if len(q) < 2:
        return Response("", media_type="text/html")

    # Python-side filtrering (Unicode-säker, hanterar ÅÄÖ)
    q_lower = q.lower()
    all_pieces = session.exec(select(Piece).order_by(Piece.title)).all()
    pieces = [
        p for p in all_pieces
        if q_lower in (p.title or "").lower()
        or q_lower in (p.contributors_cache or "").lower()
    ][:20]
    if not pieces:
        return Response("", media_type="text/html")

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id.in_([p.id for p in pieces]))
    ).all()
    pieces_by_id = {p.id: p for p in pieces}
    units = {
        u.id: u for u in session.exec(
            select(StorageUnit).where(
                StorageUnit.id.in_([pl.storage_unit_id for pl in placements])
            )
        ).all()
    }

    rows = []
    for pl in placements:
        piece = pieces_by_id.get(pl.piece_id)
        unit = units.get(pl.storage_unit_id)
        if not piece:
            continue
        rows.append(
            {
                "placement": pl,
                "piece": piece,
                "unit": unit,
                "path": _unit_path(session, unit) if unit else "Okänd plats",
            }
        )

    from app.templates_setup import templates
    return templates.TemplateResponse(
        request,
        "loans/_placement_search.html",
        {"rows": rows, "batch_id": batch_id, "csrf_token": request.session.get("csrf_token", "")},
    )


@router.post("/loans/batch/{batch_id}/add-more", dependencies=[Depends(verify_csrf)])
async def batch_add_more(
    request: Request,
    batch_id: int,
    placement_id: int = Form(...),
    copies: int = Form(1),
    return_to: str | None = Form(None),
    user: User = Depends(require_cart_actor),
    session: Session = Depends(get_session),
) -> Response:
    """Lägg till fler placeringar i en pågående batch.

    Tillåts både under picking (fynd-noter) och på en active batch (man kom
    på efter slutregistrering att man glömt en not). Vid active sätts
    picked_up_at = now eftersom noten är fysiskt med från start."""
    batch = session.get(LoanBatch, batch_id)
    if not batch or batch.status not in (LoanBatchStatus.PICKING, LoanBatchStatus.ACTIVE):
        raise HTTPException(404)
    placement = session.get(PiecePlacement, placement_id)
    if not placement:
        raise HTTPException(404)

    if return_to and return_to.startswith("/"):
        redirect_to = return_to
    else:
        redirect_to = (
            f"/loans/batch/{batch_id}/pickup"
            if batch.status == LoanBatchStatus.PICKING
            else f"/loans/batch/{batch_id}"
        )

    piece = session.get(Piece, placement.piece_id) if placement.piece_id else None
    title = piece.title if piece else "noten"

    # Hitta AKTIVA lån på denna placement i batchen. En redan återlämnad
    # loan ska INTE räknas - om man lämnar tillbaka och lånar igen ska det
    # bli en ny rad, inte att copies ökar på den återlämnade.
    existing = session.exec(
        select(Loan)
        .where(Loan.batch_id == batch.id)
        .where(Loan.placement_id == placement_id)
        .where(Loan.returned_at.is_(None))
    ).first()
    requested = max(1, copies)
    if existing:
        new_total = existing.copies + requested
        capped, avail = _cap_to_available(
            session, placement_id, new_total, exclude_loan_id=existing.id
        )
        if avail is not None and new_total > capped:
            flash(
                request,
                f"Bara {capped} ex tillgängliga - antalet sattes till max på {title} i {batch.name}",
                "warning",
            )
        else:
            flash(
                request,
                f"Ökade antal till {capped} på {title} i {batch.name}",
                "success",
            )
        existing.copies = max(1, capped)
        session.add(existing)
    else:
        capped, avail = _cap_to_available(session, placement_id, requested)
        if avail is not None and capped == 0:
            flash(request, "Inga lediga exemplar på denna placering", "warning")
            return RedirectResponse(redirect_to, status.HTTP_302_FOUND)
        if avail is not None and requested > capped:
            flash(request, f"Bara {capped} ex lediga - lade till så många", "warning")
        now = datetime.utcnow()
        session.add(
            Loan(
                placement_id=placement_id,
                borrower_name=batch.borrower_name or "",
                borrower_user_id=batch.borrower_user_id,
                copies=capped,
                batch_id=batch.id,
                borrowed_at=batch.borrowed_at,
                expected_return_at=batch.expected_return_at,
                registered_by=user.id,
                # Active-batch räknas som "redan hämtad" eftersom noten är med
                picked_up_at=now if batch.status == LoanBatchStatus.ACTIVE else None,
            )
        )
        flash(request, f'La till "{title}" i {batch.name}', "success")
    session.commit()
    return RedirectResponse(redirect_to, status.HTTP_302_FOUND)


# ----- PDF-plocklista -----------------------------------------------------


@router.get("/loans/batch/{batch_id}/pickup.pdf")
async def batch_pickup_pdf(
    request: Request,
    batch_id: int,
    user: User = Depends(require_cart_actor),
    session: Session = Depends(get_session),
) -> Response:
    batch, items = _load_batch_with_loans(session, batch_id)
    if batch.status == LoanBatchStatus.CART:
        raise HTTPException(404)
    groups = _group_by_unit(session, items)

    from weasyprint import HTML

    from app.templates_setup import templates

    html = templates.get_template("loans/pickup_pdf.html").render(
        request=request,
        batch=batch,
        groups=groups,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
    )
    pdf_bytes = HTML(string=html).write_pdf()
    buf = BytesIO(pdf_bytes)
    safe_name = (batch.name or f"batch-{batch.id}").replace('"', '')
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="plocklista-{batch.id}.pdf"'},
    )
