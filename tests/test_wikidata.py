"""Tests för wikidata-helpers. Mockar Wikidata API."""

from __future__ import annotations

import pytest


def _entity_human(qid: str = "Q42", mbid: str | None = None, birth_year: int = 1942, death_year: int | None = None) -> dict:
    """Bygg en minimal Wikidata-entity-respons."""
    claims = {
        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}],
        "P569": [{
            "mainsnak": {"datavalue": {
                "value": {"time": f"+{birth_year:04d}-03-15T00:00:00Z", "precision": 11},
            }},
        }],
    }
    if death_year:
        claims["P570"] = [{
            "mainsnak": {"datavalue": {
                "value": {"time": f"+{death_year:04d}-05-20T00:00:00Z", "precision": 11},
            }},
        }]
    if mbid:
        claims["P434"] = [{"mainsnak": {"datavalue": {"value": mbid}}}]
    return {
        "id": qid,
        "claims": claims,
        "sitelinks": {
            "svwiki": {"title": "Test Person"},
            "enwiki": {"title": "Test Person"},
        },
    }


def test_is_human_detection():
    from app.services.wikidata import _is_human

    assert _is_human(_entity_human())
    assert not _is_human({"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q12345"}}}}]}})
    assert not _is_human({})


def test_extract_birth_death_year():
    from app.services.wikidata import extract_birth_year, extract_death_year

    e = _entity_human(birth_year=1809, death_year=1847)
    assert extract_birth_year(e) == 1809
    assert extract_death_year(e) == 1847

    alive = _entity_human(birth_year=1980)
    assert extract_birth_year(alive) == 1980
    assert extract_death_year(alive) is None


def test_extract_birth_date_with_precision():
    from app.services.wikidata import _parse_wd_date

    full = {"time": "+1809-02-03T00:00:00Z", "precision": 11}
    assert _parse_wd_date(full) == (1809, 2, 3)

    year_only = {"time": "+1809-01-01T00:00:00Z", "precision": 9}
    assert _parse_wd_date(year_only) == (1809, None, None)

    junk = {"time": "garbage", "precision": 11}
    assert _parse_wd_date(junk) == (None, None, None)


def test_extract_musicbrainz_id():
    from app.services.wikidata import extract_musicbrainz_id

    e = _entity_human(mbid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert extract_musicbrainz_id(e) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert extract_musicbrainz_id(_entity_human()) is None


def test_extract_wikipedia_url_prefers_lang():
    from app.services.wikidata import extract_wikipedia_url

    e = _entity_human()
    assert extract_wikipedia_url(e, "sv") == "https://sv.wikipedia.org/wiki/Test_Person"
    # Saknas språk → fallback
    e_no_sv = {"sitelinks": {"enwiki": {"title": "Test Person"}}}
    assert extract_wikipedia_url(e_no_sv, "sv") == "https://en.wikipedia.org/wiki/Test_Person"


def test_extract_image_filename():
    from app.services.wikidata import extract_image_filename

    e = {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Felix_Mendelssohn.jpg"}}}]}}
    assert extract_image_filename(e) == "Felix_Mendelssohn.jpg"
    assert extract_image_filename({}) is None
