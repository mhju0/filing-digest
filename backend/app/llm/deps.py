"""FastAPI dependency for the LLM provider seam.

Mirrors :func:`app.db.session.get_db_session`: a thin, request-scoped factory
usable as a ``fastapi.Depends`` argument. Builds a :class:`app.llm.solar.SolarClient`
from cached :class:`~app.config.Settings` and closes the httpx client it owns when
the request ends. Callers depend on the :class:`app.llm.base.LLMClient` Protocol,
not on the concrete provider.

The client is request-scoped. A shared lifespan client would improve connection
reuse, but the current ownership model is explicit and leak-free.
"""

import logging
from collections.abc import AsyncIterator

from app.config import get_settings
from app.llm.base import LLMClient
from app.llm.solar import SolarClient

logger = logging.getLogger(__name__)


async def get_llm_client() -> AsyncIterator[LLMClient]:
    """FastAPI dependency: yield a request-scoped LLMClient, then close it.

        @router.post("/answer")
        async def answer(client: LLMClient = Depends(get_llm_client)): ...

    ``SolarClient`` lazily creates (and thus owns) its ``httpx.AsyncClient``, so it
    is closed here once the request finishes.
    """
    client = SolarClient(get_settings())
    try:
        yield client
    finally:
        await client.aclose()
