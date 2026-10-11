"""Run coverage prioritization for a stored run, if it has a coverage.json."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from verimind.engine.report import AnalysisReport
from verimind.intelligence.coverage import CoverageFile, analyze_coverage
from verimind.storage import Storage


def coverage_for_run(storage: Storage, run_id: int, report: AnalysisReport) -> dict[str, Any] | None:
    """Coverage results to store on the run, None if no coverage.json was uploaded, or an error message
    if the file is invalid. A bad coverage file never fails the log analysis."""
    path = storage.coverage_path(run_id)
    if path is None:
        return None
    try:
        data = CoverageFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        first = str(exc).splitlines()[0]
        return {"error": f"coverage.json could not be read: {first}"}
    tests = Counter(t.module for t in report.tests)
    failed = Counter(t.module for t in report.tests if t.status == "fail")
    fail_rate = {module: failed[module] / n for module, n in tests.items() if n}
    return analyze_coverage(data, fail_rate).to_dict()
