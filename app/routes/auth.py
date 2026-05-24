from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app.auth import hash_password, verify_password
from app.deps import current_user, get_session, require_auth, verify_csrf
from app.models import User
from app.templates_setup import flash, render

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login_form(
    request: Request,
    user: User | None = Depends(current_user),
) -> Response:
    if user:
        return RedirectResponse("/", status.HTTP_302_FOUND)
    return render(request, "auth/login.html", user=user)


@router.post("/login", dependencies=[Depends(verify_csrf)])
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    from sqlmodel import select

    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(password, user.password_hash):
        flash(request, "Fel användarnamn eller lösenord", "danger")
        return render(request, "auth/login.html", user=None, status_code=400)

    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()

    request.session["user_id"] = user.id

    if user.must_change_password:
        return RedirectResponse("/change-password", status.HTTP_302_FOUND)

    return RedirectResponse("/", status.HTTP_302_FOUND)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request) -> Response:
    request.session.pop("user_id", None)
    flash(request, "Du är utloggad", "info")
    return RedirectResponse("/login", status.HTTP_302_FOUND)


@router.get("/change-password")
async def change_password_form(
    request: Request,
    user: User = Depends(require_auth),
) -> Response:
    return render(request, "auth/change_password.html", user=user)


@router.post("/change-password", dependencies=[Depends(verify_csrf)])
async def change_password_submit(
    request: Request,
    current: str = Form(...),
    new: str = Form(...),
    confirm: str = Form(...),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    if not verify_password(current, user.password_hash):
        flash(request, "Nuvarande lösenord är fel", "danger")
        return render(request, "auth/change_password.html", user=user, status_code=400)
    if new != confirm:
        flash(request, "De nya lösenorden matchar inte", "danger")
        return render(request, "auth/change_password.html", user=user, status_code=400)
    if len(new) < 8:
        flash(request, "Lösenordet måste vara minst 8 tecken", "danger")
        return render(request, "auth/change_password.html", user=user, status_code=400)

    user.password_hash = hash_password(new)
    user.must_change_password = False
    session.add(user)
    session.commit()

    flash(request, "Lösenordet är bytt", "success")
    return RedirectResponse("/", status.HTTP_302_FOUND)
