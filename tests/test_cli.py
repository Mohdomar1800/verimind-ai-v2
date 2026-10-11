from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from verimind.cli import app

runner = CliRunner()


def test_generate_creates_dataset_and_protects_existing_folders(tmp_path: Path) -> None:
    out = tmp_path / "ds"
    args = ["generate", "quick", "--out", str(out), "--tests", "10"]

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert (out / "answer_key.json").exists()

    again = runner.invoke(app, args)
    assert again.exit_code != 0
    assert "--force" in again.output

    forced = runner.invoke(app, [*args, "--force"])
    assert forced.exit_code == 0, forced.output


def test_generate_refuses_unrelated_non_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("keep me")
    result = runner.invoke(app, ["generate", "quick", "--out", str(tmp_path), "--force"])
    assert result.exit_code != 0
    assert (tmp_path / "notes.txt").exists()


def test_generate_rejects_unknown_preset() -> None:
    result = runner.invoke(app, ["generate", "huge"])
    assert result.exit_code != 0


def test_analyze_and_evaluate_commands(tmp_path: Path) -> None:
    assert runner.invoke(app, ["generate", "quick", "--out", str(tmp_path / "ds"), "--tests", "30"]).exit_code == 0
    run_dir = tmp_path / "ds" / "runs" / "regression_01"
    report_md = tmp_path / "report.md"

    analyzed = runner.invoke(app, ["analyze", str(run_dir), "--markdown", str(report_md)])
    assert analyzed.exit_code == 0, analyzed.output
    assert "distinct issues" in analyzed.output or "No failures found" in analyzed.output
    assert report_md.read_text().startswith("# VeriMind report")

    evaluated = runner.invoke(app, ["evaluate", str(tmp_path / "ds")])
    assert evaluated.exit_code == 0, evaluated.output
    assert "regression_01" in evaluated.output


def test_evaluate_requires_answer_key(tmp_path: Path) -> None:
    assert runner.invoke(app, ["evaluate", str(tmp_path)]).exit_code != 0
