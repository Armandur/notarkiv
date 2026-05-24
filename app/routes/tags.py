from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func as sqlf
from sqlmodel import Session, select

from app.deps import get_session, require_auth
from app.models import PieceTag, Tag, User
from app.templates_setup import render

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
        {"by_kind": by_kind},
        user=user,
    )
