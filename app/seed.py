"""Läs YAML-filer från seed_data/ och fyll databasen."""

from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from sqlmodel import Session, select

from app.auth import hash_password
from app.config import settings
from app.db import engine
from app.models import StorageLocation, StorageUnit, Tag, UnitKind, User
from app.models.storage import LocationKind
from app.models.tag import TagKind
from app.models.user import Role

SEED_DIR = Path("seed_data")


def seed_all(clear_pieces: bool = False) -> None:
    with Session(engine) as session:
        _seed_tags(session)
        _seed_unit_kinds(session)
        _seed_users(session)
        _seed_storage_locations(session)

        if settings.initial_admin_username and settings.initial_admin_password:
            _ensure_initial_admin(session)

        if clear_pieces:
            logger.warning("--clear-pieces ignoreras i MVP - inga noter finns i seed än")

        session.commit()
    logger.info("Seed klar")


def _seed_unit_kinds(session: Session) -> None:
    data = _load_yaml("unit_kinds.yaml")
    if not data:
        return
    added = 0
    for name in data:
        existing = session.exec(select(UnitKind).where(UnitKind.name == name)).first()
        if existing:
            continue
        session.add(UnitKind(name=name))
        added += 1
    logger.info("UnitKinds: skapade {} nya", added)


def _load_yaml(filename: str) -> Any:
    path = SEED_DIR / filename
    if not path.exists():
        logger.debug("Ingen seed-fil: {}", path)
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _seed_tags(session: Session) -> None:
    data = _load_yaml("tags.yaml")
    if not data:
        return
    added = 0
    for row in data:
        existing = session.exec(select(Tag).where(Tag.name == row["name"])).first()
        if existing:
            continue
        session.add(
            Tag(
                name=row["name"],
                kind=TagKind(row.get("kind", "free")),
                sort_order=row.get("sort_order", 0),
            )
        )
        added += 1
    logger.info("Tags: skapade {} nya", added)


def _seed_users(session: Session) -> None:
    data = _load_yaml("users.yaml")
    if not data:
        return
    added = 0
    for row in data:
        existing = session.exec(select(User).where(User.username == row["username"])).first()
        if existing:
            continue
        session.add(
            User(
                username=row["username"],
                email=row.get("email"),
                password_hash=hash_password(row["password"]),
                role=Role(row.get("role", "reader")),
                must_change_password=row.get("must_change_password", False),
            )
        )
        added += 1
    logger.info("Users: skapade {} nya", added)


def _ensure_initial_admin(session: Session) -> None:
    username = settings.initial_admin_username
    assert username and settings.initial_admin_password
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing:
        return
    session.add(
        User(
            username=username,
            password_hash=hash_password(settings.initial_admin_password),
            role=Role.ADMIN,
            must_change_password=True,
        )
    )
    logger.info("Initial admin '{}' skapad (måste byta lösenord)", username)


def _seed_storage_locations(session: Session) -> None:
    data = _load_yaml("storage_locations.yaml")
    if not data:
        return
    added_locs = 0
    added_units = 0
    for row in data:
        existing_loc = session.exec(
            select(StorageLocation).where(StorageLocation.name == row["name"])
        ).first()
        if existing_loc:
            location = existing_loc
        else:
            location = StorageLocation(
                name=row["name"],
                kind=LocationKind(row.get("kind", "physical")),
                description=row.get("description"),
                sort_order=row.get("sort_order", 0),
            )
            session.add(location)
            session.flush()
            added_locs += 1

        for unit_row in row.get("units", []):
            added_units += _seed_unit_recursive(session, location.id, None, unit_row)

    logger.info("Storage: {} locations och {} units skapade", added_locs, added_units)


def _seed_unit_recursive(
    session: Session,
    location_id: int,
    parent_id: int | None,
    unit_row: dict,
) -> int:
    existing = session.exec(
        select(StorageUnit)
        .where(StorageUnit.location_id == location_id)
        .where(StorageUnit.parent_id == parent_id)
        .where(StorageUnit.name == unit_row["name"])
    ).first()

    if existing:
        unit = existing
        added = 0
    else:
        kind_id = _resolve_kind_id(session, unit_row.get("kind"))
        unit = StorageUnit(
            location_id=location_id,
            parent_id=parent_id,
            name=unit_row["name"],
            kind_id=kind_id,
            url=unit_row.get("url"),
            sort_order=unit_row.get("sort_order", 0),
            notes=unit_row.get("notes"),
        )
        session.add(unit)
        session.flush()
        added = 1

    for child in unit_row.get("children", []):
        added += _seed_unit_recursive(session, location_id, unit.id, child)

    return added


def _resolve_kind_id(session: Session, kind_name: str | None) -> int | None:
    """Lookup kind by name. Skapar nytt UnitKind om det inte finns."""
    if not kind_name:
        return None
    existing = session.exec(select(UnitKind).where(UnitKind.name == kind_name)).first()
    if existing:
        return existing.id
    new_kind = UnitKind(name=kind_name)
    session.add(new_kind)
    session.flush()
    return new_kind.id
