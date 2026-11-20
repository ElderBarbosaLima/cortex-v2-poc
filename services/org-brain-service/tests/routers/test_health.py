from fastapi.testclient import TestClient


def test_health_reports_ok(client: TestClient) -> None:
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "org-brain-service", "version": "0.1.0"}


def test_health_echoes_request_id(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "req-123"})

    assert resp.headers["X-Request-ID"] == "req-123"
