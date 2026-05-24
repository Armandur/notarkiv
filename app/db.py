from collections.abc import Iterator

from loguru import logger
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# Importera alla modeller så SQLModel.metadata vet om dem.
from app.models import (  # noqa: F401
    AppSetting,
    InventorySession,
    Piece,
    PieceImage,
    PiecePlacement,
    PieceTag,
    ScanSession,
    ScanSessionImage,
    StorageLocation,
    StorageUnit,
    Tag,
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
    logger.info("Databas initialiserad: {}", settings.database_url)


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
                    title, original_title, composer, arranger, lyricist, notes,
                    content='pieces', content_rowid='id'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS pieces_ai AFTER INSERT ON pieces BEGIN
                  INSERT INTO pieces_fts(rowid, title, original_title, composer, arranger, lyricist, notes)
                  VALUES (new.id, new.title, new.original_title, new.composer, new.arranger, new.lyricist, new.notes);
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS pieces_ad AFTER DELETE ON pieces BEGIN
                  INSERT INTO pieces_fts(pieces_fts, rowid, title, original_title, composer, arranger, lyricist, notes)
                  VALUES('delete', old.id, old.title, old.original_title, old.composer, old.arranger, old.lyricist, old.notes);
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS pieces_au AFTER UPDATE ON pieces BEGIN
                  INSERT INTO pieces_fts(pieces_fts, rowid, title, original_title, composer, arranger, lyricist, notes)
                  VALUES('delete', old.id, old.title, old.original_title, old.composer, old.arranger, old.lyricist, old.notes);
                  INSERT INTO pieces_fts(rowid, title, original_title, composer, arranger, lyricist, notes)
                  VALUES (new.id, new.title, new.original_title, new.composer, new.arranger, new.lyricist, new.notes);
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
