from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func as sqlf
from sqlmodel import Session, select

from app.deps import get_session, require_admin, require_auth, verify_csrf
from app.models import PieceTag, Tag, User
from app.models.tag import TagKind
from app.templates_setup import flash, render

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
async def list_tags(
    request: Request,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    counts = dict(
        session.exec(
            select(PieceTag.tag_id, sqlf.count(PieceTag.piece_id)).group_by(PieceTag.tag_id)
        ).all()
    )
    tags = session.exec(
        select(Tag).order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()

    by_kind: dict[str, list[dict]] = {}
    for t in tags:
        by_kind.setdefault(t.kind, []).append(
            {"tag": t, "count": counts.get(t.id, 0)}
        )

    return render(
        request,
        "tags/list.html",
        {"by_kind": by_kind, "kinds": [k.value for k in TagKind]},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_tag(
    request: Request,
    name: str = Form(...),
    kind: str = Form("free"),
    sort_order: int = Form(0),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    name = name.strip()
    if not name:
        flash(request, "Namn krävs", "danger")
        return RedirectResponse("/tags", status.HTTP_302_FOUND)
    if session.exec(select(Tag).where(Tag.name == name)).first():
        flash(request, f"Taggen '{name}' finns redan", "danger")
        return RedirectResponse("/tags", status.HTTP_302_FOUND)
    try:
        kind_enum = TagKind(kind)
    except ValueError:
        kind_enum = TagKind.FREE
    session.add(Tag(name=name, kind=kind_enum, sort_order=sort_order))
    session.commit()
    flash(request, f"Skapade '{name}'", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)


@router.post("/{tag_id}/update", dependencies=[Depends(verify_csrf)])
async def update_tag(
    request: Request,
    tag_id: int,
    name: str = Form(...),
    kind: str = Form("free"),
    sort_order: int = Form(0),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404)
    new_name = name.strip()
    if not new_name:
        flash(request, "Namn krävs", "danger")
        return RedirectResponse("/tags", status.HTTP_302_FOUND)
    if new_name != tag.name:
        clash = session.exec(
            select(Tag).where(Tag.name == new_name).where(Tag.id != tag_id)
        ).first()
        if clash:
            flash(request, f"En annan tagg heter redan '{new_name}'", "danger")
            return RedirectResponse("/tags", status.HTTP_302_FOUND)
    try:
        kind_enum = TagKind(kind)
    except ValueError:
        kind_enum = TagKind.FREE
    tag.name = new_name
    tag.kind = kind_enum
    tag.sort_order = sort_order
    session.add(tag)
    session.commit()
    flash(request, f"Uppdaterade '{tag.name}'", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)


@router.post("/{tag_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_tag(
    request: Request,
    tag_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404)
    for pt in session.exec(
        select(PieceTag).where(PieceTag.tag_id == tag_id)
    ).all():
        session.delete(pt)
    name = tag.name
    session.delete(tag)
    session.commit()
    flash(request, f"Raderade '{name}' (kopplingar borttagna)", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)
