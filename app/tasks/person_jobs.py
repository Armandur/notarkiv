"""Bakgrundsjobb: berika en Person via MusicBrainz + Wikipedia.

Trigras när en ny Person skapas (via skanning eller manuell inläggning).
Best-effort - om MB inte hittar tydlig match lämnas personen oberikad.
"""

from loguru import logger
from rapidfuzz import fuzz
from sqlmodel import Session

from app.db import engine
from app.models import Person
from app.services.musicbrainz import (
    commons_file_to_thumb_url,
    download_image_bytes,
    extract_image_url,
    fetch_wikipedia_summary,
    get_client,
    get_wikipedia_url,
)
from app.services.people import enrich_person_from_mb
from app.utils.images import save_uploaded_cover


# Minimitröskel för att auto-acceptera en MB-träff (0-100). Manuell modal
# har lägre tröskel - här vill vi undvika falska matchningar.
_AUTO_MATCH_THRESHOLD = 88


async def enrich_person_job(ctx: dict, person_id: int) -> dict:
    """Sök MB för en person, ta bästa träff över tröskeln, berika in-place."""
    with Session(engine) as session:
        person = session.get(Person, person_id)
        if not person:
            return {"status": "missing"}
        if person.musicbrainz_artist_id:
            return {"status": "already_enriched", "person_id": person_id}
        name = person.name

    try:
        client = get_client()
        results = await client.search_artist(name)
    except Exception as exc:
        logger.warning("MB search_artist misslyckades för {}: {}", name, exc)
        return {"status": "search_failed", "error": str(exc)}

    best = _pick_best_match(name, results)
    if not best:
        logger.info("Ingen auto-match för '{}' (kandidater: {})", name, len(results))
        return {"status": "no_match", "candidates": len(results)}

    try:
        artist = await client.get_artist_with_urls(best["id"])
    except Exception as exc:
        logger.warning("MB get_artist_with_urls misslyckades: {}", exc)
        return {"status": "fetch_failed", "error": str(exc)}

    if not artist:
        return {"status": "no_artist"}

    wiki_url = await get_wikipedia_url(artist)
    wiki_bio = await fetch_wikipedia_summary(wiki_url) if wiki_url else None
    portrait_path = await _maybe_download_portrait(artist)

    with Session(engine) as session:
        person = session.get(Person, person_id)
        if not person:
            return {"status": "missing"}

        if portrait_path and not person.portrait_image_path:
            person.portrait_image_path = portrait_path["path"]
            person.portrait_source_url = portrait_path["source_url"]

        enrich_person_from_mb(
            session,
            person,
            mb_artist=artist,
            wikipedia_url=wiki_url,
            biography=wiki_bio,
        )
        session.commit()

    logger.info("Berikade Person {} ({}) via MB", person_id, name)
    return {"status": "enriched", "person_id": person_id, "mbid": artist["id"]}


def _pick_best_match(name: str, results: list[dict]) -> dict | None:
    """Välj första kandidaten som matchar tillräckligt bra på namn.

    Endast Person-typ accepteras (filtrerar bort grupper/orkestrar).
    Score baseras på fuzz mellan input-namn och kandidatens name + sort-name.
    """
    if not results:
        return None
    name_l = name.lower()
    best_score = 0
    best: dict | None = None
    for r in results:
        if r.get("type") and r["type"].lower() != "person":
            continue
        score = max(
            fuzz.ratio(name_l, (r.get("name") or "").lower()),
            fuzz.ratio(name_l, (r.get("sort-name") or "").lower()),
        )
        if score > best_score:
            best_score = score
            best = r
    if best_score >= _AUTO_MATCH_THRESHOLD:
        return best
    return None


async def _maybe_download_portrait(artist: dict) -> dict | None:
    """Försök ladda ner porträtt via MB:s image-relation (Commons)."""
    image_page_url = extract_image_url(artist)
    if not image_page_url:
        return None
    thumb_url = commons_file_to_thumb_url(image_page_url, width=600)
    if not thumb_url:
        return None
    img_bytes = await download_image_bytes(thumb_url)
    if not img_bytes:
        return None
    try:
        rel_path = save_uploaded_cover(img_bytes)
    except Exception as exc:
        logger.warning("Kunde inte spara porträtt: {}", exc)
        return None
    return {"path": rel_path, "source_url": image_page_url}
