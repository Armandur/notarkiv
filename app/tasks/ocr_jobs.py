"""Bakgrundsjobb: extrahera metadata och berika med MusicBrainz."""

import asyncio
import json
from datetime import datetime
from app.utils.dates import now_utc

from loguru import logger
from sqlmodel import Session

from app.db import engine
from app.models import ScanSession
from app.models.scan_session import ScanStatus
from app.services.musicbrainz import get_client, to_suggestions
from app.services.ocr.base import get_provider
from app.utils.images import read_cover_for_ocr


async def extract_metadata_job(ctx: dict, scan_session_id: int) -> dict:
    """Huvudjobbet: läs bild -> OCR -> MB-lookup -> uppdatera ScanSession."""
    logger.info("Startar extract_metadata_job för scan={}", scan_session_id)

    with Session(engine) as session:
        scan = session.get(ScanSession, scan_session_id)
        if not scan:
            raise ValueError(f"ScanSession {scan_session_id} saknas")
        scan.status = ScanStatus.EXTRACTING
        session.add(scan)
        session.commit()
        image_path = scan.image_path
        provider_name = scan.ocr_provider

    # CancelledError ärver BaseException och fångas INTE av except Exception.
    # Utan denna yttre guard skulle en timeout (job_timeout) eller worker-
    # shutdown mitt i ett await lämna scanen fast i extracting/enriching.
    try:
        try:
            image_bytes = read_cover_for_ocr(image_path)
            provider = get_provider(provider_name)
            metadata = await provider.extract(image_bytes)
        except Exception as exc:
            logger.exception("OCR-extraktion misslyckades")
            _mark_failed(scan_session_id, str(exc))
            return {"status": "failed", "error": str(exc)}

        # Spara extraherade data och gå vidare till MB-berikning
        with Session(engine) as session:
            scan = session.get(ScanSession, scan_session_id)
            scan.raw_response = metadata.model_dump_json()
            scan.status = ScanStatus.ENRICHING
            session.add(scan)
            session.commit()

        suggestions = []
        if metadata.title:
            try:
                mb_client = get_client()
                works = await mb_client.search_work(metadata.title, metadata.composer)
                suggestions = to_suggestions(works, metadata.title, metadata.composer)
                logger.info("MB returnerade {} förslag", len(suggestions))
            except Exception as exc:
                logger.warning("MusicBrainz-anrop misslyckades: {}", exc)

        with Session(engine) as session:
            scan = session.get(ScanSession, scan_session_id)
            scan.status = ScanStatus.DONE
            scan.completed_at = now_utc()
            scan.musicbrainz_suggestion = json.dumps([s.model_dump() for s in suggestions])
            session.add(scan)
            session.commit()

        return {"status": "done", "scan_session_id": scan_session_id}
    except asyncio.CancelledError:
        logger.warning(
            "extract_metadata_job avbröts (timeout/shutdown) för scan={}",
            scan_session_id,
        )
        _mark_failed(scan_session_id, "Tidsgräns nådd - jobbet avbröts. Försök igen.")
        raise


def _mark_failed(scan_session_id: int, error: str) -> None:
    with Session(engine) as session:
        scan = session.get(ScanSession, scan_session_id)
        if scan:
            scan.status = ScanStatus.FAILED
            scan.error_message = _humanize_error(error)
            scan.completed_at = now_utc()
            session.add(scan)
            session.commit()


def _humanize_error(error: str) -> str:
    """Förkorta tekniska fel till något läsbart för användaren."""
    if not error:
        return "Okänt fel"

    # HTML-svar från proxy/CDN-felsidor (typ Cloudflare 502)
    if "<html" in error.lower() or "<!doctype" in error.lower():
        import re

        title_match = re.search(r"<title>([^<]+)</title>", error, re.IGNORECASE)
        h1_match = re.search(r"<h1[^>]*>([^<]+)</h1>", error, re.IGNORECASE)
        center = re.search(r"<center>([^<]+)</center>", error, re.IGNORECASE)
        bits = [m.group(1).strip() for m in (title_match, h1_match, center) if m]
        if bits:
            # Vanligast: Cloudflare 502 -> "502 Bad Gateway · cloudflare"
            return "Anthropic API tillfälligt otillgängligt: " + " · ".join(dict.fromkeys(bits))
        return "Externt API returnerade ett HTML-felsvar"

    if len(error) > 280:
        return error[:277] + "..."
    return error
