"""Offline tests for the Solar (Upstage) LLM adapter.

No network: an ``httpx.MockTransport`` stands in for the Solar endpoint so we can
assert the outgoing request shape and the response->LLMResult parsing without a
live call or a real key. One test proves the API key never reaches a log record
(the key lives only in the Authorization header, which we never log).
"""

import asyncio
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm.base import ChatMessage, LLMResult
from app.llm.solar import SolarApiError, SolarClient, SolarClientError

# A fake key so the offline path builds the Authorization header without a real
# secret. Distinctive so a leak is trivial to assert on.
_FAKE_KEY = "SOLARKEY123"
_FAKE_SETTINGS = Settings(solar_api_key=SecretStr(_FAKE_KEY))

_MESSAGES: list[ChatMessage] = [
    {"role": "system", "content": "You write only prose."},
    {"role": "user", "content": "Summarize the filing."},
]

# A minimal but well-formed chat-completions response body (OpenAI shape).
_OK_RESPONSE = {
    "id": "chatcmpl-xyz",
    "model": "solar-pro3",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "A concise summary."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
}

# A trivial json_schema response_format, exactly as a caller would pass it.
_JSON_SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "trivial",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}


def _run_complete(handler, *, response_format=None, settings=_FAKE_SETTINGS):
    """Drive SolarClient.complete against a MockTransport handler, offline."""

    async def _run() -> LLMResult:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        solar = SolarClient(settings=settings, client=client)
        try:
            return await solar.complete(_MESSAGES, response_format=response_format)
        finally:
            await client.aclose()

    return asyncio.run(_run())


def test_request_shape_has_auth_header_and_json_body_without_response_format() -> None:
    import json

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=_OK_RESPONSE)

    _run_complete(handler)

    assert captured["path"].endswith("/chat/completions")
    # Secret rides ONLY in the Authorization header (never the URL).
    assert captured["auth"] == f"Bearer {_FAKE_KEY}"
    body = captured["json"]
    assert body["model"] == "solar-pro3"
    assert body["messages"] == _MESSAGES
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 1024
    # response_format is OMITTED when not passed.
    assert "response_format" not in body


def test_response_format_included_only_when_passed() -> None:
    import json

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OK_RESPONSE)

    _run_complete(handler, response_format=_JSON_SCHEMA_FORMAT)
    assert seen["body"]["response_format"] == _JSON_SCHEMA_FORMAT


def test_response_parses_into_llm_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_OK_RESPONSE)

    result = _run_complete(handler)
    assert isinstance(result, LLMResult)
    assert result.text == "A concise summary."
    assert result.model == "solar-pro3"
    assert result.finish_reason == "stop"
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.raw == _OK_RESPONSE  # full payload preserved


def test_missing_usage_yields_none_token_counts() -> None:
    body = {
        "model": "solar-pro3",
        "choices": [
            {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    result = _run_complete(handler)
    assert result.text == "hi"
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_non_2xx_raises_solar_api_error_without_key_leak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(SolarApiError) as exc:
        _run_complete(handler)
    assert exc.value.status_code == 401
    msg = str(exc.value)
    assert "401" in msg
    assert _FAKE_KEY not in msg  # the key value is never surfaced


def test_missing_api_key_raises_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # never reached
        return httpx.Response(200, json=_OK_RESPONSE)

    with pytest.raises(SolarClientError):
        _run_complete(handler, settings=Settings(solar_api_key=None))


def test_api_key_never_appears_in_log_records(caplog) -> None:
    """The adapter logs the request shape but not the secret. Capture every record
    emitted during a completion and assert the fake key appears in none of them --
    the key lives only in the Authorization header, which is never logged."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_OK_RESPONSE)

    with caplog.at_level(logging.DEBUG):
        _run_complete(handler)

    assert caplog.records  # sanity: the adapter did emit at least one log line
    for record in caplog.records:
        assert _FAKE_KEY not in record.getMessage()
    assert _FAKE_KEY not in caplog.text
