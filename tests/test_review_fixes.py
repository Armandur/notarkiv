"""Regressionstester för fixarna från helkodsgranskningen 2026-07-19.

Ett test per åtgärdat fynd som har observerbart beteende värt att låsa fast.
"""

from __future__ import annotations

from sqlmodel import Session, select

from tests.conftest import get_csrf


def test_extract_wikipedia_url_avvisar_spoofad_doman():
    """TASK-78: host-kontroll, inte substräng. wikipedia.org.evil.example
    ska avvisas, sv.wikipedia.org ska godkännas."""
    from app.services.musicbrainz import extract_wikipedia_url

    spoof = {
        "relations": [
            {"type": "wikipedia", "url": {"resource": "https://wikipedia.org.evil.example/x"}}
        ]
    }
    assert extract_wikipedia_url(spoof) is None

    legit = {
        "relations": [
            {"type": "wikipedia", "url": {"resource": "https://sv.wikipedia.org/wiki/Bach"}}
        ]
    }
    assert extract_wikipedia_url(legit) == "https://sv.wikipedia.org/wiki/Bach"


def test_find_or_create_person_understreck_ar_inte_wildcard(session: Session):
    """TASK-82: '_' i namnet ska INTE matcha godtyckligt tecken (LIKE-wildcard).
    'C_P_E Bach' ska alltså inte hitta befintliga 'CxPxE Bach' utan skapa ny."""
    from app.models import Person
    from app.services.people import find_or_create_person

    session.add(Person(name="CxPxE Bach", sort_name="Bach, CxPxE"))
    session.commit()

    ny = find_or_create_person(session, "C_P_E Bach")
    session.commit()

    assert ny is not None and ny.name == "C_P_E Bach"
    assert len(session.exec(select(Person)).all()) == 2


def test_check_item_avvisas_pa_avslutad_session(logged_in_client, session: Session, admin_user):
    """TASK-73: en check mot en redan avslutad inventeringssession ska ge 404
    och inte skapa någon InventoryCheck-rad."""
    from app.models import (
        InventorySession,
        Piece,
        PiecePlacement,
        StorageLocation,
        StorageUnit,
    )
    from app.models.inventory_check import InventoryCheck
    from app.utils.dates import now_utc

    loc = StorageLocation(name="Fysisk plats", kind="physical")
    session.add(loc)
    session.flush()
    unit = StorageUnit(location_id=loc.id, name="Hylla 1")
    session.add(unit)
    session.flush()
    piece = Piece(title="Testnot", created_by=admin_user.id)
    session.add(piece)
    session.flush()
    placement = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=1)
    session.add(placement)
    inv = InventorySession(name="Avslutad session", ended_at=now_utc())
    session.add(inv)
    session.commit()
    inv_id, unit_id, placement_id = inv.id, unit.id, placement.id

    csrf = get_csrf(logged_in_client, "/tags")
    r = logged_in_client.post(
        f"/inventory/{inv_id}/check/{unit_id}/items/{placement_id}",
        data={"csrf_token": csrf, "status": "found"},
    )
    assert r.status_code == 404

    session.expire_all()
    assert session.exec(select(InventoryCheck)).all() == []


class _FakeMBClient:
    """MB-klient vars artist saknar life-span (vanligt för mindre kända personer)."""

    async def get_artist_with_urls(self, mbid: str) -> dict:
        return {"name": "Uppdaterat Namn", "life-span": {}}


def test_refresh_person_mb_bevarar_manuella_datum(
    logged_in_client, session: Session, admin_user, monkeypatch
):
    """TASK-61: när MB-artisten saknar life-span ska manuellt inmatade
    levnadsår INTE skrivas över med None."""
    import app.routes.people as people_routes
    from app.models import Person

    p = Person(
        name="Dietrich Buxtehude",
        sort_name="Buxtehude, Dietrich",
        musicbrainz_artist_id="mbid-test-123",
        birth_year=1637,
        death_year=1707,
    )
    session.add(p)
    session.commit()
    pid = p.id

    monkeypatch.setattr(people_routes, "get_client", lambda: _FakeMBClient())

    async def _ingen_wiki(artist):
        return None

    monkeypatch.setattr(people_routes, "get_wikipedia_url", _ingen_wiki)

    csrf = get_csrf(logged_in_client, "/tags")
    r = logged_in_client.post(f"/people/{pid}/refresh", data={"csrf_token": csrf})
    assert r.status_code in (302, 303)

    session.expire_all()
    refreshed = session.get(Person, pid)
    assert refreshed.birth_year == 1637
    assert refreshed.death_year == 1707
