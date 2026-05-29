from collections.abc import Iterator

from loguru import logger
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# Importera alla modeller så SQLModel.metadata vet om dem.
from app.models import (  # noqa: F401
    AppSetting,
    InventoryCheck,
    InventorySession,
    Kiosk,
    Loan,
    LoanBatch,
    Person,
    PersonLink,
    Piece,
    PieceContributor,
    PieceImage,
    PiecePlacement,
    PieceUserNote,
    PieceTag,
    ScanSession,
    ScanSessionImage,
    StorageLocation,
    StorageUnit,
    Tag,
    TagAlias,
    UnitKind,
    User,
)

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)


@event.listens_for(Engine, "connect")
def _enable_sqlite_pragmas(dbapi_connection, _connection_record):
    """Aktivera foreign keys och WAL för bättre concurrency på SQLite."""
    if "sqlite" not in settings.database_url:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def init_db() -> None:
    """Skapar tabeller och FTS-objekt om de saknas. Idempotent."""
    settings.ensure_dirs()
    SQLModel.metadata.create_all(engine)
    _ensure_fts_objects()
    _ensure_column_guards()
    _backfill_public_ids()
    _backfill_kiosk_tokens()
    logger.info("Databas initialiserad: {}", settings.database_url)


def _backfill_public_ids() -> None:
    """Sätt public_id på pieces som saknar det (efter ALTER). Idempotent."""
    if "sqlite" not in settings.database_url:
        return
    import uuid as _uuid

    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id FROM pieces WHERE public_id IS NULL")).fetchall()
        for (pid,) in rows:
            conn.execute(
                text("UPDATE pieces SET public_id = :uid WHERE id = :id"),
                {"uid": _uuid.uuid4().hex, "id": pid},
            )
        if rows:
            logger.info("Backfillade public_id på {} pieces", len(rows))


def _backfill_kiosk_tokens() -> None:
    """Slumpa kiosk_token för users som saknar det. Idempotent."""
    if "sqlite" not in settings.database_url:
        return
    import secrets

    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id FROM users WHERE kiosk_token IS NULL")).fetchall()
        for (uid,) in rows:
            conn.execute(
                text("UPDATE users SET kiosk_token = :tok WHERE id = :id"),
                {"tok": secrets.token_hex(16), "id": uid},
            )
        if rows:
            logger.info("Backfillade kiosk_token på {} users", len(rows))


def _ensure_column_guards() -> None:
    """ALTER TABLE-guards för nya kolumner som tillkommit efter prod-data.
    SQLite saknar IF NOT EXISTS för ADD COLUMN, så vi kollar PRAGMA först."""
    if "sqlite" not in settings.database_url:
        return

    from sqlalchemy import text

    additions = {
        "loans": [
            ("batch_id", "INTEGER REFERENCES loan_batches(id)"),
            ("picked_up_at", "DATETIME"),
        ],
        "psalm_books": [
            ("edition", "VARCHAR"),
        ],
        "psalm_entries": [
            ("edition", "VARCHAR"),
        ],
        "piece_psalm_refs": [
            ("edition", "VARCHAR"),
        ],
        "pieces": [
            ("spotify_url", "VARCHAR"),
            ("public_id", "VARCHAR"),
        ],
        "people": [
            ("wikidata_id", "VARCHAR"),
            ("biography_fetched_at", "DATETIME"),
            ("portrait_fetched_at", "DATETIME"),
        ],
        "tags": [
            ("description", "VARCHAR"),
        ],
        "scan_sessions": [
            ("target_piece_id", "INTEGER REFERENCES pieces(id)"),
        ],
        "tags": [
            ("parent_id", "INTEGER REFERENCES tags(id)"),
        ],
        "users": [
            ("pin_hash", "VARCHAR"),
            ("kiosk_token", "VARCHAR"),
        ],
        "kiosks": [
            ("active_inventory_session_id", "INTEGER REFERENCES inventory_sessions(id)"),
        ],
    }

    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if not existing:
                continue  # tabellen finns inte alls
            for col_name, col_def in columns:
                if col_name in existing:
                    continue
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                logger.info("ALTER TABLE {} ADD COLUMN {}", table, col_name)


def _ensure_fts_objects() -> None:
    """Sätter upp pieces_fts (FTS5) och triggers. SQLite-specifikt."""
    if "sqlite" not in settings.database_url:
        return

    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS pieces_fts USING fts5(
                    title, original_title, contributors_cache, notes,
                    content='pieces', content_rowid='id'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS pieces_ai AFTER INSERT ON pieces BEGIN
                  INSERT INTO pieces_fts(rowid, title, original_title, contributors_cache, notes)
                  VALUES (new.id, new.title, new.original_title, new.contributors_cache, new.notes);
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS pieces_ad AFTER DELETE ON pieces BEGIN
                  INSERT INTO pieces_fts(pieces_fts, rowid, title, original_title, contributors_cache, notes)
                  VALUES('delete', old.id, old.title, old.original_title, old.contributors_cache, old.notes);
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS pieces_au AFTER UPDATE ON pieces BEGIN
                  INSERT INTO pieces_fts(pieces_fts, rowid, title, original_title, contributors_cache, notes)
                  VALUES('delete', old.id, old.title, old.original_title, old.contributors_cache, old.notes);
                  INSERT INTO pieces_fts(rowid, title, original_title, contributors_cache, notes)
                  VALUES (new.id, new.title, new.original_title, new.contributors_cache, new.notes);
                END
                """
            )
        )


def reset_db() -> None:
    """Raderar databasfilen och återskapar från modellerna. Endast SQLite."""
    if "sqlite" not in settings.database_url:
        raise RuntimeError("reset_db stödjer bara SQLite. Använd dumpa+migrera för Postgres.")

    db_path = settings.database_path
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix) if suffix else db_path
        if candidate.exists():
            candidate.unlink()
            logger.info("Raderade {}", candidate)

    init_db()
