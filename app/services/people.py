"""Hjälpare för Person/PieceContributor: find-or-create från namn,
bygg cache-text för FTS, plus MB-berikning.
"""

from datetime import datetime

from sqlmodel import Session, select

from app.models import ContributorRole, Person, PieceContributor


def derive_sort_name(name: str) -> str:
    """Konvertera "Felix Mendelssohn" till "Mendelssohn, Felix" som sort_name.

    Behåller en-ords-namn ("Vivaldi") oförändrade. Hanterar enkla efterföljande
    siffror, romerska siffror eller mellannamn lika - sista ordet blir efternamn.
    """
    name = name.strip()
    if not name:
        return name
    parts = name.split()
    if len(parts) == 1:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def find_or_create_person(
    session: Session,
    name: str,
    *,
    musicbrainz_artist_id: str | None = None,
) -> Person | None:
    """Hitta befintlig Person eller skapa ny. Match på name (case-insensitive)
    eller på MBID om det finns."""
    name = name.strip()
    if not name:
        return None

    if musicbrainz_artist_id:
        existing = session.exec(
            select(Person).where(Person.musicbrainz_artist_id == musicbrainz_artist_id)
        ).first()
        if existing:
            return existing

    existing = session.exec(
        select(Person).where(Person.name.ilike(name))
    ).first()
    if existing:
        if musicbrainz_artist_id and not existing.musicbrainz_artist_id:
            existing.musicbrainz_artist_id = musicbrainz_artist_id
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        return existing

    person = Person(
        name=name,
        sort_name=derive_sort_name(name),
        musicbrainz_artist_id=musicbrainz_artist_id,
    )
    session.add(person)
    session.flush()
    return person


def replace_contributors(
    session: Session,
    piece_id: int,
    *,
    composers: list[str] | None = None,
    arrangers: list[str] | None = None,
    lyricists: list[str] | None = None,
) -> str:
    """Sätt om alla bidragsgivare för en not. Returnerar contributors_cache."""
    existing_links = session.exec(
        select(PieceContributor).where(PieceContributor.piece_id == piece_id)
    ).all()
    for link in existing_links:
        session.delete(link)
    session.flush()

    role_lists = [
        (ContributorRole.COMPOSER, composers or []),
        (ContributorRole.ARRANGER, arrangers or []),
        (ContributorRole.LYRICIST, lyricists or []),
    ]

    cache_parts: list[str] = []
    for role, names in role_lists:
        for i, raw in enumerate(names):
            name = raw.strip()
            if not name:
                continue
            person = find_or_create_person(session, name)
            if not person:
                continue
            session.add(
                PieceContributor(
                    piece_id=piece_id,
                    person_id=person.id,
                    role=role,
                    sort_order=i,
                )
            )
            cache_parts.append(f"{person.name} ({role.value})")

    return "; ".join(cache_parts)


def collect_contributors(
    session: Session, piece_id: int
) -> dict[ContributorRole, list[Person]]:
    """Hämta bidragsgivare grupperade per roll, sorterade efter sort_order."""
    rows = session.exec(
        select(PieceContributor, Person)
        .join(Person, Person.id == PieceContributor.person_id)
        .where(PieceContributor.piece_id == piece_id)
        .order_by(PieceContributor.role, PieceContributor.sort_order)
    ).all()

    out: dict[ContributorRole, list[Person]] = {}
    for pc, person in rows:
        out.setdefault(pc.role, []).append(person)
    return out


def parse_names_field(value: str | None) -> list[str]:
    """Parsa en text-input som kan innehålla flera namn separerade med ';' eller '&'.

    Vanliga delimiters: semikolon, ampersand, " och ". Komma används inte
    eftersom det förekommer i sort_name-format ("Mendelssohn, Felix").
    """
    if not value:
        return []
    import re

    parts = re.split(r"[;&]| och ", value)
    return [p.strip() for p in parts if p.strip()]
