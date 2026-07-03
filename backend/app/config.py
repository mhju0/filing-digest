"""Application settings via pydantic-settings.

Environment variables (see backend/.env.example):
    DART_API_KEY, DART_BASE_URL, SEC_BASE_URL, SEC_USER_AGENT,
    DATABASE_URL, EMBEDDING_DIM
"""

import logging
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Backend settings loaded from environment / .env file.

    Secrets (DART_API_KEY) use SecretStr and must never be logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DART (OpenDART) -- secret; keep as SecretStr, never log its value.
    dart_api_key: SecretStr | None = None
    dart_base_url: str = "https://opendart.fss.or.kr/api"

    # SEC EDGAR -- requires a User-Agent with contact info (name + email).
    sec_base_url: str = "https://data.sec.gov"
    sec_user_agent: str = "filing-digest/0.1.0 your-contact@example.com"

    database_url: str = (
        "postgresql+psycopg://filing_digest:filing_digest_dev@localhost:5432/filing_digest"
    )

    # [Verified] 1024 dims: KURE-v1 (nlpai-lab/KURE-v1) dense dimension.
    # Reserved, not consumed yet: the actual schema dimension is fixed by
    # vector(1024) in backend/db/init.sql (single source of truth). Wire this
    # up in Phase 2; changing the env var alone has no effect today.
    embedding_dim: int = 1024

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
