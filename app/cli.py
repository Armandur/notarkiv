"""CLI för databas- och användarhantering. Anropas via `python -m app.cli`."""

import typer
from loguru import logger
from sqlmodel import Session, select

from app.auth import create_user, hash_password
from app.db import engine, init_db, reset_db
from app.logging_setup import setup_logging
from app.models import User
from app.models.user import Role
from app.seed import seed_all

app = typer.Typer(no_args_is_help=True)
db = typer.Typer(no_args_is_help=True, help="Databasoperationer")
users_cli = typer.Typer(no_args_is_help=True, help="Användarhantering")

app.add_typer(db, name="db")
app.add_typer(users_cli, name="users")


@db.command("init")
def db_init() -> None:
    """Skapa tabeller och FTS-objekt om de saknas. Idempotent."""
    setup_logging()
    init_db()


@db.command("reset")
def db_reset(
    seed: bool = typer.Option(False, "--seed", help="Kör seed efter reset"),
) -> None:
    """Radera databasen och skapa ny. Endast OK före prod."""
    setup_logging()
    if not typer.confirm("Detta raderar all data i SQLite-filen. Fortsätta?"):
        raise typer.Abort()
    reset_db()
    if seed:
        seed_all()


@db.command("seed")
def db_seed(
    clear_pieces: bool = typer.Option(False, "--clear-pieces"),
) -> None:
    """Läs in seed_data/*.yaml till databasen."""
    setup_logging()
    init_db()
    seed_all(clear_pieces=clear_pieces)


@users_cli.command("create")
def users_create(
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    role: Role = typer.Option(Role.READER, "--role"),
    email: str | None = typer.Option(None, "--email"),
) -> None:
    """Skapa en användare."""
    setup_logging()
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing:
            logger.error("Användarnamn '{}' finns redan", username)
            raise typer.Exit(code=1)
        user = create_user(
            session,
            username=username,
            password=password,
            role=role,
            email=email,
            must_change_password=True,
        )
        logger.info("Skapade {} med roll {}", user.username, user.role)


@users_cli.command("create-admin")
def users_create_admin(
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Genväg för att skapa en admin-användare."""
    users_create(username=username, password=password, role=Role.ADMIN, email=None)


@users_cli.command("reset-password")
def users_reset_password(
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Sätt ett nytt lösenord och tvinga byte vid nästa login."""
    setup_logging()
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            logger.error("Hittade ingen användare '{}'", username)
            raise typer.Exit(code=1)
        user.password_hash = hash_password(password)
        user.must_change_password = True
        session.add(user)
        session.commit()
        logger.info("Lösenord uppdaterat för {}", username)


if __name__ == "__main__":
    app()
