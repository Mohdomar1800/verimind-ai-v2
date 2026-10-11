"""One-click demo data: generate synthetic regressions and queue them for analysis.

This is what the "Load demo data" button calls, so a new user sees real results within seconds.
"""

from __future__ import annotations

import tempfile
from functools import partial
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from verimind.db.models import Run
from verimind.services.runs import add_run
from verimind.storage import Storage
from verimind.synth import PRESETS, AnswerKey, generate_dataset

SIMULATOR_NAMES = {
    "vcs": "Synopsys VCS",
    "xcelium": "Cadence Xcelium",
    "questa": "Siemens Questa",
    "inhouse": "In-house",
}


ALREADY_LOADED = "The demo data is already loaded. Delete the demo runs first if you want a fresh copy."


def demo_loaded(session: Session) -> bool:
    """A second copy would duplicate every run in history and trends, so the demo loads only once."""
    return session.scalar(select(Run.id).where(Run.source == "demo").limit(1)) is not None


def load_demo(session: Session, storage: Storage, preset: str = "demo", dataset_dir: Path | None = None) -> list[Run]:
    """Queue the demo runs. `dataset_dir` reuses an already generated dataset (tests use this to
    avoid regenerating identical files); otherwise one is generated in a temporary folder."""
    if dataset_dir is not None:
        return _queue_runs(session, storage, dataset_dir)
    with tempfile.TemporaryDirectory(prefix="verimind-demo-") as tmp:
        generate_dataset(PRESETS[preset], Path(tmp))
        return _queue_runs(session, storage, Path(tmp))


def _queue_runs(session: Session, storage: Storage, dataset_dir: Path) -> list[Run]:
    runs: list[Run] = []
    for run_truth in AnswerKey.load(dataset_dir).runs:
        when = run_truth.started_at.strftime("%b %d")
        simulator = SIMULATOR_NAMES.get(run_truth.simulator, run_truth.simulator)
        folder = dataset_dir / "runs" / run_truth.run_id
        name = f"Demo nightly #{run_truth.index} - {when} ({simulator})"
        runs.append(add_run(session, storage, name, "demo", partial(storage.import_folder, folder=folder)))
    return runs
