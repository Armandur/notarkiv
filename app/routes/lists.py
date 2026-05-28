"""Användarspecifika listor av noter. Default: en Favoriter-lista per
användare. Listor är alltid privata - ingen synlighet mellan användare."""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_auth, verify_csrf
from app.models import Piece, PieceList, PieceListItem, User
from app.templates_setup import flash, render

router = APIRouter(prefix="/lists", tags=["lists"])


FAVORITES_NAME = "Favoriter"


def _ensure_favorites(session: Session, user_id: int) -> PieceList:
    """Hämta eller skapa användarens Favoriter-lista."""
    favs = session.exec(
        select(PieceList)
        .where(PieceList.user_id == user_id)
        .where(PieceList.is_favorites == True)  # noqa: E712
    ).first()
    if favs:
        return favs
    favs = PieceList(
        user_id=user_id,
        name=FAVORITES_NAME,
        description="Snabbåtkomst via stjärna på noter",
        is_favorites=True,
    )
    session.add(favs)
    session.commit()
    session.refresh(favs)
    return favs


@router.get("")
async def list_my_lists(
    request: Request,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    _ensure_favorites(session, user.id)
    lists = list(
        session.exec(
            select(PieceList)
            .where(PieceList.user_id == user.id)
            .order_by(PieceList.is_favorites.desc(), PieceList.name)
        ).all()
    )
    # Räkna antal items per lista
    counts: dict[int, int] = {}
    for ll in lists:
        c = session.exec(
            select(PieceListItem).where(PieceListItem.list_id == ll.id)
        ).all()
        counts[ll.id] = len(c)
    return render(
        request,
        "lists/list.html",
        {"lists": lists, "counts": counts},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_list(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    clean = name.strip()
    if not clean:
        flash(request, "Namnet får inte vara tomt", "danger")
        return RedirectResponse("/lists", status.HTTP_302_FOUND)
    if clean.lower() == FAVORITES_NAME.lower():
        flash(
            request,
            "Favoriter är en reserverad lista som skapas automatiskt",
            "warning",
        )
        return RedirectResponse("/lists", status.HTTP_302_FOUND)
    existing = session.exec(
        select(PieceList)
        .where(PieceList.user_id == user.id)
        .where(PieceList.name == clean)
    ).first()
    if existing:
        flash(request, f'Du har redan en lista som heter "{clean}"', "warning")
        return RedirectResponse("/lists", status.HTTP_302_FOUND)
    ll = PieceList(
        user_id=user.id,
        name=clean,
        description=(description or "").strip() or None,
    )
    session.add(ll)
    session.commit()
    flash(request, f'Lista "{clean}" skapad', "success")
    return RedirectResponse(f"/lists/{ll.id}", status.HTTP_302_FOUND)


@router.get("/{list_id}")
async def list_detail(
    request: Request,
    list_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    ll = session.get(PieceList, list_id)
    if not ll or ll.user_id != user.id:
        raise HTTPException(404)
    items = session.exec(
        select(PieceListItem, Piece)
        .join(Piece, Piece.id == PieceListItem.piece_id)
        .where(PieceListItem.list_id == list_id)
        .order_by(PieceListItem.sort_order, PieceListItem.added_at)
    ).all()
    return render(
        request,
        "lists/detail.html",
        {"piece_list": ll, "items": items},
        user=user,
    )


@router.post("/{list_id}/update", dependencies=[Depends(verify_csrf)])
async def update_list(
    request: Request,
    list_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    ll = session.get(PieceList, list_id)
    if not ll or ll.user_id != user.id:
        raise HTTPException(404)
    clean = name.strip()
    if not clean:
        flash(request, "Namnet får inte vara tomt", "danger")
        return RedirectResponse(f"/lists/{list_id}", status.HTTP_302_FOUND)
    # Favoriter får inte byta namn
    if ll.is_favorites and clean != FAVORITES_NAME:
        flash(request, "Favoriter-listan kan inte byta namn", "warning")
        return RedirectResponse(f"/lists/{list_id}", status.HTTP_302_FOUND)
    if clean != ll.name:
        clash = session.exec(
            select(PieceList)
            .where(PieceList.user_id == user.id)
            .where(PieceList.name == clean)
            .where(PieceList.id != list_id)
        ).first()
        if clash:
            flash(request, f'Du har redan en lista som heter "{clean}"', "warning")
            return RedirectResponse(f"/lists/{list_id}", status.HTTP_302_FOUND)
    ll.name = clean
    ll.description = (description or "").strip() or None
    ll.updated_at = datetime.utcnow()
    session.add(ll)
    session.commit()
    flash(request, "Lista uppdaterad", "success")
    return RedirectResponse(f"/lists/{list_id}", status.HTTP_302_FOUND)


@router.post("/{list_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_list(
    request: Request,
    list_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    ll = session.get(PieceList, list_id)
    if not ll or ll.user_id != user.id:
        raise HTTPException(404)
    if ll.is_favorites:
        flash(request, "Favoriter-listan kan inte raderas", "warning")
        return RedirectResponse(f"/lists/{list_id}", status.HTTP_302_FOUND)
    name = ll.name
    session.delete(ll)
    session.commit()
    flash(request, f'Lista "{name}" raderad', "info")
    return RedirectResponse("/lists", status.HTTP_302_FOUND)


@router.post("/{list_id}/items/add", dependencies=[Depends(verify_csrf)])
async def add_to_list(
    request: Request,
    list_id: int,
    piece_id: int = Form(...),
    return_to: str = Form(""),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    ll = session.get(PieceList, list_id)
    if not ll or ll.user_id != user.id:
        raise HTTPException(404)
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    existing = session.exec(
        select(PieceListItem)
        .where(PieceListItem.list_id == list_id)
        .where(PieceListItem.piece_id == piece_id)
    ).first()
    if existing:
        flash(request, f'"{piece.title}" finns redan i {ll.name}', "info")
    else:
        # Sätt sort_order så ny rad hamnar sist
        last = session.exec(
            select(PieceListItem)
            .where(PieceListItem.list_id == list_id)
            .order_by(PieceListItem.sort_order.desc())
        ).first()
        next_order = (last.sort_order + 1) if last else 0
        item = PieceListItem(
            list_id=list_id,
            piece_id=piece_id,
            sort_order=next_order,
        )
        session.add(item)
        session.commit()
        flash(request, f'"{piece.title}" lagd i {ll.name}', "success")
    dest = return_to if return_to.startswith("/") else f"/pieces/{piece_id}"
    return RedirectResponse(dest, status.HTTP_302_FOUND)


@router.post(
    "/{list_id}/items/{piece_id}/remove", dependencies=[Depends(verify_csrf)]
)
async def remove_from_list(
    request: Request,
    list_id: int,
    piece_id: int,
    return_to: str = Form(""),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    ll = session.get(PieceList, list_id)
    if not ll or ll.user_id != user.id:
        raise HTTPException(404)
    item = session.exec(
        select(PieceListItem)
        .where(PieceListItem.list_id == list_id)
        .where(PieceListItem.piece_id == piece_id)
    ).first()
    if item:
        session.delete(item)
        session.commit()
        flash(request, "Borttagen ur listan", "info")
    dest = return_to if return_to.startswith("/") else f"/lists/{list_id}"
    return RedirectResponse(dest, status.HTTP_302_FOUND)


@router.post("/favorite/{piece_id}", dependencies=[Depends(verify_csrf)])
async def toggle_favorite(
    request: Request,
    piece_id: int,
    return_to: str = Form(""),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    """Snabb-toggle av favorit-status för en piece."""
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    favs = _ensure_favorites(session, user.id)
    existing = session.exec(
        select(PieceListItem)
        .where(PieceListItem.list_id == favs.id)
        .where(PieceListItem.piece_id == piece_id)
    ).first()
    if existing:
        session.delete(existing)
        msg = f'"{piece.title}" borttagen från favoriter'
    else:
        last = session.exec(
            select(PieceListItem)
            .where(PieceListItem.list_id == favs.id)
            .order_by(PieceListItem.sort_order.desc())
        ).first()
        item = PieceListItem(
            list_id=favs.id,
            piece_id=piece_id,
            sort_order=(last.sort_order + 1) if last else 0,
        )
        session.add(item)
        msg = f'"{piece.title}" lagd i favoriter'
    session.commit()
    flash(request, msg, "success")
    dest = return_to if return_to.startswith("/") else f"/pieces/{piece_id}"
    return RedirectResponse(dest, status.HTTP_302_FOUND)
