"""Delade helpers för lagringsplatser och enheter."""

from sqlmodel import Session

from app.models import StorageLocation, StorageUnit

SEPARATOR = " › "


def unit_path(session: Session, unit: StorageUnit | None) -> str:
    """Bygg fullständig hierarkisk sökväg till en unit för visning:
    'Plats › Förälder › Underenhet'. Returnerar tom sträng om unit saknas."""
    if not unit:
        return ""
    parts = [unit.name]
    cur = unit
    while cur.parent_id:
        parent = session.get(StorageUnit, cur.parent_id)
        if not parent:
            break
        parts.append(parent.name)
        cur = parent
    loc = session.get(StorageLocation, unit.location_id)
    if loc:
        parts.append(loc.name)
    return SEPARATOR.join(reversed(parts))
