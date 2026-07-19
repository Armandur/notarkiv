"""FastAPI-dependencies: session, current_user, CSRF, rollkrav."""

import secrets
from collections.abc import Iterator

from fastapi import Depends, Form, HTTPException, Request, status
from sqlmodel import Session

from app.db import engine
from app.models import User
from app.models.user import Role


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return request.session["csrf_token"]


def verify_csrf(request: Request, csrf_token: str = Form(...)) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ogiltig CSRF-token")


def current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return user


def require_auth(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Inloggning krävs",
            headers={"Location": "/login"},
        )
    return user


def require_editor(user: User = Depends(require_auth)) -> User:
    if not user.can_edit:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Du saknar redigeringsbehörighet")
    return user


def require_admin(user: User = Depends(require_auth)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Endast admin har åtkomst")
    return user


def current_kiosk(
    request: Request,
    session: Session = Depends(get_session),
):
    """Kiosken som har aktiverat denna webbsession (eller None)."""
    from app.models import Kiosk

    kid = request.session.get("kiosk_id")
    if not kid:
        return None
    return session.get(Kiosk, kid)


def kiosk_borrower_id_if_active(request: Request) -> int | None:
    """Returnera kiosk_borrower_id om det finns och inte gått ut på inaktivitet.

    Rensar sessionen och returnerar None om idle-timeouten (admin-konfigurerbar)
    passerats, annars touchas last-active-stämpeln. Detta är den enda källan
    till timeout-logiken - både _kiosk_borrower (GET-vyer) och cart/kiosk-
    dependencies nedan (POST) går via denna, så POST-routes inte kan agera
    efter att sessionen egentligen borde ha loggats ut."""
    bid = request.session.get("kiosk_borrower_id")
    if not bid:
        return None

    from datetime import datetime

    from app.services.app_settings import get_kiosk_idle_timeout_minutes
    from app.utils.dates import now_utc

    timeout_min = get_kiosk_idle_timeout_minutes()
    if timeout_min > 0:
        last_active_raw = request.session.get("kiosk_borrower_last_active")
        if last_active_raw:
            try:
                last_active = datetime.fromisoformat(last_active_raw)
                if (now_utc() - last_active).total_seconds() > timeout_min * 60:
                    request.session.pop("kiosk_borrower_id", None)
                    request.session.pop("kiosk_borrower_last_active", None)
                    return None
            except (TypeError, ValueError):
                pass
    request.session["kiosk_borrower_last_active"] = now_utc().isoformat()
    return bid


def require_cart_actor(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """Hämta User som agerar på en cart-action. I kiosk-only-session (ingen
    user_id men kiosk_borrower_id satt) returneras den PIN-autenticerade
    låntagaren. Annars den inloggade användaren. Alla auth:ade roller
    räcker - utlåning är inte en redigerande operation och alla i
    körlaget ska kunna låna noter."""
    user_id = request.session.get("user_id") or kiosk_borrower_id_if_active(request)
    if not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Inloggning eller PIN-autentisering krävs",
            headers={"Location": "/login"},
        )
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ogiltig session")
    return user


def require_kiosk_editor(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """Editor som agerar i en kiosk-session. Antingen browser-inloggad
    user eller PIN-autentiserad kiosk_borrower. Båda måste ha can_edit."""
    user_id = request.session.get("user_id") or kiosk_borrower_id_if_active(request)
    if not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Inloggning eller PIN-autentisering krävs",
            headers={"Location": "/login"},
        )
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ogiltig session")
    if not user.can_edit:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Saknar redigeringsbehörighet"
        )
    return user


def require_kiosk_session(
    request: Request,
    session: Session = Depends(get_session),
):
    """Kräv att webbläsaren är aktiverad som kiosk (via /kiosk/activate)."""
    from app.models import Kiosk

    kid = request.session.get("kiosk_id")
    if not kid:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Den här enheten är inte aktiverad som kiosk - admin måste aktivera först",
        )
    kiosk = session.get(Kiosk, kid)
    if not kiosk:
        request.session.pop("kiosk_id", None)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Ogiltig kiosk-session - aktivera om"
        )
    return kiosk
