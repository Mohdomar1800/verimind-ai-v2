"""The JSON API, exercised end to end: upload -> worker -> results, plus error handling."""

from __future__ import annotations

import csv
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


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("api-ds")
    generate_dataset(replace(PRESETS["demo"], tests_per_run=60, lines_per_test=40), out)
    return out


def zip_run(dataset: Path, run: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for log in sorted((dataset / "runs" / run).glob("*.log")):
            z.write(log, f"{run}/{log.name}")
    return buf.getvalue()


def upload_and_analyze(client: TestClient, sessions: sessionmaker[Session], dataset: Path, run: str) -> int:
    resp = client.post("/api/runs", files=[("files", (f"{run}.zip", zip_run(dataset, run), "application/zip"))])
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["run"]["status"] == "queued" and body["run"]["file_count"] == 60
    assert "Analysis has started" in body["message"]
    Worker(sessions, default_storage(), Analyzer, name="api-test").run_once()
    return int(body["run"]["id"])


def test_full_journey(client: TestClient, sessions: sessionmaker[Session], dataset: Path) -> None:
    run_id = upload_and_analyze(client, sessions, dataset, "regression_01")

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "done" and run["total_tests"] == 60 and run["job"]["progress"] == 100
    assert client.get(f"/api/runs/{run_id}/progress").json()["status"] == "done"
    assert client.get("/api/runs").json()["total"] == 1

    issues = client.get(f"/api/runs/{run_id}/issues").json()
    assert issues and [i["rank"] for i in issues] == list(range(1, len(issues) + 1))
    assert issues[0]["score"] == 100 and issues[0]["triage"]["status"] == "new"
    assert sum(i["test_count"] for i in issues) == run["failed"]

    top = client.get(f"/api/runs/{run_id}/issues/{issues[0]['id']}").json()
    assert len(top["tests"]) == top["test_count"] and all(t["root_line"] for t in top["tests"])

    failing = client.get(f"/api/runs/{run_id}/tests", params={"status": "fail"}).json()
    assert failing["total"] == run["failed"]
    page = client.get(f"/api/runs/{run_id}/tests", params={"limit": 7, "offset": 0}).json()
    assert page["total"] == 60 and len(page["items"]) == 7 and page["items"][0]["status"] == "fail"
    test = top["tests"][0]
    assert client.get(f"/api/runs/{run_id}/tests", params={"q": test["test_name"]}).json()["total"] >= 1

    log = client.get(f"/api/runs/{run_id}/tests/{test['id']}/log", params={"lines": 50}).json()
    shown = [line["n"] for line in log["lines"]]
    assert test["root_line"] in shown and test["root_line"] in log["error_lines"]
    assert log["root_line"] == test["root_line"]
    raw = client.get(f"/api/runs/{run_id}/tests/{test['id']}/log/raw")
    assert raw.status_code == 200 and raw.content.count(b"\n") == test["total_lines"]

    modules = client.get(f"/api/runs/{run_id}/modules").json()
    assert sum(m["tests"] for m in modules) == 60 and modules[0]["failed"] >= modules[-1]["failed"]

    sig = issues[0]["signature_id"]
    put = client.put(
        f"/api/signatures/{sig}/triage", json={"status": "investigating", "owner": "asha", "note": "repro"}
    )
    assert put.status_code == 200 and put.json()["status"] == "investigating"
    assert client.get(f"/api/runs/{run_id}/issues").json()[0]["triage"]["owner"] == "asha"

    md = client.get(f"/api/runs/{run_id}/export", params={"format": "markdown"})
    assert md.text.startswith("# VeriMind report") and "attachment" in md.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/runs/{run_id}/export", params={"format": "csv"}).text)))
    assert len(rows) == len(issues) and rows[0]["triage_status"] == "investigating"
    exported = client.get(f"/api/runs/{run_id}/export", params={"format": "json"}).json()
    assert len(exported["tests"]) == 60

    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert not default_storage().logs_dir(run_id).exists()


def test_compare_runs_and_triage_follow_the_signature(
    client: TestClient, sessions: sessionmaker[Session], dataset: Path
) -> None:
    first = upload_and_analyze(client, sessions, dataset, "regression_01")
    last = upload_and_analyze(client, sessions, dataset, "regression_04")
    base_issues = client.get(f"/api/runs/{first}/issues").json()
    head_issues = client.get(f"/api/runs/{last}/issues").json()

    cmp = client.get("/api/compare", params={"base": first, "head": last}).json()
    assert cmp["new"] + cmp["recurring"] == len(head_issues)
    assert cmp["resolved"] + cmp["recurring"] == len(base_issues)
    assert cmp["new"] >= 1  # the demo always introduces a bug in its last run

    recurring = [c for c in cmp["issues"] if c["change"] == "recurring"]
    if recurring:  # a note made on the old run shows up on the new run's issue
        sig = recurring[0]["signature_id"]
        client.put(f"/api/signatures/{sig}/triage", json={"status": "known", "ticket": "JIRA-42"})
        head = {i["signature_id"]: i for i in client.get(f"/api/runs/{last}/issues").json()}
        assert head[sig]["triage"]["ticket"] == "JIRA-42"


def test_results_before_analysis_and_missing_things(
    client: TestClient, sessions: sessionmaker[Session], dataset: Path
) -> None:
    resp = client.post("/api/runs", files=[("files", ("r.zip", zip_run(dataset, "regression_02"), "application/zip"))])
    run_id = resp.json()["run"]["id"]
    not_ready = client.get(f"/api/runs/{run_id}/issues")
    assert not_ready.status_code == 409 and not_ready.json()["detail"] == "Analysis has not started yet."
    assert client.get("/api/runs/999").status_code == 404
    assert client.get(f"/api/runs/{run_id}/issues/999").status_code == 404
    assert client.get(f"/api/runs/{run_id}/tests/999/log").status_code == 404


def test_bad_uploads_get_clear_errors(client: TestClient) -> None:
    bad = client.post("/api/runs", files=[("files", ("setup.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 400 and "not a log file" in bad.json()["detail"]
    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w") as z:
        z.writestr("README.md", "nothing here")
    resp = client.post("/api/runs", files=[("files", ("x.zip", empty_zip.getvalue(), "application/zip"))])
    assert resp.status_code == 400 and resp.json()["detail"] == "No log files found in the upload."
    assert client.get("/api/runs").json()["total"] == 0  # nothing half-created


def test_oversized_upload_is_rejected_early(sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    from verimind.config import get_settings

    monkeypatch.setenv("VERIMIND_MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        resp = c.post("/api/runs", files=[("files", ("big.log", b"x" * (3 * 1024**2), "text/plain"))])
    assert resp.status_code == 413 and "limit of 1 MB" in resp.json()["detail"]


def test_demo_endpoint_and_reanalyze(client: TestClient, sessions: sessionmaker[Session]) -> None:
    runs = client.post("/api/demo").json()
    assert len(runs) == 4 and all(r["status"] == "queued" and r["source"] == "demo" for r in runs)
    worker = Worker(sessions, default_storage(), Analyzer, name="demo-test")
    while worker.run_once():
        pass
    first = runs[0]["id"]
    again = client.post(f"/api/runs/{first}/reanalyze")
    assert again.status_code == 202 and again.json()["status"] == "queued"


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]
    for expected in ["/api/runs", "/api/runs/{run_id}/issues", "/api/compare", "/api/runs/{run_id}/export"]:
        assert expected in paths
