from fastapi import Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.utils.dates import now_utc

from app.deps import (
    get_session,
    require_auth,
    require_editor,
    verify_csrf,
)
from app.models import (
    Piece,
    PieceImage,
    PiecePlacement,
    PiecePsalmRef,
    PieceTag,
    PieceUserNote,
    PsalmBook,
    PsalmEntry,
    StorageUnit,
    Tag,
    User,
)
from app.models.piece_image import PieceImageKind
from app.models.tag import TagKind
from app.templates_setup import flash, render
from app.utils.images import (
    delete_saved_image,
    rotate_saved_image,
    save_uploaded_cover,
)  # noqa: F401 - save_uploaded_cover används också för MB-portrait-import

from app.routes.pieces._routers import router
from app.routes.pieces.helpers import (
    _render_tag_area,
)


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
            # Inkommande antal från formuläret, annars placeringens nuvarande (kan vara None)
            incoming = int(copies) if copies and copies.isdigit() else placement.copies
            # Behåll None ("okänt/digitalt") om båda sidor är okända; summera annars.
            if incoming is None and other.copies is None:
                other.copies = None
            else:
                other.copies = (other.copies or 0) + (incoming or 0)
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
        existing.updated_at = now_utc()
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
