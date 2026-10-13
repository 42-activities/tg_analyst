"""Application settings loaded from environment variables and ``.env``."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tg_api_id: int | None = None
    tg_api_hash: SecretStr | None = None
    tg_phone: str | None = None
    tg_session_path: str = "data/tg_analyst"

    database_url: str = "sqlite:///data/tg_analyst.db"

    def require_telegram(self) -> tuple[int, str]:
        """Return (api_id, api_hash) or raise if credentials are missing."""
        if self.tg_api_id is None or self.tg_api_hash is None:
            raise RuntimeError(
                "Telegram credentials missing: set TG_API_ID and TG_API_HASH in .env "
                "(see .env.example)."
            )
        return self.tg_api_id, self.tg_api_hash.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
