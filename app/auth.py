import bcrypt
from sqlmodel import Session, select

from app.models import User
from app.models.user import Role


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def hash_pin(pin: str) -> str:
    """PIN hashas med bcrypt på samma sätt som lösenord. Validering att
    värdet är 4-8 siffror görs i route-lagret innan denna kallas."""
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, pin_hash: str) -> bool:
    return verify_password(pin, pin_hash)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.exec(select(User).where(User.username == username)).first()


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    role: Role = Role.READER,
    email: str | None = None,
    must_change_password: bool = False,
) -> User:
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=role,
        must_change_password=must_change_password,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
