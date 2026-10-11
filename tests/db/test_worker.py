from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from verimind.cli import app
from verimind.db.migrate import alembic_config
from verimind.db.models import Base, Issue, Job, JobStatus, Run, RunStatus, TestResult
from verimind.engine import Analyzer
from verimind.services.demo import load_demo
from verimind.services.runs import add_run, queue_analysis
from verimind.storage import Storage, StorageError
from verimind.synth import PRESETS, generate_dataset
from verimind.worker.main import Worker


@pytest.fixture
def run_folder(tmp_path: Path) -> Path:
    generate_dataset(replace(PRESETS["quick"], tests_per_run=40, lines_per_test=40), tmp_path / "ds")
    return tmp_path / "ds" / "runs" / "regression_01"


def make_worker(sessions: sessionmaker[Session], storage: Storage) -> Worker:
    return Worker(sessions, storage, Analyzer, poll_interval=0.01, name="test-worker")


def import_run(sessions: sessionmaker[Session], storage: Storage, folder: Path) -> int:
    with sessions() as s:
        run = add_run(s, storage, "nightly", "import", partial(storage.import_folder, folder=folder))
        s.commit()
        return run.id


def test_worker_analyzes_a_run_end_to_end(sessions: sessionmaker[Session], storage: Storage, run_folder: Path) -> None:
    run_id = import_run(sessions, storage, run_folder)
    worker = make_worker(sessions, storage)

    assert worker.run_once() is True
    assert worker.run_once() is False  # queue is empty now

    expected = Analyzer().analyze_dir(run_folder)
    with sessions() as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert (run.status, run.file_count, run.total_tests) == (RunStatus.DONE, 40, 40)
        assert (run.failed, run.issue_count) == (expected.summary.failed, expected.summary.issue_count)
        job = s.scalars(select(Job).where(Job.run_id == run_id)).one()
        assert (job.status, job.progress) == (JobStatus.DONE, 100)

        issues = list(s.scalars(select(Issue).where(Issue.run_id == run_id).order_by(Issue.rank)))
        assert [i.signature_id for i in issues] == [i.issue_id for i in expected.issues]
        failing = list(s.scalars(select(TestResult).where(TestResult.run_id == run_id, TestResult.status == "fail")))
        assert failing and all(t.issue_id is not None and t.root_line for t in failing)
        assert sum(i.test_count for i in issues) == len(failing)


def test_reanalysis_replaces_previous_results(
    sessions: sessionmaker[Session], storage: Storage, run_folder: Path
) -> None:
    run_id = import_run(sessions, storage, run_folder)
    worker = make_worker(sessions, storage)
    worker.run_once()
    with sessions() as s:
        run = s.get(Run, run_id)
        assert run is not None
        queue_analysis(s, run)
        s.commit()
    worker.run_once()
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(TestResult).where(TestResult.run_id == run_id)) == 40


def test_missing_files_fail_the_run_with_a_clear_message(
    sessions: sessionmaker[Session], storage: Storage, run_folder: Path
) -> None:
    run_id = import_run(sessions, storage, run_folder)
    storage.delete_run(run_id)
    make_worker(sessions, storage).run_once()
    with sessions() as s:
        run = s.get(Run, run_id)
        job = s.scalars(select(Job).where(Job.run_id == run_id)).one()
        assert run is not None and run.status == RunStatus.FAILED
        assert run.error == "The uploaded log files for this run are missing."
        assert job.status == JobStatus.FAILED and job.attempts == 1  # user errors are not retried


def test_unexpected_errors_are_retried(sessions: sessionmaker[Session], storage: Storage, run_folder: Path) -> None:
    run_id = import_run(sessions, storage, run_folder)

    def broken() -> Analyzer:
        raise RuntimeError("disk on fire")

    Worker(sessions, storage, broken, name="w").run_once()
    with sessions() as s:
        job = s.scalars(select(Job).where(Job.run_id == run_id)).one()
        run = s.get(Run, run_id)
        assert (job.status, job.attempts) == (JobStatus.QUEUED, 1)
        assert job.error == "Unexpected error: disk on fire"
        assert run is not None and run.status == RunStatus.QUEUED


def test_failed_upload_leaves_nothing_behind(sessions: sessionmaker[Session], tmp_path: Path) -> None:
    folder = tmp_path / "upload"
    folder.mkdir()
    (folder / "a.log").write_text("small")  # saved first...
    (folder / "b.log").write_text("x" * 50)  # ...then this one breaks the size limit
    storage = Storage(tmp_path / "store", max_bytes=20)
    with sessions() as s, pytest.raises(StorageError, match="larger than the limit"):
        add_run(s, storage, "too big", "import", partial(storage.import_folder, folder=folder))
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0
    assert not list(storage.root.rglob("*.log"))  # the already-saved a.log was removed too


def test_deleting_a_run_deletes_its_results(
    sessions: sessionmaker[Session], storage: Storage, run_folder: Path
) -> None:
    run_id = import_run(sessions, storage, run_folder)
    make_worker(sessions, storage).run_once()
    with sessions() as s:
        s.delete(s.get(Run, run_id))
        s.commit()
        for table in (Issue, TestResult, Job):
            assert s.scalar(select(func.count()).select_from(table)) == 0


def test_demo_loader_queues_runs(sessions: sessionmaker[Session], storage: Storage) -> None:
    with sessions() as s:
        runs = load_demo(s, storage, preset="quick")
        s.commit()
        assert len(runs) == 1 and runs[0].source == "demo" and runs[0].file_count == 40
        assert s.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)) == 1


def test_cli_import_and_runs(sessions: sessionmaker[Session], run_folder: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["import", str(run_folder), "--name", "From CLI"])
    assert result.exit_code == 0, result.output
    listed = runner.invoke(app, ["runs"])
    assert "From CLI" in listed.output and "queued" in listed.output


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str, sessions: sessionmaker[Session]) -> None:
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_migrations_match_the_models(database_url: str) -> None:
    # A model changed without a migration (`verimind db revision`) would show up as a difference here.
    engine = create_engine(database_url)
    with engine.connect() as conn:
        differences = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert differences == []


def test_doctor_reports_a_healthy_setup(sessions: sessionmaker[Session]) -> None:
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    for name in ("Database: Connected.", "Database tables: Up to date.", "File storage", "Log formats", "AI summaries"):
        assert name in result.output
