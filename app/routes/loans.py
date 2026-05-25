from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import Loan, Piece, PiecePlacement, StorageLocation, StorageUnit, User
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


@router.get("/loans")
async def list_loans(
    request: Request,
    show_returned: bool = False,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Översikt över alla utlån - default bara aktiva."""
    stmt = select(Loan).order_by(Loan.borrowed_at.desc())
    if not show_returned:
        stmt = stmt.where(Loan.returned_at.is_(None))
    loans = session.exec(stmt).all()

    # Berika varje loan med placement-, piece- och plats-info
    items = []
    if loans:
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
                    "piece": piece,
                    "unit": unit,
                    "location": loc,
                }
            )

    return render(
        request,
        "loans/list.html",
        {"items": items, "show_returned": show_returned},
        user=user,
    )


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
        )
    )
    session.commit()
    flash(request, f"Registrerade utlån till {name}", "success")
    return RedirectResponse(f"/pieces/{piece_id}", status.HTTP_302_FOUND)


@router.post("/loans/{loan_id}/return", dependencies=[Depends(verify_csrf)])
async def return_loan(
    request: Request,
    loan_id: int,
    user: User = Depends(require_editor),
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
        flash(request, f"Återlämnat: {loan.borrower_name}", "success")

    # Redirect tillbaka dit användaren kom ifrån om möjligt
    ref = request.headers.get("referer", "/loans")
    if not ref.startswith("/") and "://" in ref:
        # Plocka path från full URL
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
