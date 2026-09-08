from fastapi.testclient import TestClient

from app.main import app


def test_local_health_rejects_untrusted_hosts():
    client = TestClient(app)
    assert client.get("/health", headers={"host": "127.0.0.1:8001"}).status_code == 200
    assert client.get("/health", headers={"host": "attacker.invalid"}).status_code == 400
