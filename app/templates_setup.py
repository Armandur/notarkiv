"""Jinja2-uppsättning med globaler för CSRF, current_user etc."""

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.models import User

templates = Jinja2Templates(directory="app/templates")


def render(
    request: Request,
    name: str,
    context: dict | None = None,
    *,
    user: User | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Rendera en template med default-kontext (request, csrf_token, user)."""
    ctx: dict = {
        "request": request,
        "csrf_token": request.session.get("csrf_token", ""),
        "user": user,
        "flash": _pop_flash(request),
        "pending_review_count": _pending_review_count() if user and user.can_edit else 0,
        "active_inventory_global": _active_inventory() if user and user.can_edit else None,
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _active_inventory():
    """Aktiv inventeringssession (om någon) - för navbar-banner."""
    from sqlmodel import Session

    from app.db import engine
    from app.services.inventory import get_active_session

    try:
        with Session(engine) as session:
            return get_active_session(session)
    except Exception:
        return None


def _pending_review_count() -> int:
    """Globalt antal skanningar som väntar på granskning - för navbar-badge."""
    from sqlalchemy import func as sqlf
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import ScanSession

    try:
        with Session(engine) as session:
            return session.exec(
                select(sqlf.count(ScanSession.id))
                .where(ScanSession.resulting_piece_id.is_(None))
                .where(ScanSession.discarded == False)  # noqa: E712
            ).one()
    except Exception:
        return 0


def flash(request: Request, message: str, kind: str = "info") -> None:
    """Lagra ett meddelande i sessionen, visas vid nästa render."""
    request.session["_flash"] = {"message": message, "kind": kind}


def _pop_flash(request: Request) -> dict | None:
    return request.session.pop("_flash", None)
