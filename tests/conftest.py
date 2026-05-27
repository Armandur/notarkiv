"""Pytest-fixtures: temp-SQLite per test + TestClient + auth-hjälpare.

Designprincip: varje test får en helt fräsch databas via SQLAlchemy-engine
mot en tmp_path-fil. Vi monkey-patchar app.db.engine + session-deps innan
appen läses in, så alla routes använder test-databasen.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Nolla in-memory rate-limit-räknaren mellan tester."""
    from app.utils.ratelimit import reset_all_for_tests
    reset_all_for_tests()
    yield
    reset_all_for_tests()


@pytest.fixture
def test_engine(tmp_path, monkeypatch):
    """Skapar en engine mot tmp_path/test.db, ersätter app.db.engine för
    init_db() och templates_setup-helpers, och åsidosätter get_session-
    dependencyn i FastAPI-appen så routes också använder test-DB:n."""
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(Engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    from app import db as db_module
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)

    # Patcha modul-engine så lifespan/init_db och templates_setup-helpers
    # (cart_count, active_loans_count m.fl.) använder test-engine.
    monkeypatch.setattr(db_module, "engine", engine)

    # Routes använder Depends(deps.get_session), inte db.get_session.
    from app.deps import get_session
    from app.main import app

    def _get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    try:
        yield engine
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


@pytest.fixture
def session(test_engine) -> Iterator[Session]:
    """En vanlig SQLModel-session mot test-engine."""
    with Session(test_engine) as s:
        yield s


@pytest.fixture
def client(test_engine):
    """Synchronous TestClient mot appen med test-DB."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def admin_user(session):
    """Skapar en admin-användare i test-DB:n."""
    from app.auth import hash_password
    from app.models import User
    from app.models.user import Role

    user = User(
        username="testadmin",
        password_hash=hash_password("testpass"),
        role=Role.ADMIN,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def logged_in_client(client, admin_user):
    """TestClient med en aktiv session (loggad in som admin)."""
    r = client.get("/login")
    assert r.status_code == 200
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    assert m, "csrf_token saknas i /login"

    r = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": "testpass",
            "csrf_token": m.group(1),
        },
    )
    assert r.status_code in (302, 303), f"login failed: {r.status_code} {r.text[:200]}"
    return client


@pytest.fixture
def kiosk_session_client(client, session, admin_user):
    """Som logged_in_client men aktiverar också en Kiosk i sessionen via
    /kiosk/activate. Adminanvändaren loggas ut automatiskt av aktiveringen."""
    from app.models import Kiosk

    kiosk = Kiosk(name="Testkiosk")
    session.add(kiosk)
    session.commit()
    session.refresh(kiosk)

    # Logga in som admin först så vi har en CSRF-token i sessionen
    r = client.get("/login")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    assert m
    client.post(
        "/login",
        data={"username": "testadmin", "password": "testpass", "csrf_token": m.group(1)},
    )

    # Aktivera kiosken - tar bort user-login
    r = client.get(f"/kiosk/activate?token={kiosk.access_token}")
    assert r.status_code in (302, 303), f"activate misslyckades: {r.status_code} {r.text[:200]}"
    return client


def get_csrf(client, path: str = "/") -> str:
    """Hämta csrf_token från en sida med formulär. Använd /pieces eller /loans/cart."""
    r = client.get(path)
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    assert m, f"csrf_token saknas på {path}"
    return m.group(1)
