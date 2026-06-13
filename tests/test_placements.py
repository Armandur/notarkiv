"""Tester för placeringar - sammanfogning bevarar okänt antal (None)."""

from __future__ import annotations

from sqlmodel import Session, select

from tests.conftest import get_csrf


def test_merge_placements_preserves_none_copies(
    logged_in_client, session: Session, admin_user
):
    """Slås två digitala placeringar (copies=None) ihop ska resultatet förbli
    None - inte bli 0 (regression för granskningsfynd #5)."""
    from app.models import Piece, PiecePlacement, StorageLocation, StorageUnit

    loc = StorageLocation(name="Digital plats", kind="digital")
    session.add(loc)
    session.flush()
    u1 = StorageUnit(location_id=loc.id, name="Mapp A")
    u2 = StorageUnit(location_id=loc.id, name="Mapp B")
    session.add(u1)
    session.add(u2)
    session.flush()

    piece = Piece(title="Digital not", created_by=1)
    session.add(piece)
    session.flush()
    p1 = PiecePlacement(piece_id=piece.id, storage_unit_id=u1.id, copies=None)
    p2 = PiecePlacement(piece_id=piece.id, storage_unit_id=u2.id, copies=None)
    session.add(p1)
    session.add(p2)
    session.commit()
    piece_id, p1_id, u2_id = piece.id, p1.id, u2.id

    csrf = get_csrf(logged_in_client, f"/pieces/{piece_id}")
    # Flytta p1 till u2 -> merge med p2 (båda copies=None)
    r = logged_in_client.post(
        f"/pieces/{piece_id}/placements/{p1_id}/update",
        data={"csrf_token": csrf, "storage_unit_id": str(u2_id)},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    remaining = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id == piece_id)
    ).all()
    assert len(remaining) == 1, "sammanfogning ska lämna en placering"
    assert remaining[0].copies is None, "None ska bevaras, inte bli 0"
