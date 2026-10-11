"""History-aware ranking (new / recurring / regression / known) and the problem library."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from verimind.db.models import Issue
from verimind.engine import Analyzer
from verimind.services.history import problem_library
from verimind.storage import default_storage
from verimind.web.app import create_app
from verimind.worker.main import Worker


def drain(sessions: sessionmaker[Session]) -> None:
    worker = Worker(sessions, default_storage(), Analyzer, name="history")
    while worker.run_once():
        pass


@pytest.fixture
def client(analyzed_demo: None) -> Iterator[TestClient]:
    # analyzed_demo: the four demo runs, analyzed oldest first, so each one sees the ones before it.
    with TestClient(create_app()) as c:
        yield c


def issues_of(sessions: sessionmaker[Session], run_id: int) -> list[Issue]:
    with sessions() as s:
        return list(s.scalars(select(Issue).where(Issue.run_id == run_id).order_by(Issue.rank)))


def find(issues: list[Issue], msg_id: str) -> Issue:
    return next(i for i in issues if i.msg_id == msg_id)


def test_first_run_is_neutral_and_later_runs_are_labeled(client: TestClient, sessions: sessionmaker[Session]) -> None:
    assert {i.history for i in issues_of(sessions, 1)} == {"first_run"}

    latest = issues_of(sessions, 4)
    novif = find(latest, "NOVIF")  # the demo introduces this bug in its last run
    assert novif.history == "new" and any(r.startswith("New:") for r in novif.reasons)
    assert novif.score_parts["history_factor"] == 1.5
    assert find(latest, "SCBD_MISMATCH").history == "recurring"  # present in every run


def test_triage_changes_the_ranking(client: TestClient, sessions: sessionmaker[Session]) -> None:
    latest = issues_of(sessions, 4)
    scbd, novif = find(latest, "SCBD_MISMATCH"), find(latest, "NOVIF")

    # Marked fixed, but it's still failing: a regression jumps to the top.
    client.put(f"/api/signatures/{scbd.signature_id}/triage", json={"status": "fixed", "ticket": "JIRA-9"})
    # A known issue drops down so new problems surface first.
    client.put(f"/api/signatures/{novif.signature_id}/triage", json={"status": "known"})
    client.post("/api/runs/4/reanalyze")
    drain(sessions)

    reranked = issues_of(sessions, 4)
    top = reranked[0]
    assert top.signature_id == scbd.signature_id and top.history == "regression"
    assert "Regression: this was marked fixed (JIRA-9), but it is back." in top.reasons
    known = find(reranked, "NOVIF")
    assert known.history == "known" and known.rank > novif.rank
    assert top.score == 100 and all(i.score <= 100 for i in reranked)
    page = client.get("/runs/4")
    assert "Regression</span>" in page.text and "Known issue</span>" in page.text


def test_problem_library(client: TestClient, sessions: sessionmaker[Session]) -> None:
    with sessions() as s:
        library = problem_library(s)
    by_title = {e.title.split("]")[0]: e for e in library}
    scbd = by_title["[SCBD_MISMATCH"]
    assert scbd.runs_seen == 4 and scbd.last_run_id == 4

    page = client.get("/library")
    assert page.status_code == 200 and "Problem library" in page.text
    untriaged = client.get("/library", params={"show": "untriaged"})
    assert "SCBD_MISMATCH" in untriaged.text  # recurring and nobody has triaged it

    client.put(f"/api/signatures/{scbd.signature_id}/triage", json={"status": "investigating", "owner": "ravi"})
    triaged = client.get("/library", params={"show": "triaged"})
    assert "SCBD_MISMATCH" in triaged.text and "ravi" in triaged.text
    assert "SCBD_MISMATCH" not in client.get("/library", params={"show": "untriaged"}).text


def test_trends_page(client: TestClient) -> None:
    page = client.get("/trends")
    assert page.status_code == 200
    assert page.text.count('class="bar-col"') == 4  # one bar per analyzed run
    assert 'class="table heatmap"' in page.text and "axi_xbar" in page.text
    assert 'class="new-dot"' in page.text  # the last demo run introduces new problems


def test_trends_page_shows_risk_predictions(client: TestClient) -> None:
    page = client.get("/trends")
    assert "Tests most likely to fail next run" in page.text
    assert "How reliable is this?" in page.text and 'class="riskbar"' in page.text


def test_coverage_from_demo_data(client: TestClient, sessions: sessionmaker[Session]) -> None:
    run_page = client.get("/runs/1")
    assert "See coverage holes and the fastest test set" in run_page.text
    page = client.get("/runs/1/coverage")
    assert page.status_code == 200 and "Coverage holes, most important first" in page.text
    assert "The fastest test set" in page.text
    api = client.get("/api/runs/1/coverage").json()
    assert 0 < api["percent"] < 100 and api["holes"] and api["tests_for_target"] < api["tests_total"]


def test_ai_explanation_flow(client: TestClient, sessions: sessionmaker[Session], fake_claude: Any) -> None:
    from verimind.web.deps import get_ai_client

    issue = issues_of(sessions, 1)[0]
    url = f"/runs/1/issues/{issue.id}"
    assert "Turned off." in client.get(url).text  # no API key configured in tests

    fake = fake_claude("## What is happening\nScoreboard sees corrupted data.\n## What to check next\n- dump the bus")
    client.app.dependency_overrides[get_ai_client] = lambda: fake  # type: ignore[attr-defined]
    try:
        card = client.post(f"{url}/explain", headers={"HX-Request": "true"})
        assert card.status_code == 200 and "<h4>What is happening</h4>" in card.text and "<html" not in card.text
        prompt = fake.calls[0]["messages"][0]["content"]
        assert issue.title in prompt and ">" in prompt  # real log excerpt with the root line marked
        api = client.post(f"/api/runs/1/issues/{issue.id}/explain").json()
        assert api["model"] == "claude-opus-5-5" and "Scoreboard" in api["summary"]
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    # The stored summary is shown on the page later, and follows the signature to other runs.
    assert "Scoreboard sees corrupted data." in client.get(url).text
    assert client.post(f"/api/runs/1/issues/{issue.id}/explain").status_code == 503  # feature off again
