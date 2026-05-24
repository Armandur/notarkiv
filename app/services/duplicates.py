"""Hitta möjliga dubletter när en ny skanning ska sparas som piece.

Använder rapidfuzz för titel-matchning. Edition_number ger högsta säkerhet -
samma förlagsnummer på två noter är nästan alltid samma utgåva.
"""

from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlmodel import Session, select

from app.models import Piece


class DuplicateCandidate(BaseModel):
    piece_id: int
    title: str
    contributors_cache: str | None
    publisher: str | None
    edition_number: str | None
    score: int  # 0-100, högre = säkrare match


def find_duplicates(
    session: Session,
    *,
    title: str | None,
    composer: str | None = None,
    edition_number: str | None = None,
    limit: int = 3,
    threshold: int = 60,
) -> list[DuplicateCandidate]:
    """Hitta upp till `limit` möjliga dubletter med score >= threshold."""
    if not title or not title.strip():
        return []

    target_title = title.strip().lower()
    target_composer = (composer or "").strip().lower()
    target_edition = (edition_number or "").strip().lower()

    # Vi laddar alla pieces - för 200-1000 noter är detta trivialt och undviker
    # SQL-trigram-komplexitet. Skala om över 10k noter blir relevant.
    all_pieces = session.exec(select(Piece)).all()

    scored: list[DuplicateCandidate] = []
    for p in all_pieces:
        score = _score(p, target_title, target_composer, target_edition)
        if score < threshold:
            continue
        scored.append(
            DuplicateCandidate(
                piece_id=p.id,
                title=p.title,
                contributors_cache=p.contributors_cache,
                publisher=p.publisher,
                edition_number=p.edition_number,
                score=score,
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]


def _score(piece: Piece, target_title: str, target_composer: str, target_edition: str) -> int:
    title_score = fuzz.ratio((piece.title or "").lower(), target_title)

    composer_score = 0
    if target_composer and piece.contributors_cache:
        composer_score = fuzz.partial_ratio(
            piece.contributors_cache.lower(), target_composer
        )

    # Edition är "tiebreaker" - om exakt match, kraftigt höjt score
    edition_bonus = 0
    if target_edition and piece.edition_number:
        if piece.edition_number.strip().lower() == target_edition:
            edition_bonus = 30

    # Viktning: titel viktigast, kompositör som modifierare, edition som bonus
    if target_composer:
        base = int(title_score * 0.65 + composer_score * 0.35)
    else:
        base = title_score

    return min(100, base + edition_bonus)
