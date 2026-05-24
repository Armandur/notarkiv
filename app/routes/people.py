from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, func, select

from app.deps import get_session, require_admin, require_auth, require_editor, verify_csrf
from app.models import ContributorRole, Person, Piece, PieceContributor, User
from app.services.people import derive_sort_name
from app.templates_setup import flash, render

router = APIRouter(prefix="/people", tags=["people"])


@router.get("")
async def list_people(
    request: Request,
    q: str | None = None,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    stmt = select(Person).order_by(Person.sort_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Person.sort_name.ilike(like) | Person.name.ilike(like))
    people = session.exec(stmt).all()

    # Räkna antal noter per person för översikt
    counts = dict(
        session.exec(
            select(PieceContributor.person_id, func.count(PieceContributor.piece_id))
            .group_by(PieceContributor.person_id)
        ).all()
    )

    return render(
        request,
        "people/list.html",
        {"people": people, "counts": counts, "q": q or ""},
        user=user,
    )


@router.get("/{person_id}")
async def person_detail(
    request: Request,
    person_id: int,
    user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    rows = session.exec(
        select(PieceContributor, Piece)
        .join(Piece, Piece.id == PieceContributor.piece_id)
        .where(PieceContributor.person_id == person_id)
        .order_by(PieceContributor.role, Piece.title)
    ).all()

    by_role: dict[ContributorRole, list[Piece]] = {}
    for pc, piece in rows:
        by_role.setdefault(pc.role, []).append(piece)

    return render(
        request,
        "people/detail.html",
        {"person": person, "pieces_by_role": by_role},
        user=user,
    )


@router.get("/search/json")
async def search_people(
    request: Request,
    q: str = "",
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    query = q.strip()
    results: list[Person] = []
    if query:
        like = f"%{query}%"
        results = session.exec(
            select(Person)
            .where(Person.name.ilike(like) | Person.sort_name.ilike(like))
            .order_by(Person.sort_name)
            .limit(10)
        ).all()

    return render(
        request,
        "people/_search_results.html",
        {"results": results, "query": query},
        user=user,
    )


@router.post("/{person_id}", dependencies=[Depends(verify_csrf)])
async def update_person(
    request: Request,
    person_id: int,
    name: str = Form(...),
    sort_name: str | None = Form(None),
    birth_year: str | None = Form(None),
    death_year: str | None = Form(None),
    biography: str | None = Form(None),
    wikipedia_url: str | None = Form(None),
    musicbrainz_artist_id: str | None = Form(None),
    user: User = Depends(require_editor),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    person.name = name.strip()
    person.sort_name = (sort_name or "").strip() or derive_sort_name(person.name)
    person.birth_year = int(birth_year) if birth_year and birth_year.isdigit() else None
    person.death_year = int(death_year) if death_year and death_year.isdigit() else None
    person.biography = (biography or "").strip() or None
    person.wikipedia_url = (wikipedia_url or "").strip() or None
    person.musicbrainz_artist_id = (musicbrainz_artist_id or "").strip() or None

    session.add(person)
    session.commit()

    # Uppdatera contributors_cache på alla noter som denna person bidrar till
    from app.services.people import replace_contributors

    contrib_links = session.exec(
        select(PieceContributor.piece_id).where(PieceContributor.person_id == person_id)
    ).all()
    for pid in set(contrib_links):
        # Bygg om cachen via current state - en enklare väg är att läsa alla
        # bidrag för den noten och formatera om
        contrib_rows = session.exec(
            select(PieceContributor, Person)
            .join(Person, Person.id == PieceContributor.person_id)
            .where(PieceContributor.piece_id == pid)
            .order_by(PieceContributor.role, PieceContributor.sort_order)
        ).all()
        cache = "; ".join(f"{p.name} ({pc.role.value})" for pc, p in contrib_rows)
        piece = session.get(Piece, pid)
        if piece:
            piece.contributors_cache = cache or None
            session.add(piece)
    session.commit()

    flash(request, f"Uppdaterade {person.name}", "success")
    return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)


@router.post("/{person_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_person(
    request: Request,
    person_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404)

    in_use = session.exec(
        select(PieceContributor).where(PieceContributor.person_id == person_id).limit(1)
    ).first()
    if in_use:
        flash(request, f"{person.name} är kopplad till noter och kan inte raderas", "danger")
        return RedirectResponse(f"/people/{person_id}", status.HTTP_302_FOUND)

    session.delete(person)
    session.commit()
    flash(request, f"Raderade {person.name}", "success")
    return RedirectResponse("/people", status.HTTP_302_FOUND)
