"""Regressionstester för fixarna från helkodsgranskningen 2026-07-19.

Ett test per åtgärdat fynd som har observerbart beteende värt att låsa fast.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from tests.conftest import get_csrf


def _login_as(client, username: str, password: str) -> None:
    """Logga in en TestClient som en godtycklig användare."""
    import re

    r = client.get("/login")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    assert m
    r = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": m.group(1)},
    )
    assert r.status_code in (302, 303)


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


def test_fts_match_query_undviker_syntaxfel():
    """TASK-69: sökningar med FTS5-specialtecken (ledande '-', ensamt '\"')
    ska bli säkra MATCH-strängar utan syntaxfel, och prefix-sök ska funka."""
    import sqlite3

    from app.routes.pieces.helpers import _fts_match_query

    con = sqlite3.connect(":memory:")
    con.execute("CREATE VIRTUAL TABLE fts USING fts5(body)")
    con.execute("INSERT INTO fts(body) VALUES ('trollet dansar')")

    for term in ["-troll", '"', 'a"b', "räv troll", "   ", "AND OR NOT"]:
        mq = _fts_match_query(term)
        if mq:
            # Får inte kasta sqlite3.OperationalError (fts5 syntax error)
            con.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (mq,)).fetchall()

    # Prefix-sök på sista ordet ska fortfarande hitta raden
    mq = _fts_match_query("troll")
    rows = con.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (mq,)).fetchall()
    assert len(rows) == 1
    con.close()


def test_delete_user_blockeras_vid_fk_referens(
    logged_in_client, session: Session, admin_user
):
    """TASK-62: en användare som skapat en not (eller annan FK-referens) ska
    inte gå att radera - vänligt fel i stället för IntegrityError/500."""
    from app.auth import hash_password
    from app.models import Piece, User
    from app.models.user import Role

    target = User(username="skapare", password_hash=hash_password("x"), role=Role.EDITOR)
    session.add(target)
    session.flush()
    session.add(Piece(title="Not av skaparen", created_by=target.id))
    session.commit()
    target_id = target.id

    csrf = get_csrf(logged_in_client, "/pieces")
    r = logged_in_client.post(
        f"/admin/users/{target_id}/delete", data={"csrf_token": csrf}
    )
    assert r.status_code in (302, 303)
    session.expire_all()
    assert session.get(User, target_id) is not None, "användaren ska inte ha raderats"


def test_delete_user_utan_referens_gar_bra(
    logged_in_client, session: Session, admin_user
):
    """TASK-62: en användare utan FK-referenser ska fortfarande kunna raderas."""
    from app.auth import hash_password
    from app.models import User
    from app.models.user import Role

    target = User(username="oanvand", password_hash=hash_password("x"), role=Role.READER)
    session.add(target)
    session.commit()
    target_id = target.id

    csrf = get_csrf(logged_in_client, "/pieces")
    r = logged_in_client.post(
        f"/admin/users/{target_id}/delete", data={"csrf_token": csrf}
    )
    assert r.status_code in (302, 303)
    session.expire_all()
    assert session.get(User, target_id) is None


def test_mark_not_found_avvisar_hamtad_rad(
    logged_in_client, session: Session, admin_user
):
    """TASK-68: not-found på en redan hämtad rad i en active batch ska ge 404
    och inte radera raden (noten är fysiskt utlånad)."""
    from app.models import (
        Loan,
        LoanBatch,
        LoanBatchStatus,
        Piece,
        PiecePlacement,
        StorageLocation,
        StorageUnit,
    )
    from app.utils.dates import now_utc

    loc = StorageLocation(name="Arkiv", kind="physical")
    session.add(loc)
    session.flush()
    unit = StorageUnit(location_id=loc.id, name="Pärm")
    session.add(unit)
    session.flush()
    piece = Piece(title="Not", created_by=admin_user.id)
    session.add(piece)
    session.flush()
    pl = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=2)
    session.add(pl)
    session.flush()
    batch = LoanBatch(created_by=admin_user.id, status=LoanBatchStatus.ACTIVE, name="Konsert")
    session.add(batch)
    session.flush()
    loan = Loan(
        placement_id=pl.id,
        borrower_name="Låntagare",
        copies=1,
        batch_id=batch.id,
        picked_up_at=now_utc(),
    )
    session.add(loan)
    session.commit()
    loan_id = loan.id

    csrf = get_csrf(logged_in_client, "/pieces")
    r = logged_in_client.post(
        f"/loans/{loan_id}/not-found", data={"csrf_token": csrf}
    )
    assert r.status_code == 404
    session.expire_all()
    assert session.get(Loan, loan_id) is not None


def test_cart_update_tar_bort_rad_nar_inget_ledigt(
    logged_in_client, session: Session, admin_user
):
    """TASK-66: när inget är ledigt (capped=0) ska cart-raden tas bort, inte
    tvingas till 1 exemplar (annars permanent överbokning)."""
    from app.models import (
        Loan,
        LoanBatch,
        LoanBatchStatus,
        Piece,
        PiecePlacement,
        StorageLocation,
        StorageUnit,
    )
    from app.utils.dates import now_utc

    loc = StorageLocation(name="Arkiv2", kind="physical")
    session.add(loc)
    session.flush()
    unit = StorageUnit(location_id=loc.id, name="Låda")
    session.add(unit)
    session.flush()
    piece = Piece(title="Not2", created_by=admin_user.id)
    session.add(piece)
    session.flush()
    pl = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=1)
    session.add(pl)
    session.flush()

    # Aktiv batch som reserverar hela placeringen (1 av 1)
    active = LoanBatch(created_by=admin_user.id, status=LoanBatchStatus.ACTIVE, name="Aktiv")
    session.add(active)
    session.flush()
    session.add(
        Loan(
            placement_id=pl.id,
            borrower_name="Annan",
            copies=1,
            batch_id=active.id,
            picked_up_at=now_utc(),
        )
    )
    # Egen cart-batch med en rad på samma placering
    cart = LoanBatch(created_by=admin_user.id, status=LoanBatchStatus.CART)
    session.add(cart)
    session.flush()
    cart_loan = Loan(placement_id=pl.id, borrower_name="", copies=1, batch_id=cart.id)
    session.add(cart_loan)
    session.commit()
    cart_loan_id = cart_loan.id

    csrf = get_csrf(logged_in_client, "/pieces")
    r = logged_in_client.post(
        f"/loans/cart/{cart_loan_id}/update",
        data={"csrf_token": csrf, "copies": 1},
    )
    assert r.status_code in (302, 303)
    session.expire_all()
    assert session.get(Loan, cart_loan_id) is None, "raden ska tas bort, inte floras till 1"


def test_markdown_filter_sanerar_xss():
    """TASK-58: markdown-filtern renderas med | safe, så den måste sanera bort
    XSS-vektorer men behålla vanlig formatering."""
    from app.templates_setup import _markdown

    farligt = _markdown(
        "<img src=x onerror=alert(document.cookie)> <script>alert(1)</script>"
    )
    assert "onerror" not in farligt
    assert "<script" not in farligt

    assert "javascript:" not in _markdown("[klicka](javascript:alert(1))")
    assert "onclick" not in _markdown("<a href='#' onclick='steal()'>x</a>")

    # Legitim formatering ska överleva saneringen
    assert "<strong>" in _markdown("**fet**")
    assert "<table>" in _markdown("| A | B |\n|---|---|\n| 1 | 2 |")


def test_session_secret_kravs_i_produktion():
    """TASK-56: appen ska vägra köra i produktion med det publika default-
    värdet på SESSION_SECRET, men tillåta det i development (lokal körning)."""
    from pydantic import ValidationError

    from app.config import DEFAULT_SESSION_SECRET, Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            session_secret=DEFAULT_SESSION_SECRET,
        )

    prod_ok = Settings(_env_file=None, app_env="production", session_secret="a" * 32)
    assert prod_ok.session_secret == "a" * 32

    dev_ok = Settings(
        _env_file=None, app_env="development", session_secret=DEFAULT_SESSION_SECRET
    )
    assert dev_ok.app_env == "development"


async def test_extract_job_markerar_failed_vid_cancel(
    session: Session, test_engine, admin_user, monkeypatch
):
    """TASK-60: en timeout/cancel (CancelledError) mitt i jobbet ska markera
    scanen failed - inte lämna den fast i extracting - och re-raisa."""
    import asyncio

    import app.tasks.ocr_jobs as ocr_jobs
    from app.models import ScanSession
    from app.models.scan_session import ScanStatus

    monkeypatch.setattr(ocr_jobs, "engine", test_engine)
    monkeypatch.setattr(ocr_jobs, "read_cover_for_ocr", lambda p: b"fake")

    class _CancelProvider:
        async def extract(self, image_bytes):
            raise asyncio.CancelledError()

    monkeypatch.setattr(ocr_jobs, "get_provider", lambda name: _CancelProvider())

    scan = ScanSession(
        image_path="x.jpg", ocr_provider="claude_vision", user_id=admin_user.id
    )
    session.add(scan)
    session.commit()
    sid = scan.id

    with pytest.raises(asyncio.CancelledError):
        await ocr_jobs.extract_metadata_job({}, sid)

    session.expire_all()
    assert session.get(ScanSession, sid).status == ScanStatus.FAILED


def test_kiosk_borrower_timeout_pa_alla_routes(monkeypatch):
    """TASK-64: kiosk_borrower_id_if_active (som cart/kiosk-POST-dependencies
    använder) rensar sessionen vid utgången inaktivitet och touchar annars."""
    import types
    from datetime import timedelta

    import app.services.app_settings as app_settings
    from app.deps import kiosk_borrower_id_if_active
    from app.utils.dates import now_utc

    monkeypatch.setattr(app_settings, "get_kiosk_idle_timeout_minutes", lambda: 60)

    # Utgången (2h sedan, gräns 60 min) -> None + sessionen rensad
    stale = types.SimpleNamespace(
        session={
            "kiosk_borrower_id": 5,
            "kiosk_borrower_last_active": (now_utc() - timedelta(hours=2)).isoformat(),
        }
    )
    assert kiosk_borrower_id_if_active(stale) is None
    assert "kiosk_borrower_id" not in stale.session

    # Nyligen aktiv -> id kvar, stämpel touchad
    fresh = types.SimpleNamespace(
        session={
            "kiosk_borrower_id": 7,
            "kiosk_borrower_last_active": now_utc().isoformat(),
        }
    )
    assert kiosk_borrower_id_if_active(fresh) == 7


def _loan_setup(session, borrower_user_id, admin_user):
    """Skapa piece + placering + ett aktivt lån med given borrower_user_id."""
    from app.models import (
        Loan,
        Piece,
        PiecePlacement,
        StorageLocation,
        StorageUnit,
    )
    from app.utils.dates import now_utc

    loc = StorageLocation(name="Arkiv-idor", kind="physical")
    session.add(loc)
    session.flush()
    unit = StorageUnit(location_id=loc.id, name="Pärm")
    session.add(unit)
    session.flush()
    piece = Piece(title="Not", created_by=admin_user.id)
    session.add(piece)
    session.flush()
    pl = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=1)
    session.add(pl)
    session.flush()
    loan = Loan(
        placement_id=pl.id,
        borrower_name="X",
        borrower_user_id=borrower_user_id,
        copies=1,
        picked_up_at=now_utc(),
    )
    session.add(loan)
    session.commit()
    return loan.id


def test_return_loan_idor_blockeras(client, session: Session, admin_user):
    """TASK-57: en låntagare (reader) får inte återlämna någon annans lån."""
    from app.auth import hash_password
    from app.models import Loan, User
    from app.models.user import Role

    reader = User(username="lantagare1", password_hash=hash_password("pw123"), role=Role.READER)
    session.add(reader)
    session.commit()
    # Lån som tillhör admin_user, inte reader
    loan_id = _loan_setup(session, admin_user.id, admin_user)

    _login_as(client, "lantagare1", "pw123")
    csrf = get_csrf(client, "/pieces")
    r = client.post(f"/loans/{loan_id}/return", data={"csrf_token": csrf})
    assert r.status_code == 403
    session.expire_all()
    assert session.get(Loan, loan_id).returned_at is None


def test_return_loan_egen_tillaten(client, session: Session, admin_user):
    """TASK-57: en låntagare får återlämna sitt EGET lån (kiosk-självbetjäning)."""
    from app.auth import hash_password
    from app.models import Loan, User
    from app.models.user import Role

    reader = User(username="lantagare2", password_hash=hash_password("pw123"), role=Role.READER)
    session.add(reader)
    session.commit()
    reader_id = reader.id
    loan_id = _loan_setup(session, reader_id, admin_user)

    _login_as(client, "lantagare2", "pw123")
    csrf = get_csrf(client, "/pieces")
    r = client.post(f"/loans/{loan_id}/return", data={"csrf_token": csrf})
    assert r.status_code in (302, 303)
    session.expire_all()
    assert session.get(Loan, loan_id).returned_at is not None
