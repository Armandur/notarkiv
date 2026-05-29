"""Routes för Publisher-entiteter - lista, detalj, redigera, radera."""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import Piece, Publisher, User
from app.templates_setup import flash, render

router = APIRouter(prefix="/publishers", tags=["publishers"])


@router.get("")
async def list_publishers(
    request: Request,
    q: str = "",
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    stmt = select(Publisher)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            (Publisher.name.ilike(like)) | (Publisher.sort_name.ilike(like))
        )
    pubs = list(session.exec(stmt.order_by(Publisher.sort_name)).all())
    # Räkna antal noter per publisher
    counts: dict[int, int] = {}
    for p in pubs:
        counts[p.id] = len(
            session.exec(select(Piece).where(Piece.publisher_id == p.id)).all()
        )
    return render(
        request,
        "publishers/list.html",
        {"publishers": pubs, "counts": counts, "q": q},
        user=user,
    )


@router.get("/{publisher_id}")
async def publisher_detail(
    request: Request,
    publisher_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    pieces = list(
        session.exec(
            select(Piece)
            .where(Piece.publisher_id == publisher_id)
            .order_by(Piece.title)
        ).all()
    )
    return render(
        request,
        "publishers/detail.html",
        {"pub": pub, "pieces": pieces},
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_publisher(
    request: Request,
    name: str = Form(...),
    sort_name: str | None = Form(None),
    country: str | None = Form(None),
    website_url: str | None = Form(None),
    description: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    clean = name.strip()
    if not clean:
        flash(request, "Namnet får inte vara tomt", "danger")
        return RedirectResponse("/publishers", status.HTTP_302_FOUND)
    existing = session.exec(select(Publisher).where(Publisher.name == clean)).first()
    if existing:
        flash(request, f'Förlag "{clean}" finns redan', "warning")
        return RedirectResponse(f"/publishers/{existing.id}", status.HTTP_302_FOUND)
    pub = Publisher(
        name=clean,
        sort_name=(sort_name or "").strip() or clean,
        country=(country or "").strip() or None,
        website_url=(website_url or "").strip() or None,
        description=(description or "").strip() or None,
    )
    session.add(pub)
    session.commit()
    session.refresh(pub)
    flash(request, f'Förlag "{clean}" skapad', "success")
    return RedirectResponse(f"/publishers/{pub.id}", status.HTTP_302_FOUND)


@router.post("/{publisher_id}/update", dependencies=[Depends(verify_csrf)])
async def update_publisher(
    request: Request,
    publisher_id: int,
    name: str = Form(...),
    sort_name: str | None = Form(None),
    country: str | None = Form(None),
    website_url: str | None = Form(None),
    description: str | None = Form(None),
    musicbrainz_label_id: str | None = Form(None),
    wikidata_id: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    clean = name.strip()
    if not clean:
        flash(request, "Namnet får inte vara tomt", "danger")
        return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)
    if clean != pub.name:
        clash = session.exec(
            select(Publisher)
            .where(Publisher.name == clean)
            .where(Publisher.id != publisher_id)
        ).first()
        if clash:
            flash(request, f'Ett annat förlag heter redan "{clean}"', "warning")
            return RedirectResponse(
                f"/publishers/{publisher_id}", status.HTTP_302_FOUND
            )
    pub.name = clean
    pub.sort_name = (sort_name or "").strip() or clean
    pub.country = (country or "").strip() or None
    pub.website_url = (website_url or "").strip() or None
    pub.description = (description or "").strip() or None
    pub.musicbrainz_label_id = (musicbrainz_label_id or "").strip() or None
    pub.wikidata_id = (wikidata_id or "").strip() or None
    pub.updated_at = datetime.utcnow()
    session.add(pub)
    session.commit()
    flash(request, "Förlag uppdaterat", "success")
    return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)


@router.post("/{publisher_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_publisher(
    request: Request,
    publisher_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    pub = session.get(Publisher, publisher_id)
    if not pub:
        raise HTTPException(404)
    count = len(
        session.exec(select(Piece).where(Piece.publisher_id == publisher_id)).all()
    )
    if count > 0:
        flash(
            request,
            f'Kan inte radera "{pub.name}" - {count} not(er) refererar till förlaget',
            "warning",
        )
        return RedirectResponse(f"/publishers/{publisher_id}", status.HTTP_302_FOUND)
    name = pub.name
    session.delete(pub)
    session.commit()
    flash(request, f'Förlag "{name}" raderat', "info")
    return RedirectResponse("/publishers", status.HTTP_302_FOUND)
