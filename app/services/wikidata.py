"""Wikidata-klient: sök personer, hämta entity, extrahera metadata.

Wikidata fyller två luckor i MusicBrainz-flödet:
1. Personer som inte är registrerade i MB (t.ex. äldre kyrkomusiker)
2. Korslänkning - om vi har Q-id kan vi följa P434 till MBID och P18 till bild.

Designprincip: keep small, återanvänd musicbrainz.py-helpers där möjligt.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from loguru import logger

from app.services.musicbrainz import get_musicbrainz_user_agent

SEARCH_URL = "https://www.wikidata.org/w/api.php"
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

# Wikidata-property-IDs vi använder
P_INSTANCE_OF = "P31"
P_HUMAN = "Q5"
P_MBID = "P434"           # MusicBrainz artist ID
P_BIRTH_DATE = "P569"
P_DEATH_DATE = "P570"
P_COUNTRY_OF_CITIZENSHIP = "P27"
P_IMAGE = "P18"
P_OCCUPATION = "P106"


async def search_persons(query: str, lang: str = "sv", limit: int = 10) -> list[dict]:
    """Sök efter personer på Wikidata. Använder wbsearchentities + filtrerar
    träffar via instance-of-human-claim (kräver en till entity-fetch per
    träff, men vi begränsar till `limit` och cachar inte än).

    Returnerar list[{qid, label, description, wikipedia_url}]."""
    if not query.strip():
        return []

    params = {
        "action": "wbsearchentities",
        "search": query.strip(),
        "language": lang,
        "uselang": lang,
        "format": "json",
        "type": "item",
        "limit": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                SEARCH_URL,
                params=params,
                headers={"User-Agent": get_musicbrainz_user_agent()},
            )
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikidata-sök misslyckades: {}", exc)
        return []

    candidates = data.get("search") or []
    results: list[dict] = []
    for c in candidates:
        qid = c.get("id")
        if not qid:
            continue
        results.append(
            {
                "qid": qid,
                "label": c.get("label") or qid,
                "description": c.get("description") or "",
            }
        )

    # Filtrera till personer (instance of human). En entity-fetch per kandidat
    # är dyrt men vi har ändå begränsat till `limit` träffar.
    filtered: list[dict] = []
    for r_item in results:
        entity = await get_entity(r_item["qid"])
        if entity and _is_human(entity):
            r_item["wikipedia_url"] = extract_wikipedia_url(entity, lang) or extract_wikipedia_url(entity, "en")
            r_item["birth"] = extract_birth_year(entity)
            r_item["death"] = extract_death_year(entity)
            filtered.append(r_item)
    return filtered


async def get_entity(qid: str) -> dict | None:
    """Hämta full entity-objekt med claims, sitelinks, labels."""
    if not qid or not re.match(r"^Q\d+$", qid):
        return None
    url = ENTITY_URL.format(qid=qid)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                url, headers={"User-Agent": get_musicbrainz_user_agent()}
            )
            r.raise_for_status()
            return r.json().get("entities", {}).get(qid, {})
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikidata-entity {} misslyckades: {}", qid, exc)
        return None


def _is_human(entity: dict) -> bool:
    """Kolla P31 (instance of) för Q5 (human)."""
    for claim in (entity.get("claims") or {}).get(P_INSTANCE_OF, []):
        try:
            if claim["mainsnak"]["datavalue"]["value"]["id"] == P_HUMAN:
                return True
        except (KeyError, TypeError):
            continue
    return False


def _claim_value(entity: dict, prop: str) -> Any:
    """Hämta första datavalue-värdet för en property."""
    for claim in (entity.get("claims") or {}).get(prop, []):
        try:
            return claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
    return None


def extract_musicbrainz_id(entity: dict) -> str | None:
    val = _claim_value(entity, P_MBID)
    return val if isinstance(val, str) else None


def _parse_wd_date(value: Any) -> tuple[int | None, int | None, int | None]:
    """Wikidata datum: {'time': '+1809-02-03T00:00:00Z', 'precision': 11, ...}
    precision 9 = år, 10 = år+månad, 11 = år+månad+dag."""
    if not isinstance(value, dict):
        return None, None, None
    time_str = value.get("time") or ""
    m = re.match(r"^[+-](\d{1,4})-(\d{2})-(\d{2})T", time_str)
    if not m:
        return None, None, None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    precision = value.get("precision", 11)
    if precision < 9:
        return None, None, None
    if precision < 10:
        return year, None, None
    if precision < 11:
        return year, month if month else None, None
    return year, month if month else None, day if day else None


def extract_birth_year(entity: dict) -> int | None:
    y, _, _ = _parse_wd_date(_claim_value(entity, P_BIRTH_DATE))
    return y


def extract_death_year(entity: dict) -> int | None:
    y, _, _ = _parse_wd_date(_claim_value(entity, P_DEATH_DATE))
    return y


def extract_birth_date(entity: dict) -> tuple[int | None, int | None, int | None]:
    return _parse_wd_date(_claim_value(entity, P_BIRTH_DATE))


def extract_death_date(entity: dict) -> tuple[int | None, int | None, int | None]:
    return _parse_wd_date(_claim_value(entity, P_DEATH_DATE))


def extract_country_qid(entity: dict) -> str | None:
    val = _claim_value(entity, P_COUNTRY_OF_CITIZENSHIP)
    if isinstance(val, dict):
        return val.get("id")
    return None


async def country_iso_from_qid(country_qid: str | None) -> str | None:
    """Slå upp landets ISO 3166-1 alpha-2-kod (P297) från ett Q-id."""
    if not country_qid:
        return None
    entity = await get_entity(country_qid)
    if not entity:
        return None
    val = _claim_value(entity, "P297")
    if isinstance(val, str):
        return val.upper()
    return None


def extract_image_filename(entity: dict) -> str | None:
    val = _claim_value(entity, P_IMAGE)
    return val if isinstance(val, str) else None


def link_mb_wd_candidates(mb_list: list[dict], wd_list: list[dict]) -> None:
    """Markera korslänkade MB- och WD-träffar in-place baserat på namn +
    födelseår (case-insensitive). MB-träffar med matchning får 'linked_qid'
    satt. WD-träffar som matchats får 'linked'=True så templaten kan dölja
    dem (de visas inline under MB-träffen istället)."""
    def _mb_birth_year(mb: dict) -> int | None:
        try:
            ls = mb.get("life-span") or {}
            return int((ls.get("begin") or "")[:4])
        except (TypeError, ValueError):
            return None

    wd_index: dict[tuple[str, int | None], dict] = {}
    for w in wd_list:
        key = ((w.get("label") or "").lower(), w.get("birth"))
        wd_index[key] = w
    for mb in mb_list:
        key = ((mb.get("name") or "").lower(), _mb_birth_year(mb))
        match = wd_index.get(key)
        if match:
            mb["linked_qid"] = match["qid"]
            mb["linked_wd_label"] = match.get("label") or ""
            match["linked"] = True


def extract_wikipedia_url(entity: dict, lang: str = "sv") -> str | None:
    """Hämta Wikipedia-URL från sitelinks. Föredra givet språk, fall tillbaka
    på en av (sv, en, de) om ej hittad."""
    sitelinks = entity.get("sitelinks") or {}
    for candidate_lang in (lang, "sv", "en", "de"):
        key = f"{candidate_lang}wiki"
        if key in sitelinks:
            title = sitelinks[key]["title"].replace(" ", "_")
            return f"https://{candidate_lang}.wikipedia.org/wiki/{title}"
    return None
