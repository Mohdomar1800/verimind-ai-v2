"""Result pages: problem detail with triage, the tests list, the log viewer, and run comparison."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from verimind.config import get_settings
from verimind.db.models import AiSummary, Run, RunStatus, TriageStatus
from verimind.intelligence.summary import SummaryError
from verimind.services import results
from verimind.services.ai import explain_issue
from verimind.services.history import problem_library
from verimind.services.risk import risk_report
from verimind.services.runs import list_runs
from verimind.services.trends import run_trend
from verimind.web.deps import AiClientDep, ParserDep, SessionDep, StorageDep, load_run
from verimind.web.templating import render_page

router = APIRouter(include_in_schema=False)

TESTS_PER_PAGE = 50
LOG_WINDOW = 200
TRIAGE_CHOICES = [
    (TriageStatus.NEW, "New: nobody has looked yet"),
    (TriageStatus.INVESTIGATING, "Investigating: someone is on it"),
    (TriageStatus.KNOWN, "Known issue: tracked elsewhere"),
    (TriageStatus.FIXED, "Fixed: should not come back"),
    (TriageStatus.IGNORED, "Ignore: not worth acting on"),
]


def _analyzed_run(session: SessionDep, run_id: int) -> Run | RedirectResponse:
    """Result pages need finished results; otherwise send the user to the progress page."""
    run = load_run(session, run_id)
    if run.status != RunStatus.DONE:
        return RedirectResponse(f"/runs/{run_id}", status_code=303)
    return run


@router.get("/runs/{run_id}/issues/{issue_id}", response_class=HTMLResponse, response_model=None)
def issue_page(request: Request, run_id: int, issue_id: int, session: SessionDep) -> Response:
    run = _analyzed_run(session, run_id)
    if isinstance(run, RedirectResponse):
        return run
    issue = results.get_issue(session, run_id, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Problem {issue_id} does not exist in this run.")
    history = results.signature_history(session, issue.signature_id)
    return render_page(
        request,
        "issue.html",
        active="runs",
        run=run,
        issue=issue,
        tests=sorted(issue.tests, key=lambda t: t.test_name),
        triage=results.triage_map(session, [issue.signature_id]).get(issue.signature_id),
        triage_choices=TRIAGE_CHOICES,
        history=history,
        first_seen=history[-1][0] if history else run,
        ai_enabled=get_settings().ai_summaries_enabled,
        ai_summary=session.get(AiSummary, issue.signature_id),
    )


@router.post("/runs/{run_id}/issues/{issue_id}/triage", response_class=HTMLResponse, response_model=None)
def save_triage(
    request: Request,
    run_id: int,
    issue_id: int,
    session: SessionDep,
    status: TriageStatus = Form(...),
    owner: str = Form(default=""),
    ticket: str = Form(default=""),
    note: str = Form(default=""),
) -> Response:
    issue = results.get_issue(session, run_id, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Problem {issue_id} does not exist in this run.")
    triage = results.save_triage(
        session,
        issue.signature_id,
        status,
        owner.strip()[:100] or None,
        note.strip()[:10_000],
        ticket.strip()[:300] or None,
    )
    session.commit()
    if request.headers.get("HX-Request"):  # HTMX: swap just the card, with a confirmation
        return render_page(
            request, "_triage_card.html", run=load_run(session, run_id), issue=issue, triage=triage,
            triage_choices=TRIAGE_CHOICES, saved=True,
        )  # fmt: skip
    return RedirectResponse(f"/runs/{run_id}/issues/{issue_id}", status_code=303)


@router.get("/runs/{run_id}/tests", response_class=HTMLResponse, response_model=None)
def tests_page(
    request: Request,
    run_id: int,
    session: SessionDep,
    status: Literal["pass", "fail", "unknown", ""] = "",
    module: str = "",
    issue: int | None = None,
    q: str = "",
    page: int = 1,
) -> Response:
    run = _analyzed_run(session, run_id)
    if isinstance(run, RedirectResponse):
        return run
    page = max(page, 1)
    flt = results.TestFilter(status or None, module or None, issue, q.strip() or None)
    total, rows = results.run_tests(session, run_id, flt, TESTS_PER_PAGE, (page - 1) * TESTS_PER_PAGE)
    context: dict[str, Any] = {
        "run": run,
        "tests": rows,
        "total": total,
        "page": page,
        "pages": max(1, -(-total // TESTS_PER_PAGE)),
        "filters": {"status": status, "module": module, "issue": issue, "q": q},
        "modules": [m.module for m in results.module_stats(session, run_id)],
        "issues": results.run_issues(session, run_id),
    }
    if request.headers.get("HX-Request"):
        return render_page(request, "_tests_table.html", **context)
    return render_page(request, "tests.html", active="runs", **context)


@router.get("/runs/{run_id}/tests/{test_id}", response_class=HTMLResponse)
def log_page(
    request: Request,
    run_id: int,
    test_id: int,
    session: SessionDep,
    storage: StorageDep,
    parser: ParserDep,
    around: int | None = None,
    start: int | None = None,
) -> HTMLResponse:
    run = load_run(session, run_id)
    test = results.get_test(session, run_id, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail=f"Test {test_id} does not exist in this run.")
    window = results.read_log_window(storage, parser, test, around, start, LOG_WINDOW)
    errors = window.error_lines
    root = set(range(test.root_line, (test.root_end_line or test.root_line) + 1)) if test.root_line else set()
    return render_page(
        request,
        "log.html",
        active="runs",
        run=run,
        test=test,
        issue=test.issue,
        window=window,
        root_lines=root,
        error_set=set(errors),
        prev_error=max((e for e in errors if e < window.start), default=None),
        next_error=min((e for e in errors if e > window.end), default=None),
        earlier=max(1, window.start - LOG_WINDOW) if window.start > 1 else None,
        later=window.end + 1 if window.end < window.total_lines else None,
    )


@router.get("/compare", response_class=HTMLResponse)
def compare_page(
    request: Request, session: SessionDep, base: int | None = None, head: int | None = None
) -> HTMLResponse:
    done_runs = [r for r in list_runs(session, limit=200) if r.status == RunStatus.DONE]
    default_base, default_head = results.default_comparison(session)
    base_run = load_run(session, base) if base else default_base
    head_run = load_run(session, head) if head else default_head
    context: dict[str, Any] = {"runs": done_runs, "base": base_run, "head": head_run, "compared": None}
    if base_run and head_run and base_run.id != head_run.id:
        compared = results.compare_runs(session, base_run, head_run)
        context["compared"] = compared
        context["triage"] = results.triage_map(session, [c.signature_id for c in compared])
        context["groups"] = {k: [c for c in compared if c.change == k] for k in ("new", "recurring", "resolved")}
    return render_page(request, "compare.html", active="compare", **context)


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request, session: SessionDep, show: Literal["all", "triaged", "untriaged"] = "all"
) -> HTMLResponse:
    entries = problem_library(session)
    triaged = [e for e in entries if e.triage and e.triage.status != TriageStatus.NEW]
    needs_triage = [e for e in entries if not (e.triage and e.triage.status != TriageStatus.NEW) and e.runs_seen > 1]
    shown = {"all": entries, "triaged": triaged, "untriaged": needs_triage}[show]
    return render_page(
        request, "library.html", active="library", entries=shown, show=show,
        counts={"all": len(entries), "triaged": len(triaged), "untriaged": len(needs_triage)},
    )  # fmt: skip


@router.get("/trends", response_class=HTMLResponse)
def trends_page(request: Request, session: SessionDep) -> HTMLResponse:
    return render_page(request, "trends.html", active="trends", trend=run_trend(session), risk=risk_report(session))


@router.get("/runs/{run_id}/coverage", response_class=HTMLResponse, response_model=None)
def coverage_page(request: Request, run_id: int, session: SessionDep) -> Response:
    run = _analyzed_run(session, run_id)
    if isinstance(run, RedirectResponse):
        return run
    return render_page(request, "coverage.html", active="runs", run=run, cov=run.coverage)


@router.post("/runs/{run_id}/issues/{issue_id}/explain", response_class=HTMLResponse, response_model=None)
def explain(
    request: Request, run_id: int, issue_id: int, session: SessionDep, storage: StorageDep, client: AiClientDep
) -> Response:
    """Ask Claude to explain this problem (optional feature; needs ANTHROPIC_API_KEY)."""
    issue = results.get_issue(session, run_id, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Problem {issue_id} does not exist in this run.")
    error, summary = None, session.get(AiSummary, issue.signature_id)
    if client is None:
        error = "AI summaries are turned off. Set ANTHROPIC_API_KEY to enable them."
    else:
        try:
            summary = explain_issue(session, storage, client, issue)
            session.commit()
        except SummaryError as exc:
            session.rollback()
            error = str(exc)
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/runs/{run_id}/issues/{issue_id}", status_code=303)
    return render_page(
        request,
        "_ai_card.html",
        run=load_run(session, run_id),
        issue=issue,
        ai_enabled=client is not None,
        ai_summary=summary,
        ai_error=error,
    )
