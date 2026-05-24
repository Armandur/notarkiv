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


def enrich_person_from_mb(
    session: Session,
    person: Person,
    *,
    mb_artist: dict,
    wikipedia_url: str | None = None,
    biography: str | None = None,
) -> None:
    """Uppdatera Person med data från en MusicBrainz-artist (in-place).

    mb_artist är ett dict från MB-api:t (med fält id, name, sort-name, life-span,
    country). Skapar också en PersonLink för Wikipedia om URL ges och saknas.
    Sparar inte - caller commitar.
    """
    from app.models import PersonLink, PersonLinkKind

    changed = False
    if not person.musicbrainz_artist_id:
        person.musicbrainz_artist_id = mb_artist.get("id")
        changed = True
    sort_name = mb_artist.get("sort-name") or mb_artist.get("sort_name")
    if sort_name and (not person.sort_name or person.sort_name != sort_name):
        person.sort_name = sort_name
        changed = True
    life_span = mb_artist.get("life-span") or mb_artist.get("life_span") or {}
    begin = life_span.get("begin", "") or ""
    end = life_span.get("ended") and life_span.get("end", "") or life_span.get("end", "") or ""
    if begin[:4].isdigit() and not person.birth_year:
        person.birth_year = int(begin[:4])
        changed = True
    if end[:4].isdigit() and not person.death_year:
        person.death_year = int(end[:4])
        changed = True
    if mb_artist.get("country") and not person.country:
        person.country = mb_artist["country"]
        changed = True
    if biography and not person.biography:
        person.biography = biography
        person.biography_source_url = wikipedia_url
        changed = True

    if wikipedia_url:
        existing = session.exec(
            select(PersonLink)
            .where(PersonLink.person_id == person.id)
            .where(PersonLink.kind == PersonLinkKind.WIKIPEDIA)
        ).first()
        if not existing:
            session.add(
                PersonLink(
                    person_id=person.id,
                    url=wikipedia_url,
                    kind=PersonLinkKind.WIKIPEDIA,
                )
            )
            changed = True

    if changed:
        person.updated_at = datetime.utcnow()
        session.add(person)


def all_people_names(session: Session) -> list[str]:
    """Returnera alla person-namn alfabetiskt - för autocomplete-datalist."""
    return [
        p.name for p in session.exec(select(Person).order_by(Person.sort_name)).all()
    ]


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
