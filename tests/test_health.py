from fastapi.testclient import TestClient

from annotatiathon import app as app_module


def test_liveness() -> None:
    client = TestClient(app_module.app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_503_when_database_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "database_is_ready", lambda: False)
    client = TestClient(app_module.app)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_returns_200_when_database_available(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "database_is_ready", lambda: True)
    client = TestClient(app_module.app)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
