from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.greenhouse_routes import setup_greenhouse_routes


class FakeResponse:
    def __init__(self, status_code=200, content=b'{"ok":true}', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        import json
        return json.loads(self.content)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GREENHOUSE_URL", "http://greenhouse.internal:18000")
    monkeypatch.setenv("GREENHOUSE_TOKEN", "server-secret")
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.current_user = "daniel"
        return await call_next(request)

    app.include_router(setup_greenhouse_routes())
    return TestClient(app)


def test_forwards_allowlisted_path_with_server_token(client, monkeypatch):
    calls = []

    async def fake_request(self, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse(content=b'{"memories":[{"id":"1"}]}')

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client.app.middleware_stack = None
    response = client.get("/api/greenhouse/v1/memories?limit=10")

    assert response.status_code == 200
    assert response.json() == {"memories": [{"id": "1"}]}
    assert calls[0][0] == "GET"
    assert calls[0][1] == "http://greenhouse.internal:18000/v1/memories?limit=10"
    assert calls[0][2]["headers"]["authorization"] == "Bearer server-secret"


@pytest.mark.parametrize("path", [
    "/v1/not-allowed",
    "//evil.example/v1/memories",
    "/v1/memories/%2e%2e/areas",
])
def test_rejects_unsafe_or_non_allowlisted_paths_before_outbound(client, monkeypatch, path):
    calls = []

    async def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("outbound request must not be attempted")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    response = client.get("/api/greenhouse" + path)

    assert response.status_code == 404
    assert calls == []


def test_requires_authentication_before_outbound(monkeypatch):
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(setup_greenhouse_routes())
    calls = []

    async def fake_request(*args, **kwargs):
        calls.append(True)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    response = TestClient(app).get("/api/greenhouse/v1/memories")

    assert response.status_code == 401
    assert calls == []


def test_upstream_status_and_body_are_preserved_without_secrets(client, monkeypatch, caplog):
    async def fake_request(self, method, url, **kwargs):
        return FakeResponse(status_code=403, content=b'{"detail":"denied"}', headers={"x-upstream": "safe"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    response = client.get("/api/greenhouse/v1/memories")
    visible = response.content + b" ".join(f"{k}: {v}".encode() for k, v in response.headers.items())

    assert response.status_code == 403
    assert b"server-secret" not in visible
    assert "server-secret" not in caplog.text
    assert b"greenhouse.internal" not in visible


@pytest.mark.parametrize("exc, status, text", [
    (httpx.TimeoutException("slow"), 504, "Greenhouse request timed out"),
    (httpx.ConnectError("refused"), 502, "Greenhouse is unreachable"),
])
def test_upstream_failures_are_loud_and_distinct(client, monkeypatch, exc, status, text):
    async def fake_request(*args, **kwargs):
        raise exc

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    response = client.get("/api/greenhouse/v1/memories")

    assert response.status_code == status
    assert text in response.json()["message"]


def test_not_configured_is_distinct(client, monkeypatch):
    monkeypatch.delenv("GREENHOUSE_TOKEN")
    response = client.get("/api/greenhouse/v1/memories")
    assert response.status_code == 503
    assert "not configured" in response.json()["message"].lower()
