"""Delade helpers för lagringsplatser och enheter."""

from sqlmodel import Session, select

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


def unit_subtree_ids(session: Session, unit_id: int) -> set[int]:
    """Returnera set av unit_id + alla descendant unit_ids. Använd när
    en lagringsenhet och allt under den ska räknas som en enhet (t.ex.
    inventering av en pärm med flera fack)."""
    result: set[int] = {unit_id}
    # BFS över parent_id-relationen
    frontier = {unit_id}
    while frontier:
        children = session.exec(
            select(StorageUnit).where(StorageUnit.parent_id.in_(list(frontier)))
        ).all()
        new_ids = {u.id for u in children} - result
        if not new_ids:
            break
        result |= new_ids
        frontier = new_ids
    return result
