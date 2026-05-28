from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response

from app.deps import require_admin, verify_csrf
from app.models import User
from app.services import app_settings
from app.services.app_settings import (
    KEY_ANTHROPIC_API_KEY,
    KEY_CLAUDE_MODEL,
    KEY_KIOSK_IDLE_TIMEOUT_MINUTES,
    KEY_MUSICBRAINZ_USER_AGENT,
    KEY_OCR_PROVIDER,
)
from app.templates_setup import flash, render

router = APIRouter(prefix="/admin/settings", tags=["admin"])


@router.get("")
async def settings_form(
    request: Request,
    user: User = Depends(require_admin),
) -> Response:
    current = app_settings.all_settings()
    masked = {
        KEY_ANTHROPIC_API_KEY: app_settings.mask_secret(current[KEY_ANTHROPIC_API_KEY]),
        KEY_CLAUDE_MODEL: current[KEY_CLAUDE_MODEL] or "",
        KEY_MUSICBRAINZ_USER_AGENT: current[KEY_MUSICBRAINZ_USER_AGENT] or "",
        KEY_OCR_PROVIDER: current[KEY_OCR_PROVIDER] or "claude_vision",
        KEY_KIOSK_IDLE_TIMEOUT_MINUTES: str(app_settings.get_kiosk_idle_timeout_minutes()),
    }
    has_anthropic_key = bool(current[KEY_ANTHROPIC_API_KEY])

    return render(
        request,
        "admin/settings.html",
        {
            "settings": masked,
            "has_anthropic_key": has_anthropic_key,
            "providers": ["claude_vision", "tesseract", "hybrid"],
        },
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def settings_save(
    request: Request,
    anthropic_api_key: str | None = Form(None),
    clear_anthropic_key: str | None = Form(None),
    claude_model: str | None = Form(None),
    musicbrainz_user_agent: str | None = Form(None),
    ocr_provider: str | None = Form(None),
    kiosk_idle_timeout_minutes: str | None = Form(None),
    user: User = Depends(require_admin),
) -> Response:
    # API-nyckeln: tom inmatning tolkas som "ingen ändring" om inte clear_anthropic_key
    if clear_anthropic_key:
        app_settings.set_setting(KEY_ANTHROPIC_API_KEY, None, user.id)
        flash(request, "API-nyckeln rensad", "info")
    elif anthropic_api_key and anthropic_api_key.strip():
        app_settings.set_setting(KEY_ANTHROPIC_API_KEY, anthropic_api_key.strip(), user.id)

    if claude_model and claude_model.strip():
        app_settings.set_setting(KEY_CLAUDE_MODEL, claude_model.strip(), user.id)
    if musicbrainz_user_agent and musicbrainz_user_agent.strip():
        app_settings.set_setting(
            KEY_MUSICBRAINZ_USER_AGENT, musicbrainz_user_agent.strip(), user.id
        )
    if ocr_provider and ocr_provider in {"claude_vision", "tesseract", "hybrid"}:
        app_settings.set_setting(KEY_OCR_PROVIDER, ocr_provider, user.id)

    if kiosk_idle_timeout_minutes is not None:
        clean = kiosk_idle_timeout_minutes.strip()
        if clean.isdigit():
            app_settings.set_setting(
                KEY_KIOSK_IDLE_TIMEOUT_MINUTES, clean, user.id
            )

    flash(request, "Inställningar sparade", "success")
    return RedirectResponse("/admin/settings", status.HTTP_302_FOUND)
