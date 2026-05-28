"""Runtime-inställningar lagrade i DB. Faller tillbaka till env via app.config."""

from datetime import datetime

from sqlmodel import Session

from app.config import settings as env_settings
from app.db import engine
from app.models import AppSetting

KEY_ANTHROPIC_API_KEY = "anthropic_api_key"
KEY_CLAUDE_MODEL = "claude_model"
KEY_MUSICBRAINZ_USER_AGENT = "musicbrainz_user_agent"
KEY_OCR_PROVIDER = "ocr_provider"
KEY_KIOSK_IDLE_TIMEOUT_MINUTES = "kiosk_idle_timeout_minutes"

SENSITIVE_KEYS = {KEY_ANTHROPIC_API_KEY}


def get_setting(key: str, default: str | None = None) -> str | None:
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row and row.value:
            return row.value
    return default


def set_setting(key: str, value: str | None, user_id: int | None = None) -> None:
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value, updated_by=user_id)
        else:
            row.value = value
            row.updated_at = datetime.utcnow()
            row.updated_by = user_id
        session.add(row)
        session.commit()


def all_settings() -> dict[str, str | None]:
    """Returnera nuvarande effektiva värden för UI:t. Hemligheter maskas."""
    return {
        KEY_ANTHROPIC_API_KEY: get_anthropic_api_key(),
        KEY_CLAUDE_MODEL: get_setting(KEY_CLAUDE_MODEL, env_settings.claude_model),
        KEY_MUSICBRAINZ_USER_AGENT: get_setting(
            KEY_MUSICBRAINZ_USER_AGENT, env_settings.musicbrainz_user_agent
        ),
        KEY_OCR_PROVIDER: get_setting(KEY_OCR_PROVIDER, env_settings.ocr_provider),
    }


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def get_anthropic_api_key() -> str | None:
    return get_setting(KEY_ANTHROPIC_API_KEY, env_settings.anthropic_api_key)


def get_claude_model() -> str:
    return get_setting(KEY_CLAUDE_MODEL, env_settings.claude_model) or env_settings.claude_model


def get_musicbrainz_user_agent() -> str:
    return get_setting(
        KEY_MUSICBRAINZ_USER_AGENT, env_settings.musicbrainz_user_agent
    ) or env_settings.musicbrainz_user_agent


def get_ocr_provider() -> str:
    return get_setting(KEY_OCR_PROVIDER, env_settings.ocr_provider) or env_settings.ocr_provider


def get_kiosk_idle_timeout_minutes() -> int:
    """Antal minuter inaktivitet innan PIN-autentiserade låntagaren auto-
    loggas ut från kiosken. Default 60 - lång eftersom körledare kan
    vandra runt och leta noter. 0 = aldrig auto-logga-ut."""
    val = get_setting(KEY_KIOSK_IDLE_TIMEOUT_MINUTES, "60")
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 60
