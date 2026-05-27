"""Tests för QR-koder, /p/{uuid}-lookup och kiosk-flödet."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from tests.conftest import get_csrf


@pytest.fixture
def piece_with_qr(session: Session, admin_user):
    from app.models import Piece, PiecePlacement, StorageLocation, StorageUnit

    loc = StorageLocation(name="Testarkivet", kind="physical")
    session.add(loc)
    session.flush()
    unit = StorageUnit(location_id=loc.id, name="Pärm 1")
    session.add(unit)
    session.flush()
    piece = Piece(title="Testnot", created_by=admin_user.id)
    session.add(piece)
    session.flush()
    placement = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=5)
    session.add(placement)
    session.commit()
    session.refresh(piece)
    return piece


def test_piece_has_public_id(piece_with_qr):
    assert piece_with_qr.public_id is not None
    assert len(piece_with_qr.public_id) == 32


def test_qr_png_endpoint(logged_in_client, piece_with_qr):
    r = logged_in_client.get(f"/pieces/{piece_with_qr.id}/qr.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_p_redirect_to_piece(logged_in_client, piece_with_qr):
    r = logged_in_client.get(f"/p/{piece_with_qr.public_id}")
    assert r.status_code in (302, 303)
    assert r.headers["location"] == f"/pieces/{piece_with_qr.id}"


def test_p_unknown_id(logged_in_client):
    r = logged_in_client.get("/p/deadbeefdeadbeefdeadbeefdeadbeef")
    assert r.status_code == 404


def test_kiosk_activate_requires_valid_token(client):
    """Ogiltig token ger 403 + fail-vyn."""
    r = client.get("/kiosk/activate?token=trash")
    assert r.status_code == 403
    assert "Aktivering misslyckades" in r.text


def test_kiosk_routes_blocked_without_activation(logged_in_client):
    """Utan kiosk-session ska /kiosk ge 403 även för inloggad admin."""
    r = logged_in_client.get("/kiosk")
    assert r.status_code == 403


def test_kiosk_input_page_unauthed(kiosk_session_client):
    """Aktiverad kiosk utan PIN-låntagare visar login-formuläret."""
    r = kiosk_session_client.get("/kiosk")
    assert r.status_code == 200
    assert "Skanna din profil-QR" in r.text


def test_kiosk_piece_redirects_when_unauthed(kiosk_session_client, piece_with_qr):
    r = kiosk_session_client.get(f"/kiosk/{piece_with_qr.public_id}")
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/kiosk"


def test_kiosk_pin_auth_flow(
    kiosk_session_client, session: Session, piece_with_qr, admin_user
):
    from app.auth import hash_pin
    from app.models import Loan, LoanBatch, LoanBatchStatus

    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()

    csrf = get_csrf(kiosk_session_client, "/kiosk")
    r = kiosk_session_client.post(
        "/kiosk/auth",
        data={"csrf_token": csrf, "username": "testadmin", "pin": "1234"},
    )
    assert r.status_code in (302, 303)

    r = kiosk_session_client.get("/kiosk")
    assert "Skanna eller sök" in r.text
    assert "testadmin" in r.text

    r = kiosk_session_client.get(f"/kiosk/{piece_with_qr.public_id}")
    assert r.status_code == 200
    assert "Nytt utlån" in r.text


def test_kiosk_qr_auth(kiosk_session_client, session, admin_user):
    csrf = get_csrf(kiosk_session_client, "/kiosk")
    r = kiosk_session_client.post(
        "/kiosk/qr-auth",
        data={"csrf_token": csrf, "token": admin_user.kiosk_token},
    )
    assert r.status_code in (302, 303)
    r = kiosk_session_client.get("/kiosk")
    assert "Skanna eller sök" in r.text
    assert "testadmin" in r.text


def test_kiosk_qr_auth_invalid_token(kiosk_session_client):
    csrf = get_csrf(kiosk_session_client, "/kiosk")
    r = kiosk_session_client.post(
        "/kiosk/qr-auth",
        data={"csrf_token": csrf, "token": "deadbeef" * 4},
    )
    assert r.status_code in (302, 303)
    r = kiosk_session_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text  # ej autentiserad


def test_kiosk_rate_limit(kiosk_session_client, session, admin_user):
    from app.auth import hash_pin
    from app.utils.ratelimit import reset_all_for_tests

    reset_all_for_tests()
    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()

    for _ in range(5):
        csrf = get_csrf(kiosk_session_client, "/kiosk")
        kiosk_session_client.post(
            "/kiosk/auth",
            data={"csrf_token": csrf, "username": "testadmin", "pin": "9999"},
        )

    csrf = get_csrf(kiosk_session_client, "/kiosk")
    kiosk_session_client.post(
        "/kiosk/auth",
        data={"csrf_token": csrf, "username": "testadmin", "pin": "1234"},
    )
    r = kiosk_session_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text

    reset_all_for_tests()


def test_kiosk_wrong_pin(kiosk_session_client, session, admin_user):
    from app.auth import hash_pin

    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()

    csrf = get_csrf(kiosk_session_client, "/kiosk")
    r = kiosk_session_client.post(
        "/kiosk/auth",
        data={"csrf_token": csrf, "username": "testadmin", "pin": "9999"},
    )
    assert r.status_code in (302, 303)
    r = kiosk_session_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text


def test_kiosk_search_endpoint(kiosk_session_client, piece_with_qr):
    r = kiosk_session_client.get("/kiosk/search?q=Testnot")
    assert r.status_code == 200
    assert "Testnot" in r.text


def test_kiosk_search_min_length(kiosk_session_client, piece_with_qr):
    r = kiosk_session_client.get("/kiosk/search?q=T")
    assert r.status_code == 200
    assert "Testnot" not in r.text


def test_kiosk_location_filter(client, session: Session, admin_user):
    """Kiosk bunden till plats X filtrerar sök till noter på X och varnar
    vid scan av noter utanför."""
    from app.auth import hash_pin
    from app.models import Kiosk, Piece, PiecePlacement, StorageLocation, StorageUnit
    import re

    loc_kiosk = StorageLocation(name="Kioskhylla", kind="physical")
    loc_other = StorageLocation(name="Skåp B", kind="physical")
    session.add_all([loc_kiosk, loc_other])
    session.flush()
    unit_kiosk = StorageUnit(location_id=loc_kiosk.id, name="K1")
    unit_other = StorageUnit(location_id=loc_other.id, name="B1")
    session.add_all([unit_kiosk, unit_other])
    session.flush()
    p_here = Piece(title="Här", created_by=admin_user.id)
    p_other = Piece(title="Annorstädes", created_by=admin_user.id)
    session.add_all([p_here, p_other])
    session.flush()
    session.add(PiecePlacement(piece_id=p_here.id, storage_unit_id=unit_kiosk.id, copies=1))
    session.add(PiecePlacement(piece_id=p_other.id, storage_unit_id=unit_other.id, copies=1))

    # Skapa en kiosk knuten till loc_kiosk + ge admin en PIN
    kiosk = Kiosk(name="Platsbundet", location_id=loc_kiosk.id)
    session.add(kiosk)
    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()
    session.refresh(p_here)
    session.refresh(p_other)
    session.refresh(kiosk)

    # Logga in admin + aktivera den platsbundna kiosken
    r = client.get("/login")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    client.post("/login", data={"username": "testadmin", "password": "testpass", "csrf_token": m.group(1)})
    r = client.get(f"/kiosk/activate?token={kiosk.access_token}")
    assert r.status_code in (302, 303)

    # Sök på "Här" visar noten normalt
    r = client.get("/kiosk/search?q=Här")
    assert "Här" in r.text
    # Sök på "Annorstädes" visar noten MEN med varning att den inte finns på platsen
    r = client.get("/kiosk/search?q=Annorstädes")
    assert "Annorstädes" in r.text
    assert "Finns inte på Kioskhylla" in r.text

    # PIN-autentisera och kolla piece-vy
    csrf = get_csrf(client, "/kiosk")
    client.post("/kiosk/auth", data={"csrf_token": csrf, "username": "testadmin", "pin": "1234"})

    r = client.get(f"/kiosk/{p_other.public_id}")
    assert r.status_code == 200
    assert "finns inte på Kioskhylla" in r.text
    assert "Nytt utlån" not in r.text

    r = client.get(f"/kiosk/{p_here.public_id}")
    assert "finns inte på" not in r.text
    assert "Nytt utlån" in r.text


def test_kiosk_deactivate(kiosk_session_client):
    csrf = get_csrf(kiosk_session_client, "/kiosk")
    r = kiosk_session_client.post(
        "/kiosk/deactivate", data={"csrf_token": csrf}
    )
    assert r.status_code in (302, 303)
    # Efter deaktivering - /kiosk blockerar igen
    r = kiosk_session_client.get("/kiosk")
    assert r.status_code == 403


def test_qr_labels_pdf(logged_in_client, piece_with_qr):
    r = logged_in_client.get("/pieces/qr-labels.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_qr_labels_filter_by_unit(logged_in_client, session: Session, admin_user):
    from app.models import Piece, PiecePlacement, StorageLocation, StorageUnit

    loc = StorageLocation(name="Arkiv A", kind="physical")
    session.add(loc)
    session.flush()
    unit_a = StorageUnit(location_id=loc.id, name="A1")
    unit_b = StorageUnit(location_id=loc.id, name="B1")
    session.add_all([unit_a, unit_b])
    session.flush()
    p_a = Piece(title="I A", created_by=admin_user.id)
    p_b = Piece(title="I B", created_by=admin_user.id)
    session.add_all([p_a, p_b])
    session.flush()
    session.add(PiecePlacement(piece_id=p_a.id, storage_unit_id=unit_a.id, copies=1))
    session.add(PiecePlacement(piece_id=p_b.id, storage_unit_id=unit_b.id, copies=1))
    session.commit()

    r = logged_in_client.get(f"/pieces/qr-labels?unit={unit_a.id}")
    assert r.status_code == 200
    assert "I A" in r.text
    assert "I B" not in r.text


def test_set_pin_via_profile(logged_in_client, session, admin_user):
    from app.models import User as UserModel

    csrf = get_csrf(logged_in_client, "/profile")
    r = logged_in_client.post(
        "/profile/pin",
        data={"csrf_token": csrf, "pin": "5678"},
    )
    assert r.status_code in (302, 303)
    session.expire_all()
    refreshed = session.exec(select(UserModel).where(UserModel.id == admin_user.id)).first()
    assert refreshed.pin_hash is not None

    csrf = get_csrf(logged_in_client, "/profile")
    logged_in_client.post(
        "/profile/pin",
        data={"csrf_token": csrf, "pin": "abcd"},
    )
    r = logged_in_client.get("/profile")
    assert "måste vara 4-8 siffror" in r.text


def test_profile_kiosk_qr_endpoint(logged_in_client, admin_user):
    r = logged_in_client.get("/profile/kiosk-qr.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
