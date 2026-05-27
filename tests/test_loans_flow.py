"""End-to-end-test för bulk-utlån: cart → checkout → pickup → activate → return."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from tests.conftest import get_csrf


@pytest.fixture
def piece_with_placement(session: Session, admin_user):
    """Skapar en piece med en placering på en storage_unit."""
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
    placement = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=3)
    session.add(placement)
    session.commit()
    session.refresh(placement)
    return placement


def test_cart_view_empty(logged_in_client):
    """Tom korg ska rendera utan fel."""
    r = logged_in_client.get("/loans/cart")
    assert r.status_code == 200
    assert "Korgen är tom" in r.text


def test_full_bulk_loan_flow(logged_in_client, session: Session, piece_with_placement):
    """Hela bulk-utlåningsflödet end-to-end."""
    from app.models import Loan, LoanBatch, LoanBatchStatus

    # 1. Lägg i korg
    csrf = get_csrf(logged_in_client, "/pieces")
    r = logged_in_client.post(
        "/loans/cart/add",
        data={
            "csrf_token": csrf,
            "placement_id": piece_with_placement.id,
            "copies": 2,
        },
    )
    assert r.status_code in (302, 303), f"cart/add: {r.status_code} {r.text[:200]}"

    # 2. Korgen ska visa noten
    r = logged_in_client.get("/loans/cart")
    assert r.status_code == 200
    assert "Testnot" in r.text
    assert "Testarkivet" in r.text or "Pärm 1" in r.text

    # Hitta cart-batchen
    cart = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.CART)
    ).first()
    assert cart is not None
    cart_loans = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    assert len(cart_loans) == 1
    assert cart_loans[0].copies == 2

    # 3. Checkout
    csrf = get_csrf(logged_in_client, "/loans/cart")
    r = logged_in_client.post(
        "/loans/cart/checkout",
        data={
            "csrf_token": csrf,
            "name": "Konsert 14 juni",
            "borrower_user_id": "1",  # admin_user är första rad
            "expected_return": "2026-06-15",
            "notes": "Testnotering",
        },
    )
    assert r.status_code in (302, 303), f"checkout: {r.status_code} {r.text[:200]}"
    assert "/pickup" in r.headers.get("location", "")

    session.expire_all()
    batch = session.get(LoanBatch, cart.id)
    assert batch.status == LoanBatchStatus.PICKING
    assert batch.name == "Konsert 14 juni"
    assert batch.borrower_name == "testadmin"

    # 4. Pickup-vyn ska rendera
    r = logged_in_client.get(f"/loans/batch/{batch.id}/pickup")
    assert r.status_code == 200
    assert "Testnot" in r.text

    # 5. Markera som hämtad
    csrf = get_csrf(logged_in_client, f"/loans/batch/{batch.id}/pickup")
    loan_id = cart_loans[0].id
    r = logged_in_client.post(
        f"/loans/{loan_id}/pickup",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    loan = session.get(Loan, loan_id)
    assert loan.picked_up_at is not None

    # 6. Aktivera batchen
    csrf = get_csrf(logged_in_client, f"/loans/batch/{batch.id}/pickup")
    r = logged_in_client.post(
        f"/loans/batch/{batch.id}/activate",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    batch = session.get(LoanBatch, batch.id)
    assert batch.status == LoanBatchStatus.ACTIVE

    # 7. Batch-detaljvyn ska rendera
    r = logged_in_client.get(f"/loans/batch/{batch.id}")
    assert r.status_code == 200
    assert "Konsert 14 juni" in r.text

    # 8. Återlämna allt
    csrf = get_csrf(logged_in_client, f"/loans/batch/{batch.id}")
    r = logged_in_client.post(
        f"/loans/batch/{batch.id}/return-all",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    batch = session.get(LoanBatch, batch.id)
    assert batch.status == LoanBatchStatus.RETURNED
    assert batch.returned_at is not None
    loan = session.get(Loan, loan_id)
    assert loan.returned_at is not None

    # 9. /loans-översikten ska rendera med batchen i historiken
    r = logged_in_client.get("/loans?show_returned=1")
    assert r.status_code == 200, f"/loans: {r.status_code} {r.text[:300]}"
    assert "Konsert 14 juni" in r.text


def test_loans_list_during_picking(logged_in_client, session: Session, piece_with_placement):
    """/loans måste rendera även när det finns en PICKING-batch med entries."""
    from app.models import LoanBatch, LoanBatchStatus

    # Snabbflöde: cart + checkout
    csrf = get_csrf(logged_in_client, "/pieces")
    logged_in_client.post(
        "/loans/cart/add",
        data={"csrf_token": csrf, "placement_id": piece_with_placement.id, "copies": 1},
    )
    csrf = get_csrf(logged_in_client, "/loans/cart")
    logged_in_client.post(
        "/loans/cart/checkout",
        data={"csrf_token": csrf, "name": "Mid-pick", "borrower_user_id": "1"},
    )

    # /loans måste rendera med pågående pickning
    r = logged_in_client.get("/loans")
    assert r.status_code == 200, f"/loans: {r.status_code} {r.text[:300]}"
    assert "Mid-pick" in r.text
    assert "Testnot" in r.text  # entries[:5]-loopen
    # Verifiera att batchen syns med picking-status
    batch = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.PICKING)
    ).first()
    assert batch is not None


def test_cart_caps_to_available_copies(
    logged_in_client, session: Session, piece_with_placement
):
    """Lägg inte mer i korgen än vad som finns på placeringen."""
    from app.models import Loan, LoanBatch, LoanBatchStatus

    # piece_with_placement har copies=3
    csrf = get_csrf(logged_in_client, "/pieces")

    # Begär 10 ex - ska capas till 3
    r = logged_in_client.post(
        "/loans/cart/add",
        data={
            "csrf_token": csrf,
            "placement_id": piece_with_placement.id,
            "copies": 10,
        },
    )
    assert r.status_code in (302, 303)

    cart = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.CART)
    ).first()
    loan = session.exec(select(Loan).where(Loan.batch_id == cart.id)).first()
    assert loan.copies == 3, "Ska vara cap:at till placement.copies"


def test_cart_max_copies_matches_placement(
    logged_in_client, session: Session, admin_user
):
    """max_copies i cart-vyn ska vara placement.copies, inte mer."""
    from app.models import Piece, PiecePlacement, StorageLocation, StorageUnit
    from app.models import Loan, LoanBatch, LoanBatchStatus

    loc = StorageLocation(name="L", kind="physical")
    session.add(loc)
    session.flush()
    unit = StorageUnit(location_id=loc.id, name="U")
    session.add(unit)
    session.flush()
    piece = Piece(title="Singel", created_by=admin_user.id)
    session.add(piece)
    session.flush()
    placement = PiecePlacement(piece_id=piece.id, storage_unit_id=unit.id, copies=1)
    session.add(placement)
    session.commit()
    session.refresh(placement)

    csrf = get_csrf(logged_in_client, "/pieces")
    logged_in_client.post(
        "/loans/cart/add",
        data={"csrf_token": csrf, "placement_id": placement.id, "copies": 1},
    )

    # Cart-vyn ska visa "av 1", inte "av 2"
    r = logged_in_client.get("/loans/cart")
    assert r.status_code == 200
    assert "av 1" in r.text
    assert "av 2" not in r.text


def test_cart_blocks_when_no_copies_left(
    logged_in_client, session: Session, piece_with_placement
):
    """När allt redan är i korg/utlånat ska nya tillägg blockeras."""
    from app.models import Loan, LoanBatch, LoanBatchStatus

    # Fyll först korgen med alla 3 ex
    csrf = get_csrf(logged_in_client, "/pieces")
    logged_in_client.post(
        "/loans/cart/add",
        data={"csrf_token": csrf, "placement_id": piece_with_placement.id, "copies": 3},
    )
    cart = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.CART)
    ).first()
    loans_before = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    assert len(loans_before) == 1

    # Försök lägga till igen via update (ska inte gå upp över 3)
    loan = loans_before[0]
    csrf = get_csrf(logged_in_client, "/loans/cart")
    r = logged_in_client.post(
        f"/loans/cart/{loan.id}/update",
        data={"csrf_token": csrf, "copies": 50},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    loan = session.exec(select(Loan).where(Loan.id == loan.id)).first()
    assert loan.copies == 3


def test_add_all_from_unit(logged_in_client, session: Session, piece_with_placement):
    """Knappen 'Lägg alla i utlåningskorg' på storage-unit ska lägga alla
    placeringar i korgen, och vara idempotent (skipper dubbletter)."""
    from app.models import Loan, LoanBatch, LoanBatchStatus

    unit_id = piece_with_placement.storage_unit_id
    csrf = get_csrf(logged_in_client, f"/storage/units/{unit_id}")
    r = logged_in_client.post(
        f"/storage/units/{unit_id}/add-all-to-cart",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    cart = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.CART)
    ).first()
    assert cart is not None
    loans = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    assert len(loans) == 1

    # Idempotent: andra anrop får inte dubbla
    csrf = get_csrf(logged_in_client, f"/storage/units/{unit_id}")
    logged_in_client.post(
        f"/storage/units/{unit_id}/add-all-to-cart",
        data={"csrf_token": csrf},
    )
    session.expire_all()
    loans = session.exec(select(Loan).where(Loan.batch_id == cart.id)).all()
    assert len(loans) == 1


def test_placement_search_during_pickup(logged_in_client, session: Session, piece_with_placement):
    """HTMX-sök för att lägga till fler noter under plockning."""
    # Setup: skapa en pågående batch
    csrf = get_csrf(logged_in_client, "/pieces")
    logged_in_client.post(
        "/loans/cart/add",
        data={"csrf_token": csrf, "placement_id": piece_with_placement.id, "copies": 1},
    )
    csrf = get_csrf(logged_in_client, "/loans/cart")
    logged_in_client.post(
        "/loans/cart/checkout",
        data={"csrf_token": csrf, "name": "Test", "borrower_user_id": "1"},
    )
    from app.models import LoanBatch, LoanBatchStatus
    batch = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.PICKING)
    ).first()
    assert batch is not None

    # Sök ska returnera HTML-fragment med träff
    r = logged_in_client.get(f"/loans/batch/{batch.id}/search-placements?q=Testnot")
    assert r.status_code == 200
    assert "Testnot" in r.text
    assert "Lägg till" in r.text

    # Sök med < 2 tecken returnerar tomt
    r = logged_in_client.get(f"/loans/batch/{batch.id}/search-placements?q=T")
    assert r.status_code == 200
    assert "Testnot" not in r.text


def test_not_found_removes_loan(logged_in_client, session: Session, piece_with_placement):
    """Klick på 'Hittade ej' ska radera Loan-raden."""
    from app.models import Loan, LoanBatch, LoanBatchStatus

    # Lägg i korg + checkout (förkortad)
    csrf = get_csrf(logged_in_client, "/pieces")
    logged_in_client.post(
        "/loans/cart/add",
        data={"csrf_token": csrf, "placement_id": piece_with_placement.id, "copies": 1},
    )
    cart = session.exec(
        select(LoanBatch).where(LoanBatch.status == LoanBatchStatus.CART)
    ).first()
    csrf = get_csrf(logged_in_client, "/loans/cart")
    logged_in_client.post(
        "/loans/cart/checkout",
        data={"csrf_token": csrf, "name": "Test", "borrower_user_id": "1"},
    )
    loan = session.exec(select(Loan).where(Loan.batch_id == cart.id)).first()
    loan_id = loan.id

    # Markera "Hittade ej"
    csrf = get_csrf(logged_in_client, f"/loans/batch/{cart.id}/pickup")
    r = logged_in_client.post(
        f"/loans/{loan_id}/not-found",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    assert session.get(Loan, loan_id) is None  # raderad
