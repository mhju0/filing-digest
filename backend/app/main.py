"""FastAPI application entry point.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8001
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.logging_config import configure_logging

# Central logging setup: root handler + a filter that masks the DART API key
# (crtfc_key) out of every log line, including httpx's own request-URL logs.
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Warm up KURE-v1 at startup instead of paying the multi-second cold
    load (torch graph + weights) on whichever request hits /search first.
    Trade-off: adds that same delay to server startup instead -- acceptable
    since startup happens once, off the request path.

    Set EMBEDDING_WARMUP_ENABLED=false to skip this (CI/health-check
    contexts that never call /search); the model then lazy-loads on first use.
    """
    if not get_settings().embedding_warmup_enabled:
        logger.info("embedding warm-up skipped (EMBEDDING_WARMUP_ENABLED=false)")
        yield
        return

    from app.embeddings.kure import embed_texts

    embed_texts(["warm-up"])
    yield


app = FastAPI(
    title="filing-digest backend",
    version="0.1.0",
    description=(
        "DART/SEC filing digest API (v0.1). Numbers come only from "
        "structured DART/SEC data; the LLM narrates only; every claim "
        "carries a citation."
    ),
    lifespan=lifespan,
)

app.include_router(router)

logger.info("filing-digest backend app initialized (version 0.1.0)")
