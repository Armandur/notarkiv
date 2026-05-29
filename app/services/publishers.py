"""Helpers för Publisher-entiteter: find-or-create + namnnormalisering."""

from datetime import datetime

from sqlmodel import Session, select

from app.models import Publisher, PublisherLink, PublisherLinkKind


_COMPANY_AFFIXES = ("ab", "förlag", "musikförlag", "edition", "gmbh", "inc", "ltd", "co")


def _normalize(name: str) -> str:
    """Normalisera namn för dubblettkoll - lowercase, strippa
    skiljetecken (punkter, bindestreck, komma), kollapsera mellanslag
    och ta bort företagsaffix från både början och slutet.

    Gör att 'A.-B. Nordiska Musikförlaget', 'AB Nordiska Musikförlaget'
    och 'Nordiska Musikförlaget AB' matchar som samma. Plus klassiska
    'Verbum' = 'Verbum AB' = 'Verbum Förlag'."""
    import re

    n = name.lower().strip()
    # Skiljetecken till mellanslag - så 'a.-b.' blir 'a b'
    n = re.sub(r"[.,\-_/]+", " ", n)
    # Kollapsera mellanslag
    n = " ".join(n.split())
    # Slå ihop isolerat 'a b' till 'ab' (vanligt mönster för förkortat
    # AB i förlagsnamn: "A.-B. Nordiska" → "a b nordiska" → "ab nordiska")
    n = re.sub(r"\ba b\b", "ab", n)
    # Loopa tills inget affix kan strippas - täcker både prefix och
    # suffix, samt staplade affix ('AB Förlag X' eller 'X AB Förlag')
    changed = True
    while changed:
        changed = False
        for affix in _COMPANY_AFFIXES:
            if n.startswith(affix + " "):
                n = n[len(affix) + 1:]
                changed = True
            if n.endswith(" " + affix):
                n = n[: -(len(affix) + 1)]
                changed = True
    return n.strip()


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


def _classify_link(url: str) -> PublisherLinkKind:
    u = url.lower()
    if "wikipedia.org" in u:
        return PublisherLinkKind.WIKIPEDIA
    if "wikidata.org" in u:
        return PublisherLinkKind.WIKIDATA
    if "musicbrainz.org" in u:
        return PublisherLinkKind.MUSICBRAINZ
    if "imslp.org" in u:
        return PublisherLinkKind.IMSLP
    return PublisherLinkKind.OFFICIAL


def _add_link_if_new(
    session: Session,
    publisher: Publisher,
    url: str,
    kind: PublisherLinkKind,
    label: str | None = None,
) -> None:
    """Lägg en länk om samma URL inte redan finns på publishern."""
    if not url:
        return
    existing = session.exec(
        select(PublisherLink)
        .where(PublisherLink.publisher_id == publisher.id)
        .where(PublisherLink.url == url)
    ).first()
    if existing:
        return
    session.add(PublisherLink(
        publisher_id=publisher.id, url=url, kind=kind, label=label
    ))


def enrich_publisher_from_mb(
    session: Session,
    publisher: Publisher,
    mb_label: dict,
    wikipedia_url: str | None = None,
    description: str | None = None,
) -> dict:
    """Applicera fält från en MB Label-träff på en publisher. Användaren
    har aktivt valt träffen, så MB:s namn + sort-name appliceras som
    default. Returnerar en dict med 'old_name' och 'old_sort_name' om
    de bytts ut - så caller kan visa det för användaren."""
    from app.services.musicbrainz import extract_wikidata_url

    changes = {}
    publisher.musicbrainz_label_id = mb_label.get("id") or publisher.musicbrainz_label_id

    mb_name = (mb_label.get("name") or "").strip()
    mb_sort = (mb_label.get("sort-name") or "").strip()

    # Spara gamla värden om de ändras (för flash-meddelande)
    if mb_name and mb_name != publisher.name:
        clash = session.exec(
            select(Publisher)
            .where(Publisher.name == mb_name)
            .where(Publisher.id != publisher.id)
        ).first()
        if not clash:
            changes["old_name"] = publisher.name
            publisher.name = mb_name
    if mb_sort and mb_sort != publisher.sort_name:
        changes["old_sort_name"] = publisher.sort_name
        publisher.sort_name = mb_sort

    if mb_label.get("country"):
        publisher.country = mb_label["country"]

    # Wikidata-ID från URL-rels
    wd_url = extract_wikidata_url(mb_label)
    if wd_url:
        qid = wd_url.rstrip("/").rsplit("/", 1)[-1]
        if qid.startswith("Q"):
            publisher.wikidata_id = qid

    # Hemsida från officiella URL-rels (skriv över bara om vi inte har)
    if not publisher.website_url:
        for rel in mb_label.get("relations", []):
            if rel.get("type") == "official homepage":
                url = (rel.get("url") or {}).get("resource") or ""
                if url:
                    publisher.website_url = url
                    break

    # Wikipedia-beskrivning + källattribution
    if description and (not publisher.description or publisher.description_source_url):
        publisher.description = description
        publisher.description_source_url = wikipedia_url

    publisher.enriched_at = datetime.utcnow()
    publisher.updated_at = datetime.utcnow()
    session.add(publisher)
    session.flush()  # ge publisher.id om ny

    # Skapa länkar för alla URL-rels (Wikipedia, IMSLP, officiella etc.)
    for rel in mb_label.get("relations", []):
        url = (rel.get("url") or {}).get("resource") or ""
        if not url:
            continue
        rel_type = rel.get("type", "")
        if rel_type == "official homepage":
            kind = PublisherLinkKind.OFFICIAL
        elif rel_type == "wikidata":
            kind = PublisherLinkKind.WIKIDATA
        elif rel_type == "wikipedia":
            kind = PublisherLinkKind.WIKIPEDIA
        elif rel_type == "IMSLP":
            kind = PublisherLinkKind.IMSLP
        else:
            kind = _classify_link(url)
        _add_link_if_new(session, publisher, url, kind, label=rel_type or None)

    # Wikipedia-URL från get_wikipedia_url (om inte redan med via rels)
    if wikipedia_url:
        _add_link_if_new(session, publisher, wikipedia_url, PublisherLinkKind.WIKIPEDIA)

    return changes
