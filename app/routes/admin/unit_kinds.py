from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, verify_csrf
from app.models import StorageUnit, UnitKind, User
from app.templates_setup import flash, render

router = APIRouter(prefix="/admin/unit-kinds", tags=["admin"])


@router.get("")
async def list_unit_kinds(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    from sqlalchemy import func as sqlf

    kinds = session.exec(select(UnitKind).order_by(UnitKind.name)).all()
    counts = dict(
        session.exec(
            select(StorageUnit.kind_id, sqlf.count(StorageUnit.id))
            .where(StorageUnit.kind_id.is_not(None))
            .group_by(StorageUnit.kind_id)
        ).all()
    )
    return render(
        request,
        "admin/unit_kinds.html",
        {"kinds": kinds, "counts": counts},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_unit_kind(
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    name = name.strip()
    if not name:
        flash(request, "Namn krävs", "danger")
        return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)
    if session.exec(select(UnitKind).where(UnitKind.name == name)).first():
        flash(request, f"Typen '{name}' finns redan", "danger")
        return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)
    session.add(UnitKind(name=name))
    session.commit()
    flash(request, f"Skapade '{name}'", "success")
    return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)


@router.post("/{kind_id}/update", dependencies=[Depends(verify_csrf)])
async def update_unit_kind(
    request: Request,
    kind_id: int,
    name: str = Form(...),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    kind = session.get(UnitKind, kind_id)
    if not kind:
        raise HTTPException(404)
    new_name = name.strip()
    if not new_name:
        flash(request, "Namn krävs", "danger")
        return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)
    if new_name != kind.name:
        clash = session.exec(
            select(UnitKind)
            .where(UnitKind.name == new_name)
            .where(UnitKind.id != kind_id)
        ).first()
        if clash:
            flash(request, f"En annan typ heter redan '{new_name}'", "danger")
            return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)
    kind.name = new_name
    session.add(kind)
    session.commit()
    flash(request, f"Uppdaterade '{kind.name}'", "success")
    return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)


@router.post("/{kind_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_unit_kind(
    request: Request,
    kind_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    kind = session.get(UnitKind, kind_id)
    if not kind:
        raise HTTPException(404)
    in_use = session.exec(
        select(StorageUnit).where(StorageUnit.kind_id == kind_id).limit(1)
    ).first()
    if in_use:
        flash(
            request,
            f"'{kind.name}' används av minst en enhet - byt typ där först",
            "danger",
        )
        return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)
    name = kind.name
    session.delete(kind)
    session.commit()
    flash(request, f"Raderade '{name}'", "success")
    return RedirectResponse("/admin/unit-kinds", status.HTTP_302_FOUND)
