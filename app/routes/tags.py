from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func as sqlf
from sqlmodel import Session, select

from app.deps import get_session, require_auth, require_editor, verify_csrf
from app.models import PieceTag, Tag, TagAlias, User
from app.models.tag import TagKind
from app.templates_setup import flash, render

router = APIRouter(prefix="/tags", tags=["tags"])


def _build_tree(tags: list[Tag], counts: dict[int, int]) -> list[dict]:
    """Bygg en hierarkisk struktur av taggar med children-listor."""
    by_id = {t.id: {"tag": t, "count": counts.get(t.id, 0), "children": []} for t in tags}
    roots: list[dict] = []
    for t in tags:
        node = by_id[t.id]
        if t.parent_id and t.parent_id in by_id:
            by_id[t.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


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
    tags = session.exec(select(Tag).order_by(Tag.kind, Tag.name)).all()

    # Hämta aliases och gruppera per tagg
    aliases_by_tag: dict[int, list[TagAlias]] = {}
    for a in session.exec(select(TagAlias).order_by(TagAlias.name)).all():
        aliases_by_tag.setdefault(a.tag_id, []).append(a)

    by_kind: dict[str, list[dict]] = {}
    for kind in TagKind:
        kind_tags = [t for t in tags if t.kind == kind.value]
        tree = _build_tree(kind_tags, counts)
        if tree:
            by_kind[kind.value] = tree

    return render(
        request,
        "tags/list.html",
        {
            "by_kind": by_kind,
            "kinds": [k.value for k in TagKind],
            "all_tags": tags,
            "aliases_by_tag": aliases_by_tag,
        },
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_tag(
    request: Request,
    name: str = Form(...),
    kind: str = Form("free"),
    description: str | None = Form(None),
    parent_id: str | None = Form(None),
    user: User = Depends(require_editor),
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

    pid: int | None = None
    if parent_id and parent_id.isdigit():
        parent = session.get(Tag, int(parent_id))
        if parent and parent.kind == kind_enum.value:
            pid = parent.id

    session.add(
        Tag(
            name=name,
            kind=kind_enum,
            description=(description or "").strip() or None,
            parent_id=pid,
        )
    )
    session.commit()
    flash(request, f"Skapade '{name}'", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)


_INLINE_KINDS = {TagKind.VOICING, TagKind.ACCOMPANIMENT}


@router.post("/inline", dependencies=[Depends(verify_csrf)])
async def create_tag_inline(
    request: Request,
    name: str = Form(...),
    kind: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    """Skapa eller återanvänd en tagg från scan/review-flödet. Returnerar JSON
    så Tom Select kan plocka in värdet utan sidladdning."""
    name = name.strip()
    if not name:
        return JSONResponse({"error": "Namn krävs"}, status_code=400)
    try:
        kind_enum = TagKind(kind)
    except ValueError:
        return JSONResponse({"error": "Okänd typ"}, status_code=400)
    if kind_enum not in _INLINE_KINDS:
        return JSONResponse({"error": "Endast besättning/ackompanjemang"}, status_code=400)

    existing = session.exec(select(Tag).where(Tag.name == name)).first()
    if existing:
        if existing.kind != kind_enum.value:
            return JSONResponse(
                {"error": f"'{name}' finns redan som {existing.kind}"}, status_code=409
            )
        return JSONResponse({"id": existing.id, "name": existing.name, "existing": True})

    tag = Tag(name=name, kind=kind_enum)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return JSONResponse({"id": tag.id, "name": tag.name, "existing": False})


@router.post("/{tag_id}/update", dependencies=[Depends(verify_csrf)])
async def update_tag(
    request: Request,
    tag_id: int,
    name: str = Form(...),
    kind: str = Form("free"),
    description: str | None = Form(None),
    parent_id: str | None = Form(None),
    user: User = Depends(require_editor),
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

    pid: int | None = None
    if parent_id and parent_id.isdigit():
        pid_int = int(parent_id)
        if pid_int != tag_id:  # förhindra själv-loop
            parent = session.get(Tag, pid_int)
            if parent and parent.kind == kind_enum.value:
                pid = parent.id

    tag.name = new_name
    tag.kind = kind_enum
    tag.description = (description or "").strip() or None
    tag.parent_id = pid
    session.add(tag)
    session.commit()
    flash(request, f"Uppdaterade '{tag.name}'", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)


@router.post("/{tag_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_tag(
    request: Request,
    tag_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404)
    # Lyft eventuella children upp till tag.parent_id så de inte blir orphans
    for child in session.exec(select(Tag).where(Tag.parent_id == tag_id)).all():
        child.parent_id = tag.parent_id
        session.add(child)
    for pt in session.exec(select(PieceTag).where(PieceTag.tag_id == tag_id)).all():
        session.delete(pt)
    name = tag.name
    session.delete(tag)
    session.commit()
    flash(request, f"Raderade '{name}' (kopplingar borttagna)", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)


@router.post("/{tag_id}/alias/add", dependencies=[Depends(verify_csrf)])
async def add_alias(
    request: Request,
    tag_id: int,
    name: str = Form(...),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404)
    alias_name = name.strip()
    if not alias_name:
        flash(request, "Alias-namn krävs", "danger")
        return RedirectResponse("/tags", status.HTTP_302_FOUND)

    # Kolla att namnet inte krockar med taggnamn eller annat alias
    if session.exec(select(Tag).where(Tag.name == alias_name)).first():
        flash(request, f"'{alias_name}' är redan ett taggnamn", "danger")
        return RedirectResponse("/tags", status.HTTP_302_FOUND)
    if session.exec(select(TagAlias).where(TagAlias.name == alias_name)).first():
        flash(request, f"Aliaset '{alias_name}' finns redan", "danger")
        return RedirectResponse("/tags", status.HTTP_302_FOUND)

    session.add(TagAlias(tag_id=tag_id, name=alias_name))
    session.commit()
    flash(request, f"Lade till alias '{alias_name}' för {tag.name}", "success")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)


@router.post("/{tag_id}/alias/{alias_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_alias(
    request: Request,
    tag_id: int,
    alias_id: int,
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    alias = session.get(TagAlias, alias_id)
    if not alias or alias.tag_id != tag_id:
        raise HTTPException(404)
    session.delete(alias)
    session.commit()
    flash(request, "Alias borttaget", "info")
    return RedirectResponse("/tags", status.HTTP_302_FOUND)
