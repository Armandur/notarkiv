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
