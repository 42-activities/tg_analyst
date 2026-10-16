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

    database_url: str = "postgresql+psycopg://tg_analyst:tg_analyst@localhost:5432/tg_analyst"

    # "vader" (English, fast, default) or "hf" (multilingual transformer; needs the hf extra).
    sentiment_backend: str = "vader"
    hf_sentiment_model: str = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
    # Insult/obscenity/threat model, run with the "hf" sentiment backend ("" disables).
    hf_toxicity_model: str = "cointegrated/rubert-tiny-toxicity"

    # Context topics (docs/CONTEXT_TOPICS.md): thread embedding model ("" disables), the
    # clustering distance threshold, and Claude summaries of the top topics (off without
    # an API key; the text of three pseudonymized threads per topic goes to Anthropic).
    hf_embedding_model: str = "cointegrated/rubert-tiny2"
    topic_threshold: float = 0.2
    anthropic_api_key: SecretStr | None = None
    topic_summary_model: str = "claude-haiku-5-5"
    topic_summary_top: int = 25

    # Worker schedules (0 disables): incremental back-fill + enrichment of every group, and
    # re-checking the last RECONCILE_DAYS against Telegram for edits and deletions.
    auto_refresh_minutes: int = 60
    auto_reconcile_hours: int = 24
    reconcile_days: int = 30
    # Rebuild context topics of every group (0 = only when queued by hand).
    auto_topics_hours: int = 0

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
