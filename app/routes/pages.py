from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, func, select

from app.deps import current_user, get_session
from app.models import Piece, StorageLocation, Tag, User
from app.templates_setup import render

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(
    request: Request,
    user: User | None = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    if user is None:
        return RedirectResponse("/login")

    stats = {
        "pieces": session.exec(select(func.count(Piece.id))).one(),
        "locations": session.exec(select(func.count(StorageLocation.id))).one(),
        "tags": session.exec(select(func.count(Tag.id))).one(),
    }
    return render(request, "pages/index.html", {"stats": stats}, user=user)
