"""The HTML pages, walked through the way a first-time user would."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from verimind.engine import Analyzer
from verimind.storage import default_storage
from verimind.synth import PRESETS, generate_dataset
from verimind.web.app import create_app
from verimind.worker.main import Worker


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def drain(sessions: sessionmaker[Session]) -> None:
    worker = Worker(sessions, default_storage(), Analyzer, name="pages-test")
    while worker.run_once():
        pass


def test_first_visit_offers_upload_and_demo(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Upload your logs" in page.text and "Try it with demo data" in page.text
    assert 'action="/demo"' in page.text  # a plain form: works without JavaScript


def test_demo_loads_only_once(client: TestClient) -> None:
    client.post("/demo")
    again = client.post("/demo", follow_redirects=False)
    assert again.headers["location"] == "/?notice=demo-exists"
    listing = client.get("/?notice=demo-exists")
    assert "already loaded" in listing.text and listing.text.count("Waiting to start") == 4  # no duplicates
    assert 'action="/demo"' not in listing.text  # the button is gone once the demo is there
    api = client.post("/api/demo")
    assert api.status_code == 409 and "already loaded" in api.json()["detail"]


def test_demo_flow_from_button_to_results(client: TestClient, sessions: sessionmaker[Session]) -> None:
    resp = client.post("/demo", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/?notice=demo"

    listing = client.get("/?notice=demo")
    assert "Demo data loaded" in listing.text
    assert 'hx-trigger="every 2s"' in listing.text  # the table refreshes itself while runs are analyzed
    assert listing.text.count("Waiting to start") == 4

    run_page = client.get("/runs/1")
    assert "Waiting for the analyzer" in run_page.text and 'hx-trigger="every 1s"' in run_page.text
    card = client.get("/runs/1/progress-card")
    assert "HX-Refresh" not in card.headers  # still waiting: keep polling

    drain(sessions)

    assert client.get("/runs/1/progress-card").headers["HX-Refresh"] == "true"  # done: reload to show results
    done = client.get("/runs/1")
    assert "come down to" in done.text and "Problems, most important first" in done.text
    assert "Why is this ranked #1?" in done.text
    assert "/api/runs/1/export?format=csv" in done.text
    assert 'hx-trigger="every 2s"' not in client.get("/").text  # nothing left to poll for


def test_upload_form_without_javascript(client: TestClient, sessions: sessionmaker[Session], tmp_path: Path) -> None:
    generate_dataset(replace(PRESETS["quick"], tests_per_run=20, lines_per_test=30), tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for log in (tmp_path / "runs" / "regression_01").glob("*.log"):
            z.write(log, log.name)

    resp = client.post(
        "/upload",
        files=[("files", ("nightly_oct10.zip", buf.getvalue(), "application/zip"))],
        data={"name": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    run_url = resp.headers["location"]
    drain(sessions)
    page = client.get(run_url)
    assert "nightly_oct10 (" in page.text  # default name comes from the file name
    assert "20 log files" in page.text


def test_upload_errors_are_shown_on_the_form(client: TestClient) -> None:
    empty = client.post("/upload", data={"name": "x"})
    assert empty.status_code == 400 and "Please choose at least one log file." in empty.text
    bad = client.post("/upload", files=[("files", ("virus.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 400 and "not a log file" in bad.text


def test_failed_run_explains_itself_and_offers_retry(client: TestClient, sessions: sessionmaker[Session]) -> None:
    client.post("/demo")
    default_storage().delete_run(1)  # simulate lost files
    drain(sessions)
    page = client.get("/runs/1")
    assert "Analysis failed" in page.text and "log files for this run are missing" in page.text
    assert 'action="/runs/1/reanalyze"' in page.text


def test_delete_and_missing_run(client: TestClient, sessions: sessionmaker[Session]) -> None:
    client.post("/demo")
    drain(sessions)
    resp = client.post("/runs/2/delete", follow_redirects=False)
    assert resp.headers["location"] == "/?notice=deleted"
    missing = client.get("/runs/2")
    assert missing.status_code == 404 and "Run 2 does not exist." in missing.text and "Back to runs" in missing.text
