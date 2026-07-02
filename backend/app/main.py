"""FastAPI application entry point.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import logging

from fastapi import FastAPI

from app.api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="filing-digest backend",
    version="0.1.0",
    description=(
        "DART/SEC filing digest API (v0.1). Numbers come only from "
        "structured DART/SEC data; the LLM narrates only; every claim "
        "carries a citation."
    ),
)

app.include_router(router)

logger.info("filing-digest backend app initialized (version 0.1.0)")
