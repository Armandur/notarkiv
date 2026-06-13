"""Hjälpare för Person/PieceContributor: find-or-create från namn,
bygg cache-text för FTS, plus MB-berikning.
"""

from datetime import datetime
from app.utils.dates import now_utc

from sqlmodel import Session, select

from app.models import ContributorRole, Person, PieceContributor


def parse_partial_date(text: str | None) -> tuple[int | None, int | None, int | None]:
    """Parsa "YYYY", "YYYY-MM" eller "YYYY-MM-DD" till (year, month, day).

    Returnerar (None, None, None) för tom/ogiltig input. Tolererar slash istället
    för bindestreck. Validerar månader 1-12, dagar 1-31 (utan kalenderkontroll).
    """
    if not text:
        return (None, None, None)
    text = text.strip().replace("/", "-")
    if not text:
        return (None, None, None)
    parts = text.split("-")
    try:
        year = int(parts[0]) if parts[0] else None
    except ValueError:
        return (None, None, None)
    if year is None:
        return (None, None, None)
    month = None
    day = None
    if len(parts) >= 2 and parts[1]:
        try:
            month = int(parts[1])
            if not 1 <= month <= 12:
                month = None
        except ValueError:
            month = None
    if month and len(parts) >= 3 and parts[2]:
        try:
            day = int(parts[2])
            if not 1 <= day <= 31:
                day = None
        except ValueError:
            day = None
    return (year, month, day)


def format_partial_date(year: int | None, month: int | None, day: int | None) -> str:
    """Formatera till samma sträng man kan skriva in."""
    if not year:
        return ""
    if month and day:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if month:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


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
    sort_name_override: str | None = None,
) -> Person | None:
    """Hitta befintlig Person eller skapa ny. Match på name (case-insensitive)
    eller på MBID om det finns. sort_name_override sätter sort_name på en
    nyskapad person (eller uppdaterar befintlig om dess sort_name saknas)."""
    name = name.strip()
    if not name:
        return None
    override = (sort_name_override or "").strip() or None

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
            existing.updated_at = now_utc()
            session.add(existing)
        return existing

    person = Person(
        name=name,
        sort_name=override or derive_sort_name(name),
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
    """Sätt om alla bidragsgivare för en not. Returnerar contributors_cache.
    Namnen kommer från Tom Select tag-fält. Nya personer får sort_name via
    derive_sort_name; befintliga behåller sitt sort_name."""
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
    end = life_span.get("end", "") or ""
    by, bm, bd = parse_partial_date(begin)
    if by and not person.birth_year:
        person.birth_year = by
        person.birth_month = bm
        person.birth_day = bd
        changed = True
    dy, dm, dd = parse_partial_date(end)
    if dy and not person.death_year:
        person.death_year = dy
        person.death_month = dm
        person.death_day = dd
        changed = True
    if mb_artist.get("country") and not person.country:
        person.country = mb_artist["country"]
        changed = True

    # Wikidata-Q-id via MB:s url-relationer - bidirektionell utfyllnad
    if not person.wikidata_id:
        from app.services.musicbrainz import extract_wikidata_url, wikidata_id_from_url

        wd_url = extract_wikidata_url(mb_artist)
        wd_qid = wikidata_id_from_url(wd_url)
        if wd_qid:
            person.wikidata_id = wd_qid
            changed = True
    if biography and not person.biography:
        person.biography = biography
        person.biography_source_url = wikipedia_url
        person.biography_fetched_at = now_utc()
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
        person.updated_at = now_utc()
        session.add(person)


async def enqueue_enrich_for_piece(session: Session, piece_id: int) -> int:
    """Kö:a MB-berikning för alla bidragsgivare till en piece som saknar MBID.
    Returnerar antal jobb skickade. Tål Redis-fel - returnerar 0 då."""
    rows = session.exec(
        select(Person)
        .join(PieceContributor, PieceContributor.person_id == Person.id)
        .where(PieceContributor.piece_id == piece_id)
        .where(Person.musicbrainz_artist_id.is_(None))
        .where(Person.wikidata_id.is_(None))
    ).all()
    if not rows:
        return 0

    try:
        from app.tasks import get_pool

        pool = await get_pool()
    except Exception as exc:
        from loguru import logger

        logger.warning("Kunde inte ansluta till Redis för person-berikning: {}", exc)
        return 0

    seen: set[int] = set()
    for p in rows:
        if p.id in seen:
            continue
        seen.add(p.id)
        await pool.enqueue_job("enrich_person_job", p.id)
    return len(seen)


def all_people_names(session: Session) -> list[str]:
    """Returnera alla person-namn alfabetiskt - för autocomplete-datalist."""
    return [
        p.name for p in session.exec(select(Person).order_by(Person.sort_name)).all()
    ]


def all_people_for_autocomplete(session: Session) -> list[dict]:
    """Lista med {name, label} för datalist. label innehåller även namnet
    eftersom Chrome i vissa kontexter visar bara label - inte value - och
    vi vill att namnet alltid är synligt."""
    from sqlalchemy import func as sqlf

    counts = dict(
        session.exec(
            select(PieceContributor.person_id, sqlf.count(PieceContributor.id))
            .group_by(PieceContributor.person_id)
        ).all()
    )
    out: list[dict] = []
    for p in session.exec(select(Person).order_by(Person.sort_name)).all():
        bits: list[str] = [p.name]
        if p.birth_year or p.death_year:
            bits.append(f"{p.birth_year or '?'}-{p.death_year or ''}".rstrip("-"))
        n = counts.get(p.id, 0)
        if n:
            bits.append(f"{n} not" if n == 1 else f"{n} noter")
        else:
            bits.append("ingen not än")
        out.append({"name": p.name, "label": " · ".join(bits)})
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


