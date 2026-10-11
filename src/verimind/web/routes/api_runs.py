"""Runs: upload logs, follow analysis progress, re-analyze, delete, and load demo data."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from verimind.db.models import Run
from verimind.services.demo import ALREADY_LOADED, demo_loaded, load_demo
from verimind.services.runs import add_run, default_run_name, latest_job, list_runs, queue_analysis
from verimind.storage import StorageError
from verimind.web.deps import SessionDep, StorageDep, load_run
from verimind.web.schemas import JobOut, Page, RunCreated, RunOut

router = APIRouter(prefix="/api", tags=["runs"])


def run_out(session: Session, run: Run) -> RunOut:
    job = latest_job(session, run.id)
    return RunOut.model_validate(run).model_copy(update={"job": JobOut.model_validate(job) if job else None})


@router.post("/runs", status_code=202, response_model=RunCreated, summary="Upload logs for a new run")
def upload_run(
    session: SessionDep,
    storage: StorageDep,
    files: list[UploadFile] = File(description="Log files (.log/.out/.txt, optionally .gz) or .zip archives"),
    name: str | None = Form(default=None, description="Display name; defaults to the file name"),
) -> RunCreated:
    """Store the uploaded logs and queue them for analysis. Returns immediately (202 Accepted);
    follow progress with `GET /api/runs/{id}/progress`."""
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    noun = "log file" if run.file_count == 1 else "log files"
    return RunCreated(run=run_out(session, run), message=f"Received {run.file_count:,} {noun}. Analysis has started.")


@router.post("/demo", status_code=202, response_model=list[RunOut], summary="Load demo data")
def demo(session: SessionDep, storage: StorageDep) -> list[RunOut]:
    """Generate four synthetic nightly regressions and queue them for analysis (once)."""
    if demo_loaded(session):
        raise HTTPException(status_code=409, detail=ALREADY_LOADED)
    runs = load_demo(session, storage)
    session.commit()
    return [run_out(session, r) for r in runs]


@router.get("/runs", response_model=Page[RunOut], summary="List runs, newest first")
def get_runs(session: SessionDep, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> Page[RunOut]:
    total = session.scalar(select(func.count()).select_from(Run)) or 0
    runs = list_runs(session, limit=limit, offset=offset)
    return Page(total=total, items=[run_out(session, r) for r in runs])


@router.get("/runs/{run_id}", response_model=RunOut, summary="Run details and summary")
def get_run(run_id: int, session: SessionDep) -> RunOut:
    return run_out(session, load_run(session, run_id))


@router.get("/runs/{run_id}/progress", response_model=JobOut, summary="Analysis progress")
def get_progress(run_id: int, session: SessionDep) -> JobOut:
    load_run(session, run_id)
    job = latest_job(session, run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This run has no analysis job.")
    return JobOut.model_validate(job)


@router.post("/runs/{run_id}/reanalyze", status_code=202, response_model=RunOut, summary="Analyze a run again")
def reanalyze(run_id: int, session: SessionDep) -> RunOut:
    """Useful after adding a custom log format. Previous results are replaced when it finishes."""
    run = load_run(session, run_id)
    queue_analysis(session, run)
    session.commit()
    return run_out(session, run)


@router.delete("/runs/{run_id}", status_code=204, summary="Delete a run and its files")
def delete_run(run_id: int, session: SessionDep, storage: StorageDep) -> Response:
    run = load_run(session, run_id)
    session.delete(run)
    session.commit()
    storage.delete_run(run_id)
    return Response(status_code=204)
