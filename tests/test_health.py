from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from verimind.db import session as db_session
from verimind.web.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_liveness(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize(("db_ok", "status_code"), [(True, 200), (False, 503)])
def test_readiness_reflects_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_ok: bool, status_code: int
) -> None:
    monkeypatch.setattr(db_session, "check_database", lambda: db_ok)
    resp = client.get("/api/health/ready")
    assert resp.status_code == status_code
    assert resp.json()["database"] is db_ok


def test_pages_explain_when_the_database_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from verimind.config import get_settings
    from verimind.db.session import reset_caches

    monkeypatch.setenv("VERIMIND_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/nowhere")
    monkeypatch.setenv("VERIMIND_DB_CONNECT_TIMEOUT", "1")
    get_settings.cache_clear()
    reset_caches()
    try:
        client = TestClient(create_app())
        page = client.get("/")
        assert page.status_code == 503 and "reach its database" in page.text
        api = client.get("/api/runs")
        assert api.status_code == 503 and "database" in api.json()["detail"]
    finally:
        get_settings.cache_clear()
        reset_caches()


def test_unknown_page_is_friendly(client: TestClient) -> None:
    resp = client.get("/no/such/page")
    assert resp.status_code == 404 and "There is nothing at this address" in resp.text
    assert client.get("/api/nope").json() == {"detail": "Not Found"}  # the API stays JSON


def test_doctor_fails_clearly_when_the_database_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from verimind.cli import app
    from verimind.config import get_settings
    from verimind.db.session import reset_caches

    monkeypatch.setenv("VERIMIND_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/nowhere")
    monkeypatch.setenv("VERIMIND_DB_CONNECT_TIMEOUT", "1")
    get_settings.cache_clear()
    reset_caches()
    try:
        result = CliRunner().invoke(app, ["doctor"])
        assert result.exit_code == 1 and "docker compose ps" in result.output
    finally:
        get_settings.cache_clear()
        reset_caches()


def test_favicon_is_served(client: TestClient) -> None:
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/svg+xml")
