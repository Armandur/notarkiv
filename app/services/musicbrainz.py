"""MusicBrainz-klient med rate limit och in-memory cache."""

import asyncio
import time
from functools import lru_cache

import httpx
from loguru import logger
from pydantic import BaseModel
from rapidfuzz import fuzz

from app.config import settings
from app.services.app_settings import get_musicbrainz_user_agent

BASE_URL = "https://musicbrainz.org/ws/2"


class MBSuggestion(BaseModel):
    mbid: str
    title: str
    composer: str | None = None
    score: int  # 0-100, vår egen score


class MusicBrainzClient:
    """Tunn wrapper kring MB-API:t. Singleton via get_client()."""

    def __init__(self) -> None:
        self._last_request_at = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": get_musicbrainz_user_agent()},
            timeout=15.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _wait_for_rate_limit(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = settings.musicbrainz_rate_limit_delay - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def search_work(self, title: str, composer: str | None) -> list[dict]:
        cache_key = (title.lower().strip(), (composer or "").lower().strip())
        cached = _search_cache_get(cache_key)
        if cached is not None:
            return cached

        await self._wait_for_rate_limit()
        query = _build_query(title, composer)
        try:
            resp = await self._client.get(
                "/work", params={"query": query, "fmt": "json", "limit": 5}
            )
            resp.raise_for_status()
            works = resp.json().get("works", [])
        except httpx.HTTPError as exc:
            logger.warning("MusicBrainz-sökning misslyckades: {}", exc)
            return []

        _search_cache_set(cache_key, works)
        return works

    async def search_artist(self, name: str) -> list[dict]:
        """Sök artister efter namn."""
        await self._wait_for_rate_limit()
        try:
            resp = await self._client.get(
                "/artist",
                params={
                    "query": f'artist:"{_escape(name)}"',
                    "fmt": "json",
                    "limit": 8,
                },
            )
            resp.raise_for_status()
            return resp.json().get("artists", [])
        except httpx.HTTPError as exc:
            logger.warning("MB search_artist misslyckades: {}", exc)
            return []

    async def get_work_with_rels(self, mbid: str) -> dict | None:
        """Hämta ett verk med artist-relationer (composer, lyricist osv)."""
        await self._wait_for_rate_limit()
        try:
            resp = await self._client.get(
                f"/work/{mbid}", params={"fmt": "json", "inc": "artist-rels"}
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("MB get_work_with_rels misslyckades: {}", exc)
            return None

    async def get_artist_with_urls(self, mbid: str) -> dict | None:
        """Hämta en artist med URL-rels (Wikipedia, Wikidata osv)."""
        await self._wait_for_rate_limit()
        try:
            resp = await self._client.get(
                f"/artist/{mbid}", params={"fmt": "json", "inc": "url-rels"}
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("MB get_artist_with_urls misslyckades: {}", exc)
            return None


async def fetch_wikipedia_summary(url: str) -> str | None:
    """Hämta ett kort utdrag (första stycket) från ett Wikipedia-artikel-URL.

    Använder REST API:t /page/summary som returnerar JSON med 'extract'.
    Best-effort - returnerar None vid fel.
    """
    if not url:
        return None
    import re

    m = re.match(r"https?://([a-z]+)\.wikipedia\.org/wiki/(.+)", url)
    if not m:
        return None
    lang, title = m.group(1), m.group(2)
    summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                summary_url, headers={"User-Agent": get_musicbrainz_user_agent()}
            )
            resp.raise_for_status()
            data = resp.json()
            extract = data.get("extract")
            return extract.strip() if extract else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikipedia-summary misslyckades för {}: {}", url, exc)
        return None


def extract_wikipedia_url(artist: dict) -> str | None:
    """Plocka Wikipedia-URL från en MB-artist med url-rels."""
    for rel in artist.get("relations", []):
        if rel.get("type") == "wikipedia":
            url = rel.get("url", {}).get("resource")
            if url:
                return url
    # Fallback: använd wikidata -> kan lösas via /sitelinks senare
    return None


def first_composer_from_work(work: dict) -> dict | None:
    """Returnera första composer-artist från work-relations."""
    for rel in work.get("relations", []):
        if rel.get("type") == "composer":
            return rel.get("artist")
    return None


def _build_query(title: str, composer: str | None) -> str:
    parts = [f'work:"{_escape(title)}"']
    if composer:
        parts.append(f'artist:"{_escape(composer)}"')
    return " AND ".join(parts)


def _escape(text: str) -> str:
    # MB Lucene-syntax: escapa special chars
    return text.replace('"', '\\"').replace("\\", "\\\\")


# Enkel LRU-cache. Persistent cache kan läggas till senare via en SQLite-tabell.
_SEARCH_CACHE: dict[tuple[str, str], list[dict]] = {}
_CACHE_LIMIT = 1000


def _search_cache_get(key: tuple[str, str]) -> list[dict] | None:
    return _SEARCH_CACHE.get(key)


def _search_cache_set(key: tuple[str, str], value: list[dict]) -> None:
    if len(_SEARCH_CACHE) >= _CACHE_LIMIT:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    _SEARCH_CACHE[key] = value


@lru_cache(maxsize=1)
def get_client() -> MusicBrainzClient:
    return MusicBrainzClient()


def score_work(work: dict, target_title: str, target_composer: str | None) -> int:
    """Räkna ut konfidenspoäng 0-100 för en MB-träff mot extraherade fält."""
    mb_title = work.get("title", "")
    title_score = fuzz.ratio(mb_title.lower(), target_title.lower())

    composer_score = 0
    if target_composer:
        composers = _composers_from_work(work)
        if composers:
            best = max(fuzz.ratio(c.lower(), target_composer.lower()) for c in composers)
            composer_score = best

    if target_composer:
        return int(title_score * 0.6 + composer_score * 0.4)
    return int(title_score)


def _composers_from_work(work: dict) -> list[str]:
    relations = work.get("relations", [])
    composers = []
    for rel in relations:
        if rel.get("type") == "composer":
            artist = rel.get("artist", {})
            name = artist.get("name") or artist.get("sort-name")
            if name:
                composers.append(name)
    return composers


def to_suggestions(
    works: list[dict], target_title: str, target_composer: str | None, threshold: int = 60
) -> list[MBSuggestion]:
    """Filtrera och formatera MB-träffar som suggestion-listobjekt."""
    suggestions: list[MBSuggestion] = []
    for work in works:
        score = score_work(work, target_title, target_composer)
        if score < threshold:
            continue
        composers = _composers_from_work(work)
        suggestions.append(
            MBSuggestion(
                mbid=work["id"],
                title=work.get("title", ""),
                composer=composers[0] if composers else None,
                score=score,
            )
        )
    suggestions.sort(key=lambda s: s.score, reverse=True)
    return suggestions[:3]
