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
