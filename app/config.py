from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    session_secret: str = "byt-detta-i-prod-tack"
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
