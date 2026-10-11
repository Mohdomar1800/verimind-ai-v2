"""Security headers, the Content Security Policy, and the cross-site request check."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from verimind.db import session as db_session
from verimind.web.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db_session, "check_database", lambda: True)
    return TestClient(create_app())


def test_pages_get_a_strict_csp_and_security_headers(client: TestClient) -> None:
    resp = client.get("/upload")
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "<script>" not in resp.text  # no inline scripts, so the strict policy doesn't break the page


def test_api_docs_get_their_own_policy(client: TestClient) -> None:
    assert "cdn.jsdelivr.net" in client.get("/api/docs").headers["content-security-policy"]


def test_cross_site_posts_are_blocked(client: TestClient) -> None:
    blocked = client.post("/demo", headers={"Origin": "https://evil.example"})
    assert blocked.status_code == 403 and blocked.json()["detail"] == "Cross-site request blocked."
    blocked_api = client.delete("/api/runs/1", headers={"Origin": "http://attacker.local"})
    assert blocked_api.status_code == 403


def test_same_origin_and_scripted_requests_pass_the_check(client: TestClient) -> None:
    # Reaching the route (any status other than 403) means the check let the request through.
    same = client.post("/upload", headers={"Origin": "http://testserver"}, data={"name": "x"})
    assert same.status_code == 400  # the form's own validation ran
    scripted = client.post("/upload", data={"name": "x"})  # no Origin header, like curl
    assert scripted.status_code == 400
