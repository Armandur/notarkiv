"""Tidsstämpel-hjälpare.

Hela kodbasen lagrar naiv UTC (se CLAUDE.md, designbeslut 6 - Postgres-redo).
`datetime.utcnow()` är deprecated i Python 3.12 men dess ersättning
`datetime.now(timezone.utc)` är *aware* - att blanda aware och naiv tid ger
TypeError vid jämförelse/subtraktion mot lagrade naiva värden. `now_utc()` ger
den icke-deprecated naiva UTC-tiden så allt förblir konsekvent naivt.
"""

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Aktuell UTC-tid som naiv datetime (utan tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
