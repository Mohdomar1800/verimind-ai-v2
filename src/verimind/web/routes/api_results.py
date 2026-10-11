"""Analysis results: issues, tests, the log viewer, modules, run comparison, triage, and export."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from verimind.db.models import Issue, Run, RunStatus, Triage
from verimind.intelligence.summary import SummaryError
from verimind.services import results
from verimind.services.ai import explain_issue
from verimind.web.deps import AiClientDep, ParserDep, SessionDep, StorageDep, load_run
from verimind.web.routes.api_runs import run_out
from verimind.web.schemas import (
    ComparedIssueOut,
    CompareOut,
    IssueDetailOut,
    IssueOut,
    LogLine,
    LogWindowOut,
    ModuleOut,
    Page,
    TestOut,
    TriageIn,
    TriageOut,
)

router = APIRouter(prefix="/api", tags=["results"])


def _require_analyzed(run: Run) -> None:
    if run.status != RunStatus.DONE:
        message = {
            RunStatus.QUEUED: "Analysis has not started yet.",
            RunStatus.PROCESSING: "Analysis is still running.",
            RunStatus.FAILED: f"Analysis failed: {run.error}",
        }.get(RunStatus(run.status), "Analysis is not finished.")
        raise HTTPException(status_code=409, detail=message)


def _triage_out(triage: Triage | None) -> TriageOut:
    return TriageOut.model_validate(triage, from_attributes=True) if triage else TriageOut()


def _issues_out(session: Session, issues: list[Issue]) -> list[IssueOut]:
    triage = results.triage_map(session, [i.signature_id for i in issues])
    return [
        IssueOut.model_validate(i).model_copy(update={"triage": _triage_out(triage.get(i.signature_id))})
        for i in issues
    ]


@router.get("/runs/{run_id}/issues", response_model=list[IssueOut], summary="Ranked issues of a run")
def get_issues(run_id: int, session: SessionDep) -> list[IssueOut]:
    run = load_run(session, run_id)
    _require_analyzed(run)
    return _issues_out(session, results.run_issues(session, run_id))


@router.get("/runs/{run_id}/issues/{issue_id}", response_model=IssueDetailOut, summary="One issue and its tests")
def get_issue(run_id: int, issue_id: int, session: SessionDep) -> IssueDetailOut:
    issue = results.get_issue(session, run_id, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} does not exist in run {run_id}.")
    (base,) = _issues_out(session, [issue])
    tests = sorted(issue.tests, key=lambda t: t.test_name)
    return IssueDetailOut(**base.model_dump(), tests=[TestOut.model_validate(t) for t in tests])


@router.get("/runs/{run_id}/tests", response_model=Page[TestOut], summary="Tests of a run (failing first)")
def get_tests(
    run_id: int,
    session: SessionDep,
    status: Literal["pass", "fail", "unknown"] | None = None,
    module: str | None = None,
    issue_id: int | None = None,
    q: str | None = Query(None, description="Search in test names"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[TestOut]:
    run = load_run(session, run_id)
    _require_analyzed(run)
    total, rows = results.run_tests(session, run_id, results.TestFilter(status, module, issue_id, q), limit, offset)
    return Page(total=total, items=[TestOut.model_validate(t) for t in rows])


@router.get("/runs/{run_id}/tests/{test_id}/log", response_model=LogWindowOut, summary="View part of a log")
def get_log(
    run_id: int,
    test_id: int,
    session: SessionDep,
    storage: StorageDep,
    parser: ParserDep,
    around: int | None = Query(None, ge=1, description="Center the view on this line (default: the root cause)"),
    start: int | None = Query(None, ge=1, description="First line to return (overrides `around`)"),
    lines: int = Query(200, ge=1, le=results.MAX_LOG_WINDOW),
) -> LogWindowOut:
    test = results.get_test(session, run_id, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail=f"Test {test_id} does not exist in run {run_id}.")
    window = results.read_log_window(storage, parser, test, around, start, lines)
    return LogWindowOut(
        test_id=test.id,
        path=test.path,
        start=window.start,
        end=window.end,
        total_lines=window.total_lines,
        root_line=test.root_line,
        root_end_line=test.root_end_line,
        error_lines=window.error_lines,
        lines=[LogLine(n=n, text=text) for n, text in window.lines],
    )


@router.get("/runs/{run_id}/tests/{test_id}/log/raw", summary="Download the original log file")
def download_log(run_id: int, test_id: int, session: SessionDep, storage: StorageDep) -> FileResponse:
    test = results.get_test(session, run_id, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail=f"Test {test_id} does not exist in run {run_id}.")
    path = storage.log_path(run_id, test.path)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.get("/runs/{run_id}/modules", response_model=list[ModuleOut], summary="Per-module pass/fail (heatmap)")
def get_modules(run_id: int, session: SessionDep) -> list[ModuleOut]:
    run = load_run(session, run_id)
    _require_analyzed(run)
    return [
        ModuleOut(module=m.module, tests=m.tests, failed=m.failed, issues=m.issues, pass_rate=round(m.pass_rate, 4))
        for m in results.module_stats(session, run_id)
    ]


@router.get("/compare", response_model=CompareOut, summary="Compare two runs: new, recurring, resolved issues")
def compare(
    session: SessionDep, base: int = Query(description="Older run id"), head: int = Query(description="Newer run id")
) -> CompareOut:
    base_run, head_run = load_run(session, base), load_run(session, head)
    _require_analyzed(base_run)
    _require_analyzed(head_run)
    compared = results.compare_runs(session, base_run, head_run)
    triage = results.triage_map(session, [c.signature_id for c in compared])
    items = [
        ComparedIssueOut(
            signature_id=c.signature_id,
            title=c.title,
            category=c.category,
            change=c.change,
            base_tests=c.base_tests,
            head_tests=c.head_tests,
            base_issue_id=c.base_issue_id,
            head_issue_id=c.head_issue_id,
            triage=_triage_out(triage.get(c.signature_id)),
        )
        for c in compared
    ]
    count = {k: sum(1 for c in compared if c.change == k) for k in ("new", "recurring", "resolved")}
    return CompareOut(base=run_out(session, base_run), head=run_out(session, head_run), issues=items, **count)


@router.get("/signatures/{signature_id}/triage", response_model=TriageOut, tags=["triage"], summary="Triage notes")
def get_triage(signature_id: str, session: SessionDep) -> TriageOut:
    return _triage_out(session.get(Triage, signature_id))


@router.put("/signatures/{signature_id}/triage", response_model=TriageOut, tags=["triage"], summary="Update triage")
def put_triage(signature_id: str, body: TriageIn, session: SessionDep) -> TriageOut:
    """Notes are stored per signature, so they follow the problem into every future run."""
    if len(signature_id) > 40:
        raise HTTPException(status_code=400, detail="Invalid signature id.")
    triage = results.save_triage(session, signature_id, body.status, body.owner, body.note, body.ticket)
    session.commit()
    return _triage_out(triage)


@router.get("/runs/{run_id}/export", summary="Download results as CSV, JSON, or Markdown")
def export(run_id: int, session: SessionDep, format: Literal["csv", "json", "markdown"] = "markdown") -> Response:
    run = load_run(session, run_id)
    _require_analyzed(run)
    stem = f"verimind-run-{run.id}"
    if format == "csv":
        body, media, ext = results.issues_csv(session, run), "text/csv", "csv"
    elif format == "json":
        body, media, ext = (
            json.dumps(results.report_from_db(session, run).to_dict(), indent=2),
            "application/json",
            "json",
        )
    else:
        body, media, ext = results.report_from_db(session, run).to_markdown(), "text/markdown", "md"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'})


@router.get("/runs/{run_id}/coverage", summary="Coverage holes and test grading (needs an uploaded coverage.json)")
def get_coverage(run_id: int, session: SessionDep) -> dict[str, object]:
    run = load_run(session, run_id)
    _require_analyzed(run)
    if run.coverage is None:
        raise HTTPException(
            status_code=404, detail="This run has no coverage data. Upload a coverage.json with the logs."
        )
    return run.coverage


@router.post(
    "/runs/{run_id}/issues/{issue_id}/explain", tags=["ai"], summary="Explain a problem with Claude (optional)"
)
def explain(
    run_id: int, issue_id: int, session: SessionDep, storage: StorageDep, client: AiClientDep
) -> dict[str, str]:
    """Sends the problem's messages and about 30 log lines per affected test (up to 3) to Anthropic.
    Needs ANTHROPIC_API_KEY; returns 503 when the feature is off."""
    issue = results.get_issue(session, run_id, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} does not exist in run {run_id}.")
    if client is None:
        raise HTTPException(
            status_code=503, detail="AI summaries are turned off. Set ANTHROPIC_API_KEY to enable them."
        )
    try:
        summary = explain_issue(session, storage, client, issue)
        session.commit()
    except SummaryError as exc:
        session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"summary": summary.text, "model": summary.model, "created_at": summary.created_at.isoformat()}
