"""Solar (Upstage) adapter for the LLM provider seam.

Solar exposes an OpenAI-compatible REST surface, so this adapter is a thin
``httpx.AsyncClient`` POST to ``{solar_base_url}/chat/completions`` -- deliberately
no ``openai`` SDK dependency. It implements :class:`app.llm.base.LLMClient`.

SECURITY:
- ``SOLAR_API_KEY`` lives in ``settings.solar_api_key`` as a SecretStr. It travels
  ONLY in the ``Authorization: Bearer ...`` request header -- never in the URL,
  never in a log line, never in an exception message. Mirrors the DART client's
  secret discipline (app/clients/dart.py).

Scope: this is an isolated transport unit. It builds no citation logic and no
output guards -- ``response_format`` is passed through verbatim when provided.
"""

import logging
from typing import Any

import httpx

from app.config import Settings
from app.llm.base import ChatMessage, LLMResult

logger = logging.getLogger(__name__)

# Chat completions can be slow (long generations); allow a generous read window
# but keep connect tight so a dead endpoint fails fast. Mirrors dart._TIMEOUT.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class SolarClientError(RuntimeError):
    """Raised for client-side misconfiguration (e.g. missing API key)."""


class SolarApiError(RuntimeError):
    """Raised when the Solar API returns a non-2xx response.

    Carries the HTTP status and a (truncated) response body for diagnosis. The
    API key is never referenced -- it lives only in the Authorization header.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Solar API returned HTTP {status_code}: {body}")


# Response bodies can be large (or an HTML error page); cap what we put in an
# exception message so a failure stays readable and never dumps a huge payload.
_MAX_ERROR_BODY = 500


class SolarClient:
    """Adapter calling Solar's OpenAI-compatible ``/chat/completions`` (LLMClient).

    Settings are injected; an ``httpx.AsyncClient`` may be injected for testing
    (e.g. an ``httpx.MockTransport``). The model defaults to ``settings.solar_model``
    but can be overridden per-instance -- the exact Solar model name is configurable.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        model: str | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.solar_base_url.rstrip("/")
        self._model = model or settings.solar_model
        self._client = client
        self._owns_client = client is None

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """POST one chat completion to Solar and normalize it into an LLMResult.

        ``response_format`` (OpenAI-style, e.g. ``{"type": "json_schema", ...}``) is
        included in the body only when provided -- passed through untouched. Raises
        :class:`SolarApiError` on any non-2xx response (key never leaked).
        """
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            # Passthrough only -- this layer never builds/validates the schema.
            body["response_format"] = response_format

        client = self._get_client()
        # Log only the non-secret request shape. The key is in the header, not here.
        logger.info(
            "POST %s/chat/completions (model=%s, messages=%d, response_format=%s)",
            self._base_url,
            self._model,
            len(messages),
            "yes" if response_format is not None else "no",
        )
        resp = await client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key()}"},
            json=body,
        )
        if resp.status_code // 100 != 2:
            # Never surface the key; truncate the body so the message stays sane.
            raise SolarApiError(resp.status_code, resp.text[:_MAX_ERROR_BODY])
        return self._parse_response(resp.json())

    def _parse_response(self, payload: Any) -> LLMResult:
        """Turn a chat-completions response body into a normalized LLMResult.

        Split from network I/O so offline fixtures exercise parsing without a live
        call. Defensive against missing keys: a malformed body yields empty/None
        fields rather than raising, and ``raw`` always keeps the full payload.
        """
        if not isinstance(payload, dict):
            raise SolarApiError(200, "chat/completions: response is not a JSON object")

        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        text = ""
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None

        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")

        model = payload.get("model")
        return LLMResult(
            text=text,
            model=model if isinstance(model, str) else self._model,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            raw=payload,
        )

    def _api_key(self) -> str:
        secret = self._settings.solar_api_key
        value = secret.get_secret_value() if secret is not None else ""
        if not value:
            raise SolarClientError(
                "SOLAR_API_KEY is not configured (set it in the environment/.env)"
            )
        return value

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying httpx client if this instance created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
