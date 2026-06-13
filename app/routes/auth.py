from datetime import datetime
from app.utils.dates import now_utc

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

    user.last_login_at = now_utc()
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


@router.get("/profile")
async def profile(
    request: Request,
    user: User = Depends(require_auth),
) -> Response:
    return render(request, "auth/profile.html", user=user)


@router.get("/profile/kiosk-qr.png")
async def profile_kiosk_qr(
    request: Request,
    user: User = Depends(require_auth),
) -> Response:
    """QR-bild för kiosk-auth. Data = 'u:<token>' så kiosken kan skilja
    det från piece-QR. Användarens token är hemlig - behandla som lösenord."""
    import io
    import qrcode

    if not user.kiosk_token:
        raise HTTPException(404, "Ingen kiosk-token - logga ut och in igen")
    img = qrcode.make(f"u:{user.kiosk_token}", box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.post("/profile/kiosk-token/regenerate", dependencies=[Depends(verify_csrf)])
async def regenerate_kiosk_token(
    request: Request,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    import secrets

    user.kiosk_token = secrets.token_hex(16)
    session.add(user)
    session.commit()
    flash(request, "Ny kiosk-QR genererad - gamla koden funkar inte längre", "success")
    return RedirectResponse("/profile", status.HTTP_302_FOUND)


@router.post("/profile/pin", dependencies=[Depends(verify_csrf)])
async def set_pin(
    request: Request,
    pin: str = Form(...),
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    from app.auth import hash_pin

    clean = pin.strip()
    if not clean.isdigit() or not (4 <= len(clean) <= 8):
        flash(request, "PIN måste vara 4-8 siffror", "danger")
        return RedirectResponse("/profile", status.HTTP_302_FOUND)
    user.pin_hash = hash_pin(clean)
    session.add(user)
    session.commit()
    flash(request, "PIN-kod sparad", "success")
    return RedirectResponse("/profile", status.HTTP_302_FOUND)


@router.post("/profile/pin/clear", dependencies=[Depends(verify_csrf)])
async def clear_pin(
    request: Request,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    user.pin_hash = None
    session.add(user)
    session.commit()
    flash(request, "PIN-kod borttagen", "info")
    return RedirectResponse("/profile", status.HTTP_302_FOUND)
