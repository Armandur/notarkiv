import sys

from loguru import logger

from app.config import settings


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "{message}"
        ),
        backtrace=True,
        diagnose=settings.app_env == "development",
    )

    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                traces_sample_rate=0.1,
                environment=settings.app_env,
            )
            logger.info("Sentry initialiserad")
        except ImportError:
            logger.warning("SENTRY_DSN angiven men sentry-sdk är inte installerad")
