from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.llm.deps import get_llm_client
from app.main import app


@pytest.mark.parametrize("headers", [
    {"origin": "https://attacker.invalid"},
    {"origin": "null"},
    {"origin": "http://testserver:8002"},
    {"origin": "http://testserver:0"},
    {"origin": "https://testserver"},
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
    {"sec-fetch-site": "unexpected"},
    {"origin": "http://testserver", "sec-fetch-site": "cross-site"},
])
@pytest.mark.parametrize("method,path", [
    ("GET", "/companies/00000000-0000-0000-0000-000000000001/digest"),
    ("POST", "/answer"),
])
def test_foreign_browser_request_stops_before_dependencies(headers, method, path):
    db = Mock(side_effect=AssertionError("Database dependency reached"))
    llm = Mock(side_effect=AssertionError("Model dependency reached"))
    app.dependency_overrides[get_db_session] = lambda: db()
    app.dependency_overrides[get_llm_client] = lambda: llm()
    try:
        response = TestClient(app).request(method, path, headers=headers)
        assert response.status_code == 403
        db.assert_not_called()
        llm.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_llm_client, None)


@pytest.mark.parametrize("headers", [
    {},
    {"origin": "http://testserver"},
    {"origin": "http://testserver:80", "sec-fetch-site": "same-origin"},
    {"sec-fetch-site": "none"},
])
def test_native_and_same_origin_requests_remain_available(headers):
    assert TestClient(app).get("/health", headers=headers).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"origin": "http://testserver"}])
def test_allowed_digest_request_reaches_builder(headers, monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import routes
    from app.digests import CompanyNotFoundError

    builder = AsyncMock(side_effect=CompanyNotFoundError("missing"))
    monkeypatch.setattr(routes, "build_company_digest", builder)
    app.dependency_overrides[get_db_session] = lambda: object()
    app.dependency_overrides[get_llm_client] = lambda: object()
    try:
        response = TestClient(app).get(
            "/companies/00000000-0000-0000-0000-000000000001/digest", headers=headers
        )
        assert response.status_code == 404
        builder.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_llm_client, None)
