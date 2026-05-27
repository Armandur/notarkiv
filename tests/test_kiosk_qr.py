"""Tester för QR-koder, /p/{uuid}-lookup och kioskvy."""

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
    """Nya pieces ska få public_id automatiskt via default_factory."""
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


def test_p_redirect_to_kiosk(logged_in_client, piece_with_qr):
    r = logged_in_client.get(f"/p/{piece_with_qr.public_id}?kiosk=1")
    assert r.status_code in (302, 303)
    assert "/kiosk/" in r.headers["location"]


def test_p_unknown_id(logged_in_client):
    r = logged_in_client.get("/p/deadbeefdeadbeefdeadbeefdeadbeef")
    assert r.status_code == 404


def test_kiosk_input_page_unauthed(logged_in_client):
    """Utan PIN-autentisering ska kiosken visa login-formuläret."""
    r = logged_in_client.get("/kiosk")
    assert r.status_code == 200
    assert "Skanna din profil-QR" in r.text


def test_kiosk_piece_redirects_when_unauthed(logged_in_client, piece_with_qr):
    """Piece-vy i kiosk ska redirecta till /kiosk när ingen är PIN-autentiserad."""
    r = logged_in_client.get(f"/kiosk/{piece_with_qr.public_id}")
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/kiosk"


def test_kiosk_pin_auth_flow(logged_in_client, session: Session, piece_with_qr, admin_user):
    """Sätt PIN, autentisera i kiosken, lägg i korg, registrera utlån."""
    from app.auth import hash_pin
    from app.models import Loan, LoanBatch, LoanBatchStatus

    # Sätt PIN på admin (vår testanvändare)
    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()

    # Autentisera
    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/kiosk/auth",
        data={"csrf_token": csrf, "username": "testadmin", "pin": "1234"},
    )
    assert r.status_code in (302, 303)

    # Nu ska kioskvyn visa "skanna" och borrower
    r = logged_in_client.get("/kiosk")
    assert "Skanna en not" in r.text
    assert "testadmin" in r.text

    # Piece-vyn ska funka
    r = logged_in_client.get(f"/kiosk/{piece_with_qr.public_id}")
    assert r.status_code == 200
    assert "Lägg till i utlån" in r.text

    # Lägg till i kiosk-cart
    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/loans/cart/add",
        data={
            "csrf_token": csrf,
            "placement_id": session.exec(select(Loan).where(Loan.id == 0)).first() and None,  # dummy
        },
    )
    # Hämta placement_id korrekt
    from app.models import PiecePlacement
    placement = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece_with_qr.id)
    ).first()
    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/loans/cart/add",
        data={
            "csrf_token": csrf,
            "placement_id": placement.id,
            "return_to": "/kiosk",
            "copies": 1,
        },
    )
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/kiosk"  # return_to respekterades

    # Cart är knuten till borrower (admin_user), inte till någon kiosk-bot
    cart = session.exec(
        select(LoanBatch)
        .where(LoanBatch.status == LoanBatchStatus.CART)
        .where(LoanBatch.created_by == admin_user.id)
    ).first()
    assert cart is not None
    loans = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    assert len(loans) == 1

    # Checkout direkt utan att gå via /loans/cart
    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/kiosk/checkout",
        data={"csrf_token": csrf, "name": "Test-kioskutlån"},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    cart = session.exec(
        select(LoanBatch).where(LoanBatch.id == cart.id)
    ).first()
    assert cart.status == LoanBatchStatus.ACTIVE
    assert cart.borrower_username if hasattr(cart, "borrower_username") else cart.borrower_name == "testadmin"

    # Loan-raden ska vara picked_up_at satt (kiosk hoppar pickup)
    loan = session.exec(select(Loan).where(Loan.batch_id == cart.id)).first()
    assert loan.picked_up_at is not None

    # Auto-logout: nästa GET /kiosk visar login-formuläret igen
    r = logged_in_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text


def test_kiosk_qr_auth(logged_in_client, session, admin_user):
    """QR-token-baserad inloggning."""
    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/kiosk/qr-auth",
        data={"csrf_token": csrf, "token": admin_user.kiosk_token},
    )
    assert r.status_code in (302, 303)

    r = logged_in_client.get("/kiosk")
    assert "Skanna en not" in r.text
    assert "testadmin" in r.text


def test_kiosk_qr_auth_invalid_token(logged_in_client, session, admin_user):
    """Ogiltig token autentiserar inte."""
    from app.utils.ratelimit import reset_all_for_tests
    reset_all_for_tests()

    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/kiosk/qr-auth",
        data={"csrf_token": csrf, "token": "deadbeef" * 4},
    )
    assert r.status_code in (302, 303)
    r = logged_in_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text  # ej autentiserad


def test_kiosk_rate_limit(logged_in_client, session, admin_user):
    """Fem fel i rad ska låsa ut."""
    from app.auth import hash_pin
    from app.utils.ratelimit import reset_all_for_tests

    reset_all_for_tests()
    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()

    # 5 fel försök
    for _ in range(5):
        csrf = get_csrf(logged_in_client, "/kiosk")
        logged_in_client.post(
            "/kiosk/auth",
            data={"csrf_token": csrf, "username": "testadmin", "pin": "9999"},
        )

    # 6:e försöket - även med korrekt PIN ska det blockeras
    csrf = get_csrf(logged_in_client, "/kiosk")
    logged_in_client.post(
        "/kiosk/auth",
        data={"csrf_token": csrf, "username": "testadmin", "pin": "1234"},
    )
    r = logged_in_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text  # fortfarande ej autentiserad

    reset_all_for_tests()


def test_profile_kiosk_qr_endpoint(logged_in_client, admin_user):
    """QR-bilden för profilen ska genereras."""
    r = logged_in_client.get("/profile/kiosk-qr.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_kiosk_wrong_pin(logged_in_client, session, admin_user):
    """Fel PIN ska inte autentisera."""
    from app.auth import hash_pin

    admin_user.pin_hash = hash_pin("1234")
    session.add(admin_user)
    session.commit()

    csrf = get_csrf(logged_in_client, "/kiosk")
    r = logged_in_client.post(
        "/kiosk/auth",
        data={"csrf_token": csrf, "username": "testadmin", "pin": "9999"},
    )
    assert r.status_code in (302, 303)

    # Fortfarande utloggad
    r = logged_in_client.get("/kiosk")
    assert "Skanna din profil-QR" in r.text


def test_set_pin_via_profile(logged_in_client, session, admin_user):
    """Profilsidan ska låta användaren sätta sin PIN."""
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

    # Bara siffror tillåtna
    csrf = get_csrf(logged_in_client, "/profile")
    r = logged_in_client.post(
        "/profile/pin",
        data={"csrf_token": csrf, "pin": "abcd"},
    )
    # Ska redirecta (fail) men pin-hash kvarstår från förra setet
    r = logged_in_client.get("/profile")
    assert "måste vara 4-8 siffror" in r.text


def test_qr_labels_page(logged_in_client, piece_with_qr):
    r = logged_in_client.get("/pieces/qr-labels")
    assert r.status_code == 200
    assert "Testnot" in r.text
    assert f"/pieces/{piece_with_qr.id}/qr.png" in r.text


def test_qr_labels_pdf(logged_in_client, piece_with_qr):
    r = logged_in_client.get("/pieces/qr-labels.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_qr_labels_filter_by_unit(logged_in_client, session: Session, admin_user):
    """qr-labels med ?unit=X ska bara visa noter med placering där."""
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
