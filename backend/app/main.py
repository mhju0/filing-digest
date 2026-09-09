"""FastAPI application entry point.

Run with: uvicorn app.main:app --host 127.0.0.1 --port 8001
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from app import __version__
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

    await asyncio.to_thread(embed_texts, ["warm-up"])
    yield


app = FastAPI(
    title="filing-digest backend",
    version=__version__,
    description=(
        "DART/SEC filing digest API. Numbers come only from "
        "structured DART/SEC data; the LLM narrates only; every claim "
        "carries a citation."
    ),
    lifespan=lifespan,
)

def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


@app.middleware("http")
async def browser_boundary(request: Request, call_next):
    origins = request.headers.getlist("origin")
    sites = request.headers.getlist("sec-fetch-site")
    expected = _origin(f"{request.url.scheme}://{request.headers.get('host', '')}")
    foreign_origin = bool(origins) and (
        len(origins) != 1 or _origin(origins[0]) is None or _origin(origins[0]) != expected
    )
    if foreign_origin or any(site.lower() not in ("same-origin", "none") for site in sites):
        return JSONResponse({"detail": "Cross-site browser request denied"}, status_code=403)
    return await call_next(request)


app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_hosts)
app.include_router(router)

logger.info("filing-digest backend app initialized (version %s)", __version__)
