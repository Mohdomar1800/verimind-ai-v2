from pathlib import Path

from pipeline.ingest_data import classify_artifact, select_repos


def test_select_repos_subset() -> None:
    selected = select_repos(["opentitan", "ibex"])
    assert [repo.name for repo in selected] == ["opentitan", "ibex"]


def test_classify_artifact_coverage() -> None:
    assert classify_artifact(Path("reports/coverage/line_cov.info")) == "coverage"


def test_classify_artifact_sim_log() -> None:
    assert classify_artifact(Path("out/sim_run/test.log")) == "simulation_log"
