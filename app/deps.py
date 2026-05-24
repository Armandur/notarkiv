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
