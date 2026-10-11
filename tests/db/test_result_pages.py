"""Problem detail, triage, tests list, log viewer and compare pages."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from verimind.db.models import Issue, TestResult, Triage
from verimind.web.app import create_app


@pytest.fixture
def client(analyzed_demo: None) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def top_issue(sessions: sessionmaker[Session], run_id: int) -> Issue:
    with sessions() as s:
        return s.scalars(select(Issue).where(Issue.run_id == run_id, Issue.rank == 1)).one()


def test_issue_page_explains_and_shows_history(client: TestClient, sessions: sessionmaker[Session]) -> None:
    # Find a problem that appears in more than one run, so the page has history to show.
    with sessions() as s:
        sigs = s.scalars(select(Issue.signature_id).where(Issue.run_id == 4)).all()
        shared = next(
            sig for sig in sigs if s.scalar(select(Issue.id).where(Issue.run_id == 1, Issue.signature_id == sig))
        )
        issue = s.scalars(select(Issue).where(Issue.run_id == 4, Issue.signature_id == shared)).one()
    page = client.get(f"/runs/4/issues/{issue.id}")
    assert page.status_code == 200
    assert f"Problem #{issue.rank}" in page.text and "Why it's ranked" in page.text
    assert "Seen in" in page.text and "First seen in" in page.text and "Demo nightly #1" in page.text
    assert page.text.count('href="/runs/4/tests/') == issue.test_count  # every affected test links to its log


def test_triage_saves_with_and_without_htmx(client: TestClient, sessions: sessionmaker[Session]) -> None:
    issue = top_issue(sessions, 1)
    form = {"status": "investigating", "owner": "asha", "ticket": "JIRA-7", "note": "repro on seed 42"}
    partial = client.post(f"/runs/1/issues/{issue.id}/triage", data=form, headers={"HX-Request": "true"})
    assert partial.status_code == 200 and "Saved." in partial.text and "<html" not in partial.text
    with sessions() as s:
        saved = s.get(Triage, issue.signature_id)
        assert saved is not None and (saved.status, saved.owner, saved.ticket) == ("investigating", "asha", "JIRA-7")

    plain = client.post(f"/runs/1/issues/{issue.id}/triage", data={**form, "status": "known"}, follow_redirects=False)
    assert plain.status_code == 303
    assert "Triage: known" in client.get("/runs/1").text  # shown on the run overview too


def test_tests_page_filters_and_pages(client: TestClient) -> None:
    full = client.get("/runs/1/tests")
    assert full.status_code == 200 and "120 tests match." in full.text and "Page 1 of 3" in full.text
    failed = client.get("/runs/1/tests", params={"status": "fail"})
    assert "15 tests match." in failed.text and "Passed</span>" not in failed.text
    partial = client.get("/runs/1/tests", params={"q": "uart"}, headers={"HX-Request": "true"})
    assert "<html" not in partial.text and 'id="tests-table"' in partial.text
    assert "12 tests match." in partial.text
    page3 = client.get("/runs/1/tests", params={"page": 3})
    assert "Page 3 of 3" in page3.text and "Next →" not in page3.text


def test_links_and_forms_with_blank_values_work(client: TestClient) -> None:
    # Every "Next →" link on the tests page, followed as a user would, must open.
    url = "/runs/1/tests"
    for _ in range(3):
        page = client.get(url)
        assert page.status_code == 200
        url = (re.findall(r'href="([^"]+)">Next →', page.text) or [""])[0].replace("&amp;", "&")
        if not url:
            break
    assert "Page 3 of 3" in page.text
    # Forms submit "" for "Any"/"All"; that means "no filter", not an error.
    assert "15 tests match." in client.get("/runs/1/tests?status=fail&module=&issue=&q=").text
    assert client.get("/compare?base=&head=").status_code == 200
    assert client.get("/api/runs/1/tests?issue_id=").status_code == 200


def test_bad_page_input_gets_a_friendly_page(client: TestClient) -> None:
    page = client.get("/runs/1/tests?page=abc")
    assert page.status_code == 400 and "<html" in page.text and "has a value VeriMind" in page.text
    assert client.get("/api/runs/1/tests?limit=abc").status_code == 422  # the API keeps JSON validation errors


def test_log_viewer_centers_on_the_root_cause(client: TestClient, sessions: sessionmaker[Session]) -> None:
    with sessions() as s:
        test = s.scalars(select(TestResult).where(TestResult.run_id == 1, TestResult.follow_on_errors > 2)).first()
        assert test is not None and test.root_line
    page = client.get(f"/runs/1/tests/{test.id}")
    assert page.status_code == 200
    assert re.search(rf'id="L{test.root_line}" class="logline root"', page.text)
    assert "Where it went wrong:" in page.text and f"line {test.root_line}" in page.text
    assert 'class="logline err"' in page.text  # follow-on errors are highlighted too
    first = client.get(f"/runs/1/tests/{test.id}", params={"start": 1})
    assert "Showing lines 1–" in first.text and 'id="L1"' in first.text
    assert client.get(f"/runs/1/tests/{test.id + 100_000}").status_code == 404


def test_compare_page(client: TestClient) -> None:
    default = client.get("/compare")  # picks the two newest analyzed runs
    assert default.status_code == 200 and "new problems" in default.text and "went away" in default.text
    chosen = client.get("/compare", params={"base": 1, "head": 4})
    assert "Still there" in chosen.text and "Demo nightly #4" in chosen.text
    same = client.get("/compare", params={"base": 2, "head": 2})
    assert "Choose two different runs." in same.text


def test_result_pages_wait_for_analysis(sessions: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as c:
        c.post("/demo")  # queued, no worker has run
        resp = c.get("/runs/1/tests", follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/runs/1"
