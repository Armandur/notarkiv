"""Helpers för Publisher-entiteter: find-or-create + namnnormalisering."""

from datetime import datetime

from sqlmodel import Session, select

from app.models import Publisher


def _normalize(name: str) -> str:
    """Normalisera namn för dubblettkoll - strippa whitespace,
    lowercase, kollapsera multipla mellanslag och ta bort vanliga
    företagssuffix så 'Verbum' / 'Verbum AB' / 'Verbum Förlag' matchar."""
    n = " ".join(name.strip().split()).lower()
    # Vanliga suffix
    for suffix in (" ab", " förlag", " musikförlag", " edition", " gmbh", " inc", " ltd"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].rstrip()
    return n


def find_or_create_publisher(session: Session, name: str | None) -> Publisher | None:
    """Hitta en existerande publisher (case-insensitiv + suffix-tolerant)
    eller skapa en ny med det givna namnet. Tomma/None returnerar None."""
    if not name:
        return None
    clean = name.strip()
    if not clean:
        return None

    norm = _normalize(clean)
    # Hämta alla och matcha normaliserat - billigare än att bygga
    # SQL-funktioner. Vid 200-500 publishers är detta trivialt.
    all_pubs = session.exec(select(Publisher)).all()
    for p in all_pubs:
        if _normalize(p.name) == norm:
            return p

    # Inget match - skapa ny
    pub = Publisher(name=clean, sort_name=clean)
    session.add(pub)
    session.commit()
    session.refresh(pub)
    return pub


def all_publishers_for_autocomplete(session: Session) -> list[dict]:
    """Returnera alla publishers för datalist-autocomplete."""
    pubs = session.exec(select(Publisher).order_by(Publisher.sort_name)).all()
    return [{"id": p.id, "name": p.name, "country": p.country or ""} for p in pubs]


def enrich_publisher_from_mb(
    session: Session,
    publisher: Publisher,
    mb_label: dict,
    wikipedia_url: str | None = None,
    description: str | None = None,
) -> None:
    """Applicera fält från en MB Label-träff på en publisher. Skriver
    bara över tomma fält - användarens redigeringar bevaras."""
    from app.services.musicbrainz import extract_wikidata_url

    changed = False
    if not publisher.musicbrainz_label_id:
        publisher.musicbrainz_label_id = mb_label.get("id")
        changed = True
    if not publisher.country and mb_label.get("country"):
        publisher.country = mb_label["country"]
        changed = True
    # MB:s "name" och "sort-name"
    if mb_label.get("sort-name") and publisher.sort_name == publisher.name:
        publisher.sort_name = mb_label["sort-name"]
        changed = True
    # Wikidata-ID från URL-rels
    if not publisher.wikidata_id:
        wd_url = extract_wikidata_url(mb_label)
        if wd_url:
            # https://www.wikidata.org/wiki/Q12345 → Q12345
            qid = wd_url.rstrip("/").rsplit("/", 1)[-1]
            if qid.startswith("Q"):
                publisher.wikidata_id = qid
                changed = True
    # Hemsida från officiella URL-rels
    if not publisher.website_url:
        for rel in mb_label.get("relations", []):
            if rel.get("type") == "official homepage":
                url = (rel.get("url") or {}).get("resource") or ""
                if url:
                    publisher.website_url = url
                    changed = True
                    break
    # Beskrivning från Wikipedia om vi inte har egen
    if not publisher.description and description:
        publisher.description = description
        changed = True
    if changed:
        publisher.enriched_at = datetime.utcnow()
        publisher.updated_at = datetime.utcnow()
        session.add(publisher)
