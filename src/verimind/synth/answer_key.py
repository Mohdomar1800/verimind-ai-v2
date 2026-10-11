"""The answer key: what really happened in a synthetic dataset.

Written next to the logs as `answer_key.json`. The analysis engine never reads it. Only the
evaluation harness does, to score the engine's output against the truth (e.g. "did the true root
cause appear in the top 3 issues?").
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel

from verimind.synth.catalog import Category

ANSWER_KEY_FILE = "answer_key.json"


class BugTruth(BaseModel):
    bug_id: str
    key: str
    title: str
    category: Category
    block: str | None
    signature_hint: str
    introduced_in_run: int  # 1-based run index where the bug first exists
    fixed_in_run: int | None  # first run where it is gone; None if never fixed
    hits_per_run: dict[str, int] = {}


class TestTruth(BaseModel):
    __test__: ClassVar[bool] = False  # stop pytest from treating this as a test class

    test_name: str
    seed: int
    block: str
    scenario: str
    log_file: str  # path relative to the dataset root, with forward slashes
    status: Literal["pass", "fail"]
    root_cause: str | None  # bug_id, or None for passing tests
    first_failure_line: int | None  # 1-based line where the root-cause error starts
    error_lines: list[int]  # every error-level line (root cause + cascade), 1-based
    total_lines: int


class RunTruth(BaseModel):
    run_id: str
    index: int
    simulator: str
    started_at: datetime
    tests: list[TestTruth]

    @property
    def failing(self) -> list[TestTruth]:
        return [t for t in self.tests if t.status == "fail"]


class AnswerKey(BaseModel):
    format_version: int = 1
    dataset: str
    seed: int
    generator_version: str
    spec: dict[str, object]
    bugs: list[BugTruth]
    runs: list[RunTruth]

    def save(self, dataset_dir: Path) -> Path:
        path = dataset_dir / ANSWER_KEY_FILE
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, dataset_dir: Path) -> AnswerKey:
        return cls.model_validate_json((dataset_dir / ANSWER_KEY_FILE).read_text(encoding="utf-8"))
