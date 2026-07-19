from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, verify_csrf
from app.models import PiecePsalmRef, PsalmBook, PsalmEntry, User
from app.templates_setup import flash, render

router = APIRouter(prefix="/admin/psalmbooks", tags=["admin"])


@router.get("")
async def list_psalmbooks(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    books = session.exec(
        select(PsalmBook).order_by(PsalmBook.sort_order, PsalmBook.name)
    ).all()
    # Räkna antal psalmref per bok
    from sqlalchemy import func as sqlf

    counts = dict(
        session.exec(
            select(PiecePsalmRef.book_id, sqlf.count(PiecePsalmRef.id))
            .group_by(PiecePsalmRef.book_id)
        ).all()
    )
    return render(
        request,
        "admin/psalmbooks.html",
        {"books": books, "counts": counts},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_psalmbook(
    request: Request,
    name: str = Form(...),
    edition: str | None = Form(None),
    description: str | None = Form(None),
    sort_order: int = Form(0),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    name = name.strip()
    edition_val = (edition or "").strip() or None
    if not name:
        flash(request, "Namn krävs", "danger")
        return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)
    # Dubblettkoll på (name, edition)
    if session.exec(
        select(PsalmBook)
        .where(PsalmBook.name == name)
        .where(PsalmBook.edition == edition_val)
    ).first():
        flash(request, f"'{name}' ({edition_val or 'utan utgåva'}) finns redan", "danger")
        return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)

    session.add(
        PsalmBook(
            name=name,
            edition=edition_val,
            description=(description or "").strip() or None,
            sort_order=sort_order,
        )
    )
    session.commit()
    flash(request, f"Skapade '{name}'", "success")
    return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)


@router.post("/{book_id}/update", dependencies=[Depends(verify_csrf)])
async def update_psalmbook(
    request: Request,
    book_id: int,
    name: str = Form(...),
    edition: str | None = Form(None),
    description: str | None = Form(None),
    sort_order: int = Form(0),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    book = session.get(PsalmBook, book_id)
    if not book:
        raise HTTPException(404)

    new_name = name.strip()
    new_edition = (edition or "").strip() or None
    if new_name != book.name or new_edition != book.edition:
        clash = session.exec(
            select(PsalmBook)
            .where(PsalmBook.name == new_name)
            .where(PsalmBook.edition == new_edition)
            .where(PsalmBook.id != book_id)
        ).first()
        if clash:
            flash(
                request,
                f"'{new_name}' ({new_edition or 'utan utgåva'}) finns redan",
                "danger",
            )
            return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)

    book.name = new_name
    book.edition = new_edition
    book.description = (description or "").strip() or None
    book.sort_order = sort_order
    session.add(book)
    session.commit()
    flash(request, f"Uppdaterade '{book.name}'", "success")
    return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)


@router.post("/{book_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_psalmbook(
    request: Request,
    book_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    book = session.get(PsalmBook, book_id)
    if not book:
        raise HTTPException(404)

    in_use = session.exec(
        select(PiecePsalmRef).where(PiecePsalmRef.book_id == book_id).limit(1)
    ).first()
    if in_use:
        flash(
            request,
            f"'{book.name}' används av minst en not - ta bort referenserna först",
            "danger",
        )
        return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)

    has_entries = session.exec(
        select(PsalmEntry).where(PsalmEntry.book_id == book_id).limit(1)
    ).first()
    if has_entries:
        flash(
            request,
            f"'{book.name}' har registrerade psalmnummer - ta bort dem först",
            "danger",
        )
        return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)

    name = book.name
    session.delete(book)
    session.commit()
    flash(request, f"Raderade '{name}'", "success")
    return RedirectResponse("/admin/psalmbooks", status.HTTP_302_FOUND)
