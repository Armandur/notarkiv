import secrets
import string

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.auth import hash_password
from app.deps import get_session, require_admin, verify_csrf
from app.models import (
    AppSetting,
    InventoryCheck,
    InventorySession,
    Loan,
    LoanBatch,
    Piece,
    ScanSession,
    User,
)
from app.models.user import Role
from app.templates_setup import flash, render

router = APIRouter(prefix="/admin/users", tags=["admin"])


def _generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("")
async def list_users(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    users = session.exec(select(User).order_by(User.username)).all()
    return render(
        request,
        "admin/users.html",
        {"users": users, "roles": [r.value for r in Role]},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_user_action(
    request: Request,
    username: str = Form(...),
    role: Role = Form(...),
    email: str | None = Form(None),
    password: str | None = Form(None),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    username = username.strip()
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing:
        flash(request, f"Användarnamn '{username}' finns redan", "danger")
        return RedirectResponse("/admin/users", status.HTTP_302_FOUND)

    temp_password = password.strip() if password and password.strip() else _generate_temp_password()
    new_user = User(
        username=username,
        email=(email or "").strip() or None,
        password_hash=hash_password(temp_password),
        role=role,
        must_change_password=True,
    )
    session.add(new_user)
    session.commit()

    if password and password.strip():
        flash(request, f"Skapade {username}. Användaren måste byta lösenord vid login.", "success")
    else:
        flash(
            request,
            f"Skapade {username} med tillfälligt lösenord: {temp_password}",
            "success",
        )
    return RedirectResponse("/admin/users", status.HTTP_302_FOUND)


@router.post("/{user_id}/role", dependencies=[Depends(verify_csrf)])
async def update_role(
    request: Request,
    user_id: int,
    role: Role = Form(...),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404)
    if target.id == user.id and role != Role.ADMIN:
        flash(request, "Du kan inte ta bort din egen admin-roll", "danger")
        return RedirectResponse("/admin/users", status.HTTP_302_FOUND)

    target.role = role
    session.add(target)
    session.commit()
    flash(request, f"Roll för {target.username} satt till {role.value}", "success")
    return RedirectResponse("/admin/users", status.HTTP_302_FOUND)


@router.post("/{user_id}/reset-password", dependencies=[Depends(verify_csrf)])
async def reset_password_action(
    request: Request,
    user_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404)

    temp = _generate_temp_password()
    target.password_hash = hash_password(temp)
    target.must_change_password = True
    session.add(target)
    session.commit()
    flash(request, f"Nytt tillfälligt lösenord för {target.username}: {temp}", "success")
    return RedirectResponse("/admin/users", status.HTTP_302_FOUND)


@router.post("/{user_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_user_action(
    request: Request,
    user_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404)
    if target.id == user.id:
        flash(request, "Du kan inte radera ditt eget konto", "danger")
        return RedirectResponse("/admin/users", status.HTTP_302_FOUND)

    # Blockera radering om användaren refereras av en FK utan ondelete=CASCADE
    # (skulle annars ge IntegrityError/500). PieceList och PieceUserNote har
    # CASCADE och städas automatiskt, så de behöver ingen koll här.
    references = [
        (LoanBatch, LoanBatch.created_by),
        (LoanBatch, LoanBatch.borrower_user_id),
        (Loan, Loan.borrower_user_id),
        (Loan, Loan.registered_by),
        (ScanSession, ScanSession.user_id),
        (Piece, Piece.created_by),
        (InventorySession, InventorySession.started_by),
        (InventoryCheck, InventoryCheck.checked_by),
        (AppSetting, AppSetting.updated_by),
    ]
    in_use = any(
        session.exec(select(model).where(col == user_id).limit(1)).first()
        for model, col in references
    )
    if in_use:
        flash(
            request,
            f"Kan inte radera {target.username} - användaren har skapat lån, "
            "skanningar, noter eller andra poster",
            "danger",
        )
        return RedirectResponse("/admin/users", status.HTTP_302_FOUND)

    session.delete(target)
    session.commit()
    flash(request, f"Raderade {target.username}", "success")
    return RedirectResponse("/admin/users", status.HTTP_302_FOUND)
