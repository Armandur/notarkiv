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

    async def search_label(self, name: str) -> list[dict]:
        """Sök labels (skivbolag/notutgivare) efter namn."""
        await self._wait_for_rate_limit()
        try:
            resp = await self._client.get(
                "/label",
                params={
                    "query": f'label:"{_escape(name)}"',
                    "fmt": "json",
                    "limit": 8,
                },
            )
            resp.raise_for_status()
            return resp.json().get("labels", [])
        except httpx.HTTPError as exc:
            logger.warning("MB search_label misslyckades: {}", exc)
            return []

    async def get_label_with_urls(self, mbid: str) -> dict | None:
        """Hämta en label med URL-rels (Wikipedia, Wikidata, hemsida)."""
        await self._wait_for_rate_limit()
        try:
            resp = await self._client.get(
                f"/label/{mbid}", params={"fmt": "json", "inc": "url-rels"}
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("MB get_label_with_urls misslyckades: {}", exc)
            return None


async def fetch_wikipedia_summary(url: str) -> str | None:
    """Hämta hela introduktionen (alla stycken innan första sektionsrubriken)
    från en Wikipedia-artikel. Använder MediaWiki action API med
    prop=extracts, exintro=1 och explaintext=1 för ren text.
    Best-effort - returnerar None vid fel.
    """
    if not url:
        return None
    import re
    from urllib.parse import unquote

    m = re.match(r"https?://([a-z]+)\.wikipedia\.org/wiki/(.+)", url)
    if not m:
        return None
    lang, title_raw = m.group(1), m.group(2)
    title = unquote(title_raw).replace("_", " ")
    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "titles": title,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                api_url, params=params,
                headers={"User-Agent": get_musicbrainz_user_agent()},
            )
            resp.raise_for_status()
            data = resp.json()
            pages = (data.get("query") or {}).get("pages") or {}
            for page in pages.values():
                extract = page.get("extract")
                if not extract:
                    continue
                return _truncate_wiki_extract(extract.strip())
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikipedia-extract misslyckades för {}: {}", url, exc)
    return None


def _truncate_wiki_extract(text: str, max_chars: int = 8000) -> str:
    """Klipp Wikipedia-extract till biografi-text och konvertera till
    markdown-format: MediaWiki ==-rubriker blir ##, enkla radbrytningar
    mellan stycken blir dubbla (paragraf-break) så Markdown renderar
    varje stycke som <p>."""
    import re

    stop_headings = (
        "Verk", "Verklista", "Verkförteckning", "Inspelningar", "Verkurval",
        "Referenser", "Källor", "Noter", "Citat", "Externa länkar",
        "Vidare läsning", "Bibliografi", "Diskografi", "Se även", "Litteratur",
        "Works", "Selected works", "Recordings", "References", "Notes",
        "External links", "Further reading", "Bibliography", "Discography",
        "See also", "Sources",
    )
    # Normalisera radbrytningar till \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Klipp vid första stop-rubrik
    cut_at = len(text)
    for h in stop_headings:
        pattern = re.compile(
            rf"^={{2,}}\s*{re.escape(h)}\s*={{2,}}\s*$", re.MULTILINE
        )
        m = pattern.search(text)
        if m and m.start() < cut_at:
            cut_at = m.start()
    text = text[:cut_at].rstrip()

    # Konvertera MediaWiki-rubriker till markdown.
    def heading_repl(m):
        level = len(m.group(1))
        title = m.group(2).strip()
        return f"\n\n{'#' * level} {title}\n"

    text = re.sub(
        r"^(={2,6})\s*(.+?)\s*\1\s*$",
        heading_repl,
        text,
        flags=re.MULTILINE,
    )
    # Säkerställ att enkla radbrytningar mellan stycken blir paragraf-
    # break. Wikipedia-extract:en har ofta bara '\n' mellan stycken.
    text = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", text)
    # Komprimera multipla blankrader
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) > max_chars:
        cut = text.rfind("\n\n", 0, max_chars)
        if cut < max_chars * 0.5:
            cut = text.rfind(". ", 0, max_chars)
        if cut > 0:
            text = text[:cut].rstrip() + "\n\n…"
        else:
            text = text[:max_chars].rstrip() + "…"
    return text


def extract_wikipedia_url(artist: dict) -> str | None:
    """Plocka direkt Wikipedia-relation. Många nyare MB-poster har bara
    wikidata-relation - använd get_wikipedia_url för fallback via Wikidata.
    Filtrerar bort URL:er som inte pekar på en wikipedia.org-domän (MB har
    historiskt haft wikidata-länkar registrerade som type='wikipedia')."""
    for rel in artist.get("relations", []):
        if rel.get("type") == "wikipedia":
            url = rel.get("url", {}).get("resource")
            if url and "wikipedia.org" in url:
                return url
    return None


def extract_wikidata_url(artist: dict) -> str | None:
    for rel in artist.get("relations", []):
        if rel.get("type") == "wikidata":
            url = rel.get("url", {}).get("resource")
            if url:
                return url
    return None


def extract_streaming_urls(artist: dict) -> dict[str, str]:
    """Plocka strömnings-rels (Spotify, Apple Music, YouTube etc.).
    Returnerar dict med kind→url. Kind matchar PersonLinkKind-värden."""
    result: dict[str, str] = {}
    for rel in artist.get("relations", []):
        url = (rel.get("url") or {}).get("resource") or ""
        if not url:
            continue
        if "spotify.com" in url and "spotify" not in result:
            result["spotify"] = url
        elif "youtube.com" in url and "youtube" not in result:
            result["youtube"] = url
        elif "instagram.com" in url and "instagram" not in result:
            result["instagram"] = url
    return result


def extract_image_url(artist: dict) -> str | None:
    """Plocka image-relation (oftast commons.wikimedia.org/wiki/File:... URL)."""
    for rel in artist.get("relations", []):
        if rel.get("type") == "image":
            url = rel.get("url", {}).get("resource")
            if url:
                return url
    return None


def commons_file_to_thumb_url(file_url: str, width: int = 600) -> str | None:
    """Konvertera Commons File-URL till Special:FilePath som ger faktisk bild."""
    import re

    m = re.match(r"https?://commons\.wikimedia\.org/wiki/File:(.+)", file_url)
    if not m:
        return None
    filename = m.group(1)
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width={width}"


async def download_image_bytes(url: str) -> bytes | None:
    """Ladda ner en bildfil. Följer redirects (Special:FilePath -> CDN).
    Validerar att svaret är en bild via content-type och rimlig storlek -
    Commons returnerar HTML eller mini-placeholder för filer som inte finns."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(
                url, headers={"User-Agent": get_musicbrainz_user_agent()}
            )
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            if not content_type.startswith("image/"):
                logger.warning(
                    "Bildhämtning fick fel content-type ({}) för {}",
                    content_type, url,
                )
                return None
            if len(resp.content) < 500:
                logger.warning(
                    "Bildhämtning fick orealistiskt liten body ({} bytes) för {}",
                    len(resp.content), url,
                )
                return None
            return resp.content
    except httpx.HTTPError as exc:
        logger.warning("Bildhämtning misslyckades för {}: {}", url, exc)
        return None


def wikidata_id_from_url(url: str | None) -> str | None:
    """Plocka ut Q-id från en Wikidata-URL ('...wiki/Q12345' -> 'Q12345')."""
    if not url:
        return None
    import re

    m = re.search(r"(Q\d+)", url)
    return m.group(1) if m else None


async def _fetch_wikidata_entity(wikidata_url: str) -> dict | None:
    """Hämta hela entity-objektet från Special:EntityData."""
    import re

    m = re.match(r"https?://www\.wikidata\.org/wiki/(Q\d+)", wikidata_url)
    if not m:
        return None
    entity_id = m.group(1)
    api_url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                api_url, headers={"User-Agent": get_musicbrainz_user_agent()}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("entities", {}).get(entity_id, {})
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikidata-entity misslyckades: {}", exc)
        return None


async def resolve_wikipedia_via_wikidata(
    wikidata_url: str, langs: tuple[str, ...] = ("sv", "en", "de")
) -> str | None:
    """Hämta Wikipedia-URL via Wikidata-sitelinks. Försöker språk i ordning."""
    entity = await _fetch_wikidata_entity(wikidata_url)
    if not entity:
        return None
    sitelinks = entity.get("sitelinks", {})
    for lang in langs:
        key = f"{lang}wiki"
        if key in sitelinks:
            title = sitelinks[key]["title"].replace(" ", "_")
            return f"https://{lang}.wikipedia.org/wiki/{title}"
    return None


async def resolve_image_via_wikidata(wikidata_url: str) -> str | None:
    """Hämta Commons-bild-URL från Wikidata-P18 (bild)-property. Returnerar
    en commons.wikimedia.org/wiki/File:... URL som kan passas till
    commons_file_to_thumb_url för att få nedladdningsbar bild."""
    entity = await _fetch_wikidata_entity(wikidata_url)
    if not entity:
        return None
    claims = entity.get("claims", {})
    p18 = claims.get("P18") or []
    for claim in p18:
        try:
            value = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if value:
            # Commons File-namn → URL
            filename = value.replace(" ", "_")
            return f"https://commons.wikimedia.org/wiki/File:{filename}"
    return None


async def get_wikipedia_url(artist: dict) -> str | None:
    """Bästa Wikipedia-URL: direkt rel om finns, annars via Wikidata-sitelinks."""
    direct = extract_wikipedia_url(artist)
    if direct:
        return direct
    wd = extract_wikidata_url(artist)
    if wd:
        return await resolve_wikipedia_via_wikidata(wd)
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
