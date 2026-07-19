from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Publikt default-värde (finns i repot/.env.example). Får aldrig användas i
# produktion - då kan vem som helst signera egna sessionscookies.
DEFAULT_SESSION_SECRET = "byt-detta-i-prod-tack"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    session_secret: str = DEFAULT_SESSION_SECRET
    database_path: Path = Path("./data/notarkiv.db")
    images_path: Path = Path("./data/images")

    ocr_provider: Literal["claude_vision", "tesseract", "hybrid"] = "claude_vision"
    anthropic_api_key: str | None = None
    claude_model: str = "claude-haiku-4-5"

    redis_url: str = "redis://localhost:6379/0"

    musicbrainz_user_agent: str = "notarkiv/0.1 (set-via-env@example.tld)"
    musicbrainz_rate_limit_delay: float = 1.0

    log_level: str = "INFO"
    sentry_dsn: str | None = None

    initial_admin_username: str | None = None
    initial_admin_password: str | None = None

    @model_validator(mode="after")
    def _kraev_riktig_secret_i_prod(self) -> "Settings":
        if self.app_env == "production" and self.session_secret == DEFAULT_SESSION_SECRET:
            raise ValueError(
                "SESSION_SECRET måste sättas till ett unikt slumpat värde i "
                "produktion (t.ex. `python -c \"import secrets; "
                "print(secrets.token_hex(32))\"`). Default-värdet är publikt och "
                "gör sessionscookies förfalskningsbara."
            )
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def covers_dir(self) -> Path:
        return self.images_path / "covers"

    @property
    def thumbnails_dir(self) -> Path:
        return self.images_path / "thumbnails"

    def ensure_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
