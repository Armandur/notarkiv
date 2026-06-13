from datetime import datetime, timedelta
from app.utils.dates import now_utc

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, func, select

from app.deps import get_session, require_admin, verify_csrf
from app.models import ScanSession, User
from app.models.scan_session import ScanStatus
from app.tasks import get_pool
from app.templates_setup import flash, render

router = APIRouter(prefix="/admin/jobs", tags=["admin"])


@router.get("")
async def jobs_dashboard(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    """Översikt över skanningsjobb + verktyg att åtgärda fastnade."""
    by_status = dict(
        session.exec(
            select(ScanSession.status, func.count(ScanSession.id))
            .group_by(ScanSession.status)
        ).all()
    )

    # Pending som är äldre än 5 min anses fastnade
    threshold = now_utc() - timedelta(minutes=5)
    stuck = session.exec(
        select(ScanSession)
        .where(ScanSession.status == ScanStatus.PENDING)
        .where(ScanSession.created_at < threshold)
        .where(ScanSession.discarded == False)  # noqa: E712
        .order_by(ScanSession.created_at)
    ).all()

    # Recent failed
    recent_failed = session.exec(
        select(ScanSession)
        .where(ScanSession.status == ScanStatus.FAILED)
        .where(ScanSession.discarded == False)  # noqa: E712
        .order_by(ScanSession.created_at.desc())
        .limit(10)
    ).all()

    return render(
        request,
        "admin/jobs.html",
        {
            "by_status": by_status,
            "stuck": stuck,
            "recent_failed": recent_failed,
        },
        user=user,
    )


@router.post("/requeue-stuck", dependencies=[Depends(verify_csrf)])
async def requeue_stuck(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    """Re-enqueua alla pending-skanningar som är äldre än 5 min."""
    threshold = now_utc() - timedelta(minutes=5)
    stuck = session.exec(
        select(ScanSession)
        .where(ScanSession.status == ScanStatus.PENDING)
        .where(ScanSession.created_at < threshold)
        .where(ScanSession.discarded == False)  # noqa: E712
    ).all()

    if not stuck:
        flash(request, "Inga fastnade skanningar hittades", "info")
        return RedirectResponse("/admin/jobs", status.HTTP_302_FOUND)

    pool = await get_pool()
    count = 0
    for scan in stuck:
        await pool.enqueue_job(
            "extract_metadata_job", scan.id, _job_id=f"requeue-{scan.id}-{int(now_utc().timestamp())}"
        )
        count += 1

    flash(request, f"Återstartade {count} fastnade skanningar", "success")
    return RedirectResponse("/admin/jobs", status.HTTP_302_FOUND)


@router.post("/retry-failed", dependencies=[Depends(verify_csrf)])
async def retry_failed(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    """Re-enqueua alla failed-skanningar (rensar error_message)."""
    failed = session.exec(
        select(ScanSession)
        .where(ScanSession.status == ScanStatus.FAILED)
        .where(ScanSession.discarded == False)  # noqa: E712
    ).all()
    if not failed:
        flash(request, "Inga misslyckade skanningar", "info")
        return RedirectResponse("/admin/jobs", status.HTTP_302_FOUND)

    pool = await get_pool()
    for scan in failed:
        scan.status = ScanStatus.PENDING
        scan.error_message = None
        session.add(scan)
        await pool.enqueue_job(
            "extract_metadata_job", scan.id, _job_id=f"retry-{scan.id}-{int(now_utc().timestamp())}"
        )
    session.commit()
    flash(request, f"Återstartade {len(failed)} misslyckade skanningar", "success")
    return RedirectResponse("/admin/jobs", status.HTTP_302_FOUND)
