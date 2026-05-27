"""Enkel in-memory rate-limit för kiosk-auth.

Räknar fel per IP. Vid `MAX_ATTEMPTS` fel inom `WINDOW_SECONDS` blockeras
nya försök i `LOCKOUT_SECONDS`. Räknaren nollas vid lyckat försök.

Lever per-process - räcker för en singel-uvicorn-instans. Vid scale-out
behöver vi Redis-baserad räknare.
"""

from __future__ import annotations

import time
from threading import Lock

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60   # räknarfönster
LOCKOUT_SECONDS = 5 * 60   # vänta innan nya försök tillåts efter MAX

# {ip: {"count": int, "first_at": float, "locked_until": float}}
_state: dict[str, dict] = {}
_lock = Lock()


def check_kiosk_attempts(ip: str) -> bool:
    """Returnerar True om IP får göra ett försök. False = blockerad."""
    now = time.time()
    with _lock:
        entry = _state.get(ip)
        if not entry:
            return True
        if entry.get("locked_until", 0) > now:
            return False
        if now - entry.get("first_at", 0) > WINDOW_SECONDS:
            # Fönstret har gått ut - reset
            _state.pop(ip, None)
            return True
        return entry.get("count", 0) < MAX_ATTEMPTS


def record_kiosk_failure(ip: str) -> None:
    """Registrera ett misslyckat försök. Lås ut om MAX_ATTEMPTS nås."""
    now = time.time()
    with _lock:
        entry = _state.get(ip)
        if not entry or now - entry.get("first_at", 0) > WINDOW_SECONDS:
            _state[ip] = {"count": 1, "first_at": now, "locked_until": 0}
            return
        entry["count"] = entry.get("count", 0) + 1
        if entry["count"] >= MAX_ATTEMPTS:
            entry["locked_until"] = now + LOCKOUT_SECONDS


def reset_kiosk_attempts(ip: str) -> None:
    """Rensa räknaren - kallas efter lyckad autentisering."""
    with _lock:
        _state.pop(ip, None)


def reset_all_for_tests() -> None:
    """Test-helper: nolla all state."""
    with _lock:
        _state.clear()
