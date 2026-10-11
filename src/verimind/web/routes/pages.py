"""Server-rendered HTML pages.

Every page works without JavaScript (plain forms and links). HTMX adds live updates: the runs list
and the analysis progress card refresh themselves while work is in progress.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from verimind.db.models import Run, RunStatus
from verimind.services import results
from verimind.services.demo import ALREADY_LOADED, demo_loaded, load_demo
from verimind.services.runs import add_run, default_run_name, latest_job, list_runs, queue_analysis
from verimind.storage import StorageError
from verimind.web.deps import SessionDep, StorageDep, load_run
from verimind.web.templating import WEB_DIR, render_page

router = APIRouter(include_in_schema=False)

NOTICES = {
    "demo": "Demo data loaded. Four nightly regressions are being analyzed. This takes a few seconds.",
    "demo-exists": ALREADY_LOADED,
    "deleted": "The run and its log files were deleted.",
    "reanalyze": "Analysis restarted. Results will refresh when it finishes.",
}
ACTIVE = (RunStatus.QUEUED, RunStatus.PROCESSING)


def _runs_context(session: Session) -> dict[str, Any]:
    runs = list_runs(session, limit=200)
    jobs = {r.id: latest_job(session, r.id) for r in runs if r.status in ACTIVE}
    return {"runs": runs, "jobs": jobs, "any_active": bool(jobs)}


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: SessionDep, notice: str | None = None) -> HTMLResponse:
    return render_page(
        request,
        "runs.html",
        active="runs",
        notice=NOTICES.get(notice or ""),
        demo_loaded=demo_loaded(session),
        **_runs_context(session),
    )


@router.get("/partials/runs", response_class=HTMLResponse)
def runs_table(request: Request, session: SessionDep) -> HTMLResponse:
    return render_page(request, "_runs_table.html", **_runs_context(session))


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request) -> HTMLResponse:
    return render_page(request, "upload.html", active="upload")


@router.post("/upload", response_model=None)
def upload_submit(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    files: list[UploadFile] = File(default=[]),
    name: str = Form(default=""),
) -> Response:
    """Fallback for browsers without JavaScript; the page normally uploads through the API."""
    files = [f for f in files if f.filename]
    if not files:
        return render_page(request, "upload.html", 400, active="upload", error="Please choose at least one log file.")
    try:
        run = add_run(
            session,
            storage,
            name or default_run_name([f.filename or "" for f in files]),
            "upload",
            lambda run_id: storage.save_files(run_id, [(f.filename or "upload.log", f.file) for f in files]),
        )
        session.commit()
    except StorageError as exc:
        session.rollback()
        return render_page(request, "upload.html", 400, active="upload", error=str(exc))
    return RedirectResponse(f"/runs/{run.id}", status_code=303)


@router.post("/demo")
def demo(session: SessionDep, storage: StorageDep) -> RedirectResponse:
    if demo_loaded(session):
        return RedirectResponse("/?notice=demo-exists", status_code=303)
    load_demo(session, storage)
    session.commit()
    return RedirectResponse("/?notice=demo", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: int, session: SessionDep, notice: str | None = None) -> HTMLResponse:
    run = load_run(session, run_id)
    context: dict[str, Any] = {"run": run, "job": latest_job(session, run_id), "notice": NOTICES.get(notice or "")}
    if run.status == RunStatus.DONE:
        issues = results.run_issues(session, run_id)
        context["issues"] = issues
        context["triage"] = results.triage_map(session, [i.signature_id for i in issues])
        context["modules"] = [m for m in results.module_stats(session, run_id) if m.failed][:8]
        context["top_score"] = max((i.score for i in issues), default=100) or 100
    return render_page(request, "run.html", active="runs", **context)


@router.get("/runs/{run_id}/progress-card", response_class=HTMLResponse)
def progress_card(request: Request, run_id: int, session: SessionDep) -> HTMLResponse:
    run = load_run(session, run_id)
    response = render_page(request, "_run_progress.html", run=run, job=latest_job(session, run_id))
    if run.status not in ACTIVE:
        response.headers["HX-Refresh"] = "true"  # analysis finished: reload the page to show results
    return response


@router.post("/runs/{run_id}/reanalyze")
def reanalyze(run_id: int, session: SessionDep) -> RedirectResponse:
    run: Run = load_run(session, run_id)
    queue_analysis(session, run)
    session.commit()
    return RedirectResponse(f"/runs/{run_id}?notice=reanalyze", status_code=303)


@router.post("/runs/{run_id}/delete")
def delete(run_id: int, session: SessionDep, storage: StorageDep) -> RedirectResponse:
    run = load_run(session, run_id)
    session.delete(run)
    session.commit()
    storage.delete_run(run_id)
    return RedirectResponse("/?notice=deleted", status_code=303)


@router.get("/favicon.ico")
def favicon() -> FileResponse:
    """Browsers ask for /favicon.ico even when the page links an icon; serve the SVG instead of a 404."""
    return FileResponse(WEB_DIR / "static" / "favicon.svg", media_type="image/svg+xml")
