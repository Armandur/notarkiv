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
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, message: str, kind: str = "info") -> None:
    """Lagra ett meddelande i sessionen, visas vid nästa render."""
    request.session["_flash"] = {"message": message, "kind": kind}


def _pop_flash(request: Request) -> dict | None:
    return request.session.pop("_flash", None)
