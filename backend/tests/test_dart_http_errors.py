"""DART failure diagnostics must not expose its query-string API key."""

import asyncio
import traceback

import httpx
import pytest

from app.clients.dart import DartClient
from app.config import Settings

_KEY = "SYNTHETIC_DART_HTTP_ERROR_KEY"
_CALLS = [
    ("refresh_corp_codes", {}),
    ("list_filings", {"corp_code": "00126380", "bgn_de": "20230101", "end_de": "20231231"}),
    ("fetch_financials", {"corp_code": "00126380", "bsns_year": "2023", "reprt_code": "11011"}),
    ("fetch_company_eng_name", {"corp_code": "00126380"}),
    ("fetch_document", {"rcept_no": "20240312000736"}),
]


@pytest.mark.parametrize("method,kwargs", _CALLS)
@pytest.mark.parametrize("failure", ["status", httpx.ReadTimeout, httpx.ConnectError])
def test_dart_http_failures_preserve_type_without_key_in_diagnostics(
    method, kwargs, failure, tmp_path, caplog
) -> None:
    def handler(request):
        assert request.url.params["crtfc_key"] == _KEY
        if failure == "status":
            return httpx.Response(403, text=_KEY, headers={"x-error": _KEY})
        raise failure(f"request failed with {_KEY}", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = DartClient(
                Settings(_env_file=None, dart_api_key=_KEY),
                client=transport,
                cache_path=tmp_path / "corpcode.json",
            )
            await getattr(client, method)(**kwargs)

    expected_type = httpx.HTTPStatusError if failure == "status" else failure
    with pytest.raises(expected_type) as caught:
        asyncio.run(run())
    error = caught.value
    assert _KEY not in str(error)
    assert _KEY not in "".join(traceback.format_exception(error))
    assert _KEY not in str(error.request.url)
    assert error.request.url.params["crtfc_key"] == "***"
    assert _KEY not in repr(error.request.headers)
    assert _KEY not in caplog.text
    if failure == "status":
        assert error.response.status_code == 403
        assert error.response.request is error.request
        assert error.response.content == b""
        assert _KEY not in repr(error.response.headers)
        assert error.response.history == []
