"""Jinja2-uppsättning med globaler för CSRF, current_user etc."""

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.models import User
from app.utils.countries import country_display, country_flag_emoji, country_name_sv
from app.utils.languages import language_display, language_name_sv


def to_paragraphs(text: str | None) -> list[dict]:
    """Dela en text i stycken. Returnerar lista av dicts {kind, text}
    där kind är 'heading' (för MediaWiki-rubriker som '== X ==') eller
    'p' för vanlig text. Hanterar '\\n\\n', '\\r\\n\\r\\n' och enkla
    radbrytningar (Wikipedia-extracten har ofta bara radbrytningar)."""
    if not text:
        return []
    import re

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n+", normalized)
    if len(parts) <= 1:
        parts = normalized.split("\n")
    out: list[dict] = []
    heading_re = re.compile(r"^(={2,})\s*(.+?)\s*\1$")
    for raw in parts:
        s = raw.strip()
        if not s:
            continue
        m = heading_re.match(s)
        if m:
            out.append({"kind": "heading", "level": len(m.group(1)), "text": m.group(2)})
        else:
            out.append({"kind": "p", "text": s})
    return out


def _markdown(text: str | None) -> str:
    """Rendera markdown till HTML. Tom sträng om None."""
    if not text:
        return ""
    import markdown as md

    return md.markdown(
        text,
        extensions=["nl2br", "sane_lists", "tables"],
        output_format="html",
    )


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["country_display"] = country_display
templates.env.globals["country_flag_emoji"] = country_flag_emoji
templates.env.globals["country_name_sv"] = country_name_sv
templates.env.globals["language_display"] = language_display
templates.env.globals["language_name_sv"] = language_name_sv
templates.env.globals["to_paragraphs"] = to_paragraphs
templates.env.filters["markdown"] = _markdown


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
        "active_inventory_global": _active_inventory(user.id) if user and user.can_edit else None,
        "active_loans_count": _active_loans_count() if user else 0,
        "cart_count": _cart_count(user.id) if user and user.can_edit else 0,
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _active_inventory(user_id: int):
    """Användarens egen aktiva inventering (om någon) - för navbar-prick."""
    from sqlmodel import Session

    from app.db import engine
    from app.services.inventory import get_user_default_active_session

    try:
        with Session(engine) as session:
            return get_user_default_active_session(session, user_id)
    except Exception:
        return None


def _active_loans_count() -> int:
    from sqlalchemy import func as sqlf
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Loan

    try:
        with Session(engine) as session:
            return session.exec(
                select(sqlf.count(Loan.id)).where(Loan.returned_at.is_(None))
            ).one()
    except Exception:
        return 0


def _cart_count(user_id: int) -> int:
    """Antal Loan-rader i användarens cart-batch."""
    from sqlalchemy import func as sqlf
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Loan, LoanBatch, LoanBatchStatus

    try:
        with Session(engine) as session:
            cart = session.exec(
                select(LoanBatch)
                .where(LoanBatch.created_by == user_id)
                .where(LoanBatch.status == LoanBatchStatus.CART)
            ).first()
            if not cart:
                return 0
            return session.exec(
                select(sqlf.count(Loan.id)).where(Loan.batch_id == cart.id)
            ).one()
    except Exception:
        return 0


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
