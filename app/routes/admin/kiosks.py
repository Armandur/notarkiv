"""Admin-CRUD för Kiosk-enheter."""

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, verify_csrf
from app.models import Kiosk, StorageLocation, User
from app.templates_setup import flash, render

router = APIRouter(prefix="/admin/kiosks", tags=["admin"])


@router.get("")
async def list_kiosks(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    kiosks = session.exec(select(Kiosk).order_by(Kiosk.name)).all()
    locations = session.exec(select(StorageLocation).order_by(StorageLocation.name)).all()
    return render(
        request,
        "admin/kiosks.html",
        {"kiosks": kiosks, "locations": locations},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_kiosk(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    location_id: str | None = Form(None),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    name = name.strip()
    if not name:
        flash(request, "Namn krävs", "danger")
        return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)
    if session.exec(select(Kiosk).where(Kiosk.name == name)).first():
        flash(request, f"En kiosk med namnet '{name}' finns redan", "danger")
        return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)
    loc_id = int(location_id) if location_id and location_id.isdigit() else None
    session.add(
        Kiosk(
            name=name,
            description=(description or "").strip() or None,
            location_id=loc_id,
        )
    )
    session.commit()
    flash(request, f"Skapade kiosken '{name}'", "success")
    return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)


@router.post("/{kiosk_id}/update", dependencies=[Depends(verify_csrf)])
async def update_kiosk(
    request: Request,
    kiosk_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    location_id: str | None = Form(None),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    kiosk = session.get(Kiosk, kiosk_id)
    if not kiosk:
        raise HTTPException(404)
    new_name = name.strip()
    if new_name != kiosk.name:
        clash = session.exec(
            select(Kiosk).where(Kiosk.name == new_name).where(Kiosk.id != kiosk_id)
        ).first()
        if clash:
            flash(request, f"En annan kiosk heter redan '{new_name}'", "danger")
            return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)
    kiosk.name = new_name
    kiosk.description = (description or "").strip() or None
    kiosk.location_id = int(location_id) if location_id and location_id.isdigit() else None
    session.add(kiosk)
    session.commit()
    flash(request, f"Uppdaterade '{kiosk.name}'", "success")
    return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)


@router.post("/{kiosk_id}/regenerate-token", dependencies=[Depends(verify_csrf)])
async def regenerate_token(
    request: Request,
    kiosk_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    kiosk = session.get(Kiosk, kiosk_id)
    if not kiosk:
        raise HTTPException(404)
    kiosk.access_token = secrets.token_hex(16)
    session.add(kiosk)
    session.commit()
    flash(
        request,
        f"Genererade ny token för '{kiosk.name}' - aktivera om kioskenheten",
        "warning",
    )
    return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)


@router.post("/{kiosk_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_kiosk(
    request: Request,
    kiosk_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    kiosk = session.get(Kiosk, kiosk_id)
    if not kiosk:
        raise HTTPException(404)
    name = kiosk.name
    session.delete(kiosk)
    session.commit()
    flash(request, f"Raderade kiosken '{name}'", "info")
    return RedirectResponse("/admin/kiosks", status.HTTP_302_FOUND)
