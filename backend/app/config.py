"""Application settings via pydantic-settings.

Environment variables are documented in ``backend/.env.example`` and mirror
the fields on :class:`Settings`.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend settings loaded from environment / .env file.

    API secrets (``DART_API_KEY`` and ``LLM_API_KEY``) use ``SecretStr`` and
    must never be logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]

    # DART (OpenDART) -- secret; keep as SecretStr, never log its value.
    dart_api_key: SecretStr | None = None
    dart_base_url: str = "https://opendart.fss.or.kr/api"

    # LLM provider (Gemini by default) -- OpenAI-compatible chat completions. The LLM writes only
    # narrative (numbers come from the structured filing API); this is the one
    # provider seam (see app.llm). llm_api_key is a secret -- never log it; it
    # travels only in the Authorization header. base_url is the OpenAI-compatible
    # root (adapter appends /chat/completions); model is configurable -- the exact
    # model name may change independently of this default.
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_model: str = "gemini-3.5-flash-lite"

    # SEC EDGAR -- requires a User-Agent with contact info (name + email).
    sec_base_url: str = "https://data.sec.gov"
    sec_user_agent: str = "filing-digest/0.5.2 your-contact@example.com"

    database_url: str = (
        "postgresql+psycopg://filing_digest:filing_digest_dev@localhost:5433/filing_digest"
    )

    # KURE-v1 (nlpai-lab/KURE-v1): the cross-lingual (KO/EN) 1024-dim model whose
    # vectors backfill filing_chunks.embedding. A HuggingFace model id; overriding
    # it would change the embedding space, so it is pinned here as the one knob.
    embedding_model: str = "nlpai-lab/KURE-v1"

    # When the model is already in the local HF cache, skip HF Hub's network
    # freshness checks at load time (see app.embeddings.kure._configure_offline_mode).
    # Turn off to force a network check even when cached (e.g. to pick up a
    # newly pushed revision).
    embedding_offline_first: bool = True

    # Skip the KURE-v1 warm-up in FastAPI's lifespan (app.main.lifespan) so
    # CI/health-check startups don't pay the multi-second model load. The
    # model then lazy-loads on the first /search request instead.
    embedding_warmup_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (one read per process)."""
    return Settings()
