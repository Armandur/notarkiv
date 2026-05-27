"""Tests för nästlade taggar och alias."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from tests.conftest import get_csrf


@pytest.fixture
def admin_user_setup(admin_user):
    """admin_user-fixturen räcker - bara för att tydliggöra dependency."""
    return admin_user


def test_create_tag_with_parent(logged_in_client, session: Session, admin_user_setup):
    from app.models import Tag
    from app.models.tag import TagKind

    # Skapa parent först
    parent = Tag(name="Kyrkliga handlingar", kind=TagKind.OCCASION)
    session.add(parent)
    session.commit()
    session.refresh(parent)

    csrf = get_csrf(logged_in_client, "/tags")
    r = logged_in_client.post(
        "/tags",
        data={
            "csrf_token": csrf,
            "name": "Begravning",
            "kind": "occasion",
            "parent_id": str(parent.id),
        },
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    child = session.exec(select(Tag).where(Tag.name == "Begravning")).first()
    assert child is not None
    assert child.parent_id == parent.id


def test_alias_creation_and_filter(
    logged_in_client, session: Session, admin_user_setup
):
    """Filter på /pieces?tag=ALIAS ska matcha alla pieces med själva taggen."""
    from app.models import Piece, PieceTag, Tag, TagAlias
    from app.models.tag import TagKind

    tag = Tag(name="Allhelgona", kind=TagKind.OCCASION)
    session.add(tag)
    session.flush()
    piece = Piece(title="Helgonens dag", created_by=1)
    session.add(piece)
    session.flush()
    session.add(PieceTag(piece_id=piece.id, tag_id=tag.id))
    session.commit()

    # Lägg till alias via UI
    csrf = get_csrf(logged_in_client, "/tags")
    r = logged_in_client.post(
        f"/tags/{tag.id}/alias/add",
        data={"csrf_token": csrf, "name": "Minnesgudstjänst"},
    )
    assert r.status_code in (302, 303)

    session.expire_all()
    aliases = session.exec(select(TagAlias).where(TagAlias.tag_id == tag.id)).all()
    assert len(aliases) == 1
    assert aliases[0].name == "Minnesgudstjänst"

    # Filtrera /pieces med alias-namnet → ska hitta piece
    r = logged_in_client.get("/pieces?tag=Minnesgudstjänst")
    assert r.status_code == 200
    assert "Helgonens dag" in r.text

    # Filtrera med korrekt taggnamn → samma resultat
    r = logged_in_client.get("/pieces?tag=Allhelgona")
    assert r.status_code == 200
    assert "Helgonens dag" in r.text


def test_alias_unique(logged_in_client, session: Session, admin_user_setup):
    """Alias-namn får inte kollidera med taggnamn eller annat alias."""
    from app.models import Tag
    from app.models.tag import TagKind

    tag = Tag(name="Påsk", kind=TagKind.OCCASION)
    session.add(tag)
    session.commit()

    csrf = get_csrf(logged_in_client, "/tags")
    r = logged_in_client.post(
        f"/tags/{tag.id}/alias/add",
        data={"csrf_token": csrf, "name": "Påsk"},  # samma som tag.name
    )
    assert r.status_code in (302, 303)
    # /tags ska visa flash-fel
    r = logged_in_client.get("/tags")
    assert "redan ett taggnamn" in r.text


def test_delete_tag_lifts_children(
    logged_in_client, session: Session, admin_user_setup
):
    """Att radera en parent ska göra children till parent.parent (eller root)."""
    from app.models import Tag
    from app.models.tag import TagKind

    grand = Tag(name="A", kind=TagKind.FREE)
    session.add(grand)
    session.flush()
    parent = Tag(name="B", kind=TagKind.FREE, parent_id=grand.id)
    session.add(parent)
    session.flush()
    child = Tag(name="C", kind=TagKind.FREE, parent_id=parent.id)
    session.add(child)
    session.commit()

    # Spara id-värden innan expunge så de inte triggar refresh på borttagna rader
    parent_id, child_id, grand_id = parent.id, child.id, grand.id

    csrf = get_csrf(logged_in_client, "/tags")
    r = logged_in_client.post(
        f"/tags/{parent_id}/delete",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    session.expunge_all()
    deleted = session.exec(select(Tag).where(Tag.id == parent_id)).first()
    assert deleted is None
    refreshed = session.exec(select(Tag).where(Tag.id == child_id)).first()
    assert refreshed is not None
    assert refreshed.parent_id == grand_id, "Child ska lyftas till grandparent"
