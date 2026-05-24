"""Custom middleware: säkerställ CSRF-token i session, sätt request.state.user."""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp


class EnsureCSRFTokenMiddleware(BaseHTTPMiddleware):
    """Sätt en CSRF-token i sessionen vid första requesten."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_urlsafe(32)
        return await call_next(request)
