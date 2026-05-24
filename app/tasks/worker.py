"""arq WorkerSettings. Starta med: uv run arq app.tasks.worker.WorkerSettings"""

from arq.connections import RedisSettings
from loguru import logger

from app.config import settings
from app.logging_setup import setup_logging
from app.tasks.ocr_jobs import extract_metadata_job


async def startup(ctx: dict) -> None:
    setup_logging()
    logger.info("arq-worker startar mot {}", settings.redis_url)


async def shutdown(ctx: dict) -> None:
    logger.info("arq-worker stänger ner")
    from app.services.musicbrainz import get_client

    try:
        await get_client().close()
    except Exception as exc:
        logger.warning("Kunde inte stänga MB-klient: {}", exc)


class WorkerSettings:
    functions = [extract_metadata_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 120
    max_jobs = 4
