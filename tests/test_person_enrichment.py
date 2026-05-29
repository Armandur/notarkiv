"""Verifierar att MB-berikning kö:as automatiskt när pieces sparas."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from tests.conftest import get_csrf


@pytest.fixture
def mock_arq_pool(monkeypatch):
    """Mocka arq-poolen så vi kan registrera vilka jobb som kö:as."""

    class FakePool:
        def __init__(self):
            self.jobs: list[tuple[str, tuple]] = []

        async def enqueue_job(self, name: str, *args):
            self.jobs.append((name, args))

    pool = FakePool()

    async def _get_pool():
        return pool

    monkeypatch.setattr("app.tasks.get_pool", _get_pool)
    return pool


def test_new_piece_triggers_person_enrichment(
    logged_in_client, session: Session, mock_arq_pool
):
    """När en piece skapas manuellt ska arq-jobb kö:as för composer/arranger
    utan MBID."""
    csrf = get_csrf(logged_in_client, "/pieces/new")
    r = logged_in_client.post(
        "/pieces/new",
        data={
            "csrf_token": csrf,
            "title": "Testkomposition",
            "composer": "Felix Mendelssohn",
            "arranger": "",
            "lyricist": "",
            "composer_sort": "Mendelssohn, Felix",
            "arranger_sort": "",
            "lyricist_sort": "",
            "language": "",
            "publisher": "",
            "edition_number": "",
        },
    )
    assert r.status_code in (302, 303), f"create: {r.status_code} {r.text[:300]}"

    # Person ska ha skapats utan MBID
    from app.models import Person
    person = session.exec(select(Person).where(Person.name == "Felix Mendelssohn")).first()
    assert person is not None
    assert person.musicbrainz_artist_id is None

    # Enrich-jobbet ska ha kö:ats för den personen
    assert any(
        name == "enrich_person_job" and args == (person.id,)
        for name, args in mock_arq_pool.jobs
    ), f"enrich_person_job kö:ades inte: {mock_arq_pool.jobs}"


def test_pieces_new_multi_composer_tags(
    logged_in_client, session: Session, mock_arq_pool
):
    """POST /pieces/new med flera composer-värden (Tom Select-multi) ska
    skapa en Person per tag och länka via PieceContributor."""
    from app.models import ContributorRole, Person, Piece, PieceContributor

    csrf = get_csrf(logged_in_client, "/pieces/new")
    r = logged_in_client.post(
        "/pieces/new",
        data={
            "csrf_token": csrf,
            "title": "Duo-stycket",
            "composer": ["Felix Mendelssohn", "Hugo Distler"],
            "publisher": "Verbum",
        },
    )
    assert r.status_code in (302, 303), r.text[:300]

    piece = session.exec(select(Piece).where(Piece.title == "Duo-stycket")).first()
    assert piece is not None
    contribs = session.exec(
        select(PieceContributor).where(PieceContributor.piece_id == piece.id)
    ).all()
    composer_ids = [pc.person_id for pc in contribs if pc.role == ContributorRole.COMPOSER]
    assert len(composer_ids) == 2
    names = sorted(p.name for p in session.exec(
        select(Person).where(Person.id.in_(composer_ids))
    ).all())
    assert names == ["Felix Mendelssohn", "Hugo Distler"]
    # publisher: registrerad både som fritextfält och som FK
    assert piece.publisher == "Verbum"
    assert piece.publisher_id is not None


def test_person_with_mbid_not_re_enriched(
    logged_in_client, session: Session, mock_arq_pool
):
    """Person som redan har MBID ska INTE kö:as om för enrichment."""
    from app.auth import hash_password
    from app.models import ContributorRole, Person, PieceContributor, Piece, User

    # Skapa person med MBID redan
    person = Person(
        name="Hugo Distler",
        sort_name="Distler, Hugo",
        musicbrainz_artist_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    session.add(person)
    session.commit()

    csrf = get_csrf(logged_in_client, "/pieces/new")
    r = logged_in_client.post(
        "/pieces/new",
        data={
            "csrf_token": csrf,
            "title": "Annan",
            "composer": "Hugo Distler",
            "arranger": "",
            "lyricist": "",
            "composer_sort": "",
            "arranger_sort": "",
            "lyricist_sort": "",
            "language": "",
            "publisher": "",
            "edition_number": "",
        },
    )
    assert r.status_code in (302, 303)

    # Personen med MBID ska INTE finnas i kön
    assert not any(
        name == "enrich_person_job" and args == (person.id,)
        for name, args in mock_arq_pool.jobs
    )
