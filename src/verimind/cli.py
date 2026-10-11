"""Command-line interface: `verimind <command>`.

Commands are added phase by phase (generate synthetic logs, analyze a folder, ...).
"""

from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

import typer

from verimind import __version__
from verimind.config import get_settings
from verimind.engine import Analyzer, FormatError, FormatRegistry, LogParser, discover_logs
from verimind.synth import PRESETS, generate_dataset
from verimind.synth.answer_key import ANSWER_KEY_FILE
from verimind.synth.generator import SIMULATOR_CHOICES

app = typer.Typer(help="VeriMind: triage RTL verification logs.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """VeriMind command-line tools."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def generate(
    preset: str = typer.Argument("demo", help=f"Dataset size/shape: {', '.join(PRESETS)}."),
    out: Path | None = typer.Option(None, help="Output folder. Default: datasets/<preset>."),
    seed: int | None = typer.Option(None, help="Random seed; the same seed always gives the same logs."),
    runs: int | None = typer.Option(None, help="Number of regression runs."),
    tests: int | None = typer.Option(None, help="Tests per regression run."),
    simulator: str | None = typer.Option(None, help=f"Log format: {', '.join(SIMULATOR_CHOICES)}."),
    force: bool = typer.Option(False, "--force", help="Replace an existing generated dataset in the folder."),
) -> None:
    """Generate synthetic verification logs (with an answer key) to try VeriMind on."""
    if preset not in PRESETS:
        raise typer.BadParameter(f"unknown preset '{preset}'. Choose one of: {', '.join(PRESETS)}")
    base = PRESETS[preset]
    try:
        spec = replace(
            base,
            seed=base.seed if seed is None else seed,
            runs=base.runs if runs is None else runs,
            tests_per_run=base.tests_per_run if tests is None else tests,
            simulator=base.simulator if simulator is None else simulator,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    out_dir = out or Path("datasets") / preset
    if out_dir.exists() and any(out_dir.iterdir()):
        if not (out_dir / ANSWER_KEY_FILE).exists():
            raise typer.BadParameter(f"{out_dir} is not empty and is not a generated dataset; choose another --out")
        if not force:
            raise typer.BadParameter(f"{out_dir} already has a dataset. Add --force to replace it.")
        shutil.rmtree(out_dir)

    typer.echo(f"Generating '{preset}' dataset in {out_dir} ...")
    report = generate_dataset(spec, out_dir)
    key = report.answer_key
    real_bugs = [b for b in key.bugs if b.category != "infrastructure"]
    typer.echo(
        f"Done: {len(key.runs)} regression run(s), {report.total_tests:,} test logs, "
        f"{report.total_lines:,} lines ({report.total_bytes / 1e6:.1f} MB)."
    )
    typer.echo(
        f"  {report.failing_tests:,} failing tests caused by {len(real_bugs)} injected bugs "
        f"plus random infrastructure failures."
    )
    typer.echo(f"  Answer key: {out_dir / ANSWER_KEY_FILE}")


def _registry() -> FormatRegistry:
    formats_dir = get_settings().formats_dir
    try:
        return FormatRegistry.load([formats_dir] if formats_dir else None)
    except FormatError as exc:
        typer.secho(f"Problem with a log format definition: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


@app.command()
def formats() -> None:
    """List the log formats VeriMind can read."""
    registry = _registry()
    for name in registry.names():
        typer.echo(f"  {name:<10} {registry.describe(name)}")


@app.command()
def parse(
    path: Path = typer.Argument(..., exists=True, help="A log file, or a folder of logs (searched recursively)."),
    log_format: str | None = typer.Option(None, "--format", help="Force a format instead of auto-detecting."),
    as_json: bool = typer.Option(False, "--json", help="Print full results as JSON."),
    show: int = typer.Option(20, help="How many failing tests to list."),
) -> None:
    """Read logs and report which tests failed and where (no grouping or ranking yet)."""
    paths = discover_logs(path)
    if not paths:
        raise typer.BadParameter(f"no log files found in {path}")
    parser = LogParser(_registry())
    if log_format and log_format not in parser.registry.names():
        raise typer.BadParameter(f"unknown format '{log_format}'. Known: {', '.join(parser.registry.names())}")

    started = time.perf_counter()
    results = list(parser.parse_many(paths, log_format))
    elapsed = time.perf_counter() - started

    if as_json:
        typer.echo(json.dumps([r.to_dict() for r in results], indent=2))
        return

    statuses = Counter(r.status for r in results)
    formats_seen = ", ".join(f"{name}: {n}" for name, n in Counter(r.format for r in results).most_common())
    lines = sum(r.total_lines for r in results)
    typer.echo(f"Read {len(results):,} log files ({lines:,} lines) in {elapsed:.1f}s. Formats: {formats_seen}")
    typer.echo(f"  Passed: {statuses['pass']:,}   Failed: {statuses['fail']:,}   Unclear: {statuses['unknown']:,}")
    failing = [r for r in results if r.status == "fail"]
    if failing:
        typer.echo("\nFailing tests (first error in each):")
    for r in failing[:show]:
        first = r.first_error
        seed = f" seed {r.seed}" if r.seed is not None else ""
        where = f"line {first.line}: {first.message[:90]}" if first else r.status_reason
        shown_path = r.path.relative_to(path) if path.is_dir() else r.path
        typer.echo(f"  {r.test_name}{seed}\n      {where}\n      {shown_path}")
    if len(failing) > show:
        typer.echo(f"  ... and {len(failing) - show} more (use --show or --json to see all)")


@app.command()
def analyze(
    path: Path = typer.Argument(..., exists=True, help="A folder with one regression run's logs."),
    as_json: bool = typer.Option(False, "--json", help="Print the full report as JSON."),
    markdown: Path | None = typer.Option(None, help="Also save the report as a Markdown file."),
    show: int = typer.Option(10, help="How many issues to list."),
) -> None:
    """Group failures into distinct issues and rank what to debug first."""
    if not discover_logs(path):
        raise typer.BadParameter(f"no log files found in {path}")
    report = Analyzer(LogParser(_registry())).analyze_dir(path)
    if markdown:
        markdown.write_text(report.to_markdown(), encoding="utf-8")
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return

    s = report.summary
    typer.echo(
        f"Analyzed {s.total_tests:,} tests in '{s.name}' ({s.seconds:.1f}s): "
        f"{s.passed:,} passed, {s.failed:,} failed, {s.unknown:,} unclear."
    )
    if not report.issues:
        typer.secho("No failures found.", fg="green")
        return
    typer.secho(
        f"{s.failed:,} failing tests and {s.error_lines:,} error lines come down to {s.issue_count} distinct issues:\n",
        bold=True,
    )
    typer.echo(f"  {'#':>2}  {'Score':>5}  {'Tests':>5}  {'Category':<14}  Issue")
    for issue in report.issues[:show]:
        typer.echo(
            f"  {issue.rank:>2}  {issue.score:>5.0f}  {issue.test_count:>5}  {issue.category:<14}  {issue.title[:100]}"
        )
        first = issue.tests[0]
        typer.echo(f"  {'':>32}e.g. {first.path} line {first.line}")
    if len(report.issues) > show:
        typer.echo(f"  ... and {len(report.issues) - show} more (use --show or --json)")
    if markdown:
        typer.echo(f"\nReport saved to {markdown}")


@app.command()
def evaluate(
    dataset: Path = typer.Argument(..., exists=True, help="A dataset made by `verimind generate`."),
) -> None:
    """Measure analysis accuracy against a generated dataset's answer key."""
    from verimind.evaluation import evaluate_dataset

    if not (dataset / ANSWER_KEY_FILE).exists():
        raise typer.BadParameter(f"{dataset} has no {ANSWER_KEY_FILE}; create one with `verimind generate`")
    result = evaluate_dataset(dataset, Analyzer(LogParser(_registry())))
    scores = result.runs
    header = f"{'run':<15}{'failing':>8}{'bugs':>6}{'issues':>8}{'status':>8}{'root line':>11}{'cluster F1':>12}"
    typer.echo(header + f"{'category':>10}")
    for r in scores:
        typer.echo(
            f"{r.run_id:<15}{r.failing_tests:>8}{r.true_causes:>6}{r.issues_found:>8}{r.status_accuracy:>8.1%}"
            f"{r.root_cause_accuracy:>11.1%}{r.cluster_f1:>12.3f}{r.category_accuracy:>10.1%}"
        )
    failing = sum(r.failing_tests for r in scores)
    lines = sum(r.error_lines for r in scores)
    issues = sum(r.issues_found for r in scores)
    typer.echo(f"\nTotal: {failing} failing tests with {lines:,} error lines reduced to {issues} issues to triage.")
    bugs = len(result.signatures_per_bug)
    typer.echo(f"Signature stability: {result.stable_bugs} of {bugs} bugs keep one id across all runs.")
    if result.risk:
        risk = result.risk
        auc = (
            f"AUC {risk.model_auc:.2f} vs {risk.baseline_auc:.2f}"
            if risk.model_auc and risk.baseline_auc
            else "AUC n/a"
        )
        typer.echo(
            f"Failure prediction ({risk.evaluated_runs} backtests): top-k hit rate {risk.model_precision:.0%} "
            f"vs {risk.last_run_precision:.0%} for 'failed last run' and {risk.failure_rate:.0%} for random; {auc}."
        )


db_app = typer.Typer(help="Database maintenance.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Create or update the database tables (safe to run repeatedly)."""
    from verimind.db.migrate import upgrade

    upgrade(get_settings().database_url)
    typer.echo("Database is up to date.")


@db_app.command("revision")
def db_revision(message: str = typer.Argument(..., help="Short description, e.g. 'add owner index'.")) -> None:
    """For developers: generate a migration from changes to db/models.py (review it before committing)."""
    from verimind.db.migrate import make_revision

    make_revision(message, get_settings().database_url)


def _wait_for(run_ids: list[int]) -> None:
    """Show job progress until the given runs finish (used by `import --wait` and `demo --wait`)."""
    from verimind.db.models import Run, RunStatus
    from verimind.db.session import session_scope
    from verimind.services.runs import latest_job

    pending = set(run_ids)
    last_message: dict[int, str] = {}
    while pending:
        with session_scope() as session:
            for run_id in sorted(pending):
                run = session.get(Run, run_id)
                job = latest_job(session, run_id)
                if run is None:
                    pending.discard(run_id)
                    continue
                message = f"{job.progress:>3}% {job.progress_message}" if job else run.status
                if run.status in (RunStatus.DONE, RunStatus.FAILED):
                    message = (
                        f"done: {run.failed} failing tests, {run.issue_count} issues"
                        if run.status == RunStatus.DONE
                        else f"failed: {run.error}"
                    )
                    pending.discard(run_id)
                if last_message.get(run_id) != message:
                    typer.echo(f"  run {run_id} ({run.name}): {message}")
                    last_message[run_id] = message
        if pending:
            time.sleep(1)


@app.command("import")
def import_run(
    folder: Path = typer.Argument(..., exists=True, help="Folder with one regression run's log files."),
    name: str | None = typer.Option(None, help="Display name. Default: the folder name."),
    wait: bool = typer.Option(False, "--wait", help="Wait and show progress until analysis finishes."),
) -> None:
    """Add a folder of logs as a new run and queue it for analysis by the worker."""
    from verimind.db.session import session_scope
    from verimind.services.runs import add_run
    from verimind.storage import StorageError, default_storage

    storage = default_storage()
    try:
        with session_scope() as session:
            run = add_run(
                session, storage, name or folder.name, "import", lambda rid: storage.import_folder(rid, folder)
            )
            run_id, file_count = run.id, run.file_count
    except StorageError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Run {run_id} created with {file_count:,} log files and queued for analysis.")
    if wait:
        _wait_for([run_id])


@app.command()
def demo(wait: bool = typer.Option(False, "--wait", help="Wait until the demo runs are analyzed.")) -> None:
    """Load demo data: four synthetic nightly regressions, queued for analysis."""
    from verimind.db.session import session_scope
    from verimind.services.demo import load_demo
    from verimind.storage import default_storage

    with session_scope() as session:
        run_ids = [r.id for r in load_demo(session, default_storage())]
    typer.echo(f"Loaded {len(run_ids)} demo runs: {', '.join(map(str, run_ids))}.")
    if wait:
        _wait_for(run_ids)


@app.command()
def runs(limit: int = typer.Option(20, help="How many runs to show.")) -> None:
    """List runs, newest first."""
    from verimind.db.session import session_scope
    from verimind.services.runs import list_runs

    with session_scope() as session:
        rows = list_runs(session, limit)
        if not rows:
            typer.echo("No runs yet. Try `verimind demo` or `verimind import <folder>`.")
        for r in rows:
            typer.echo(
                f"  {r.id:>4}  {r.status:<10}  {r.total_tests:>6} tests  {r.failed:>5} failed  "
                f"{r.issue_count:>3} issues  {r.name}"
            )


@app.command()
def doctor() -> None:
    """Check that everything VeriMind needs is working, and explain how to fix what isn't."""
    from verimind.doctor import run_checks

    symbols = {"ok": ("OK  ", "green"), "warn": ("WARN", "yellow"), "fail": ("FAIL", "red"), "info": ("INFO", None)}
    checks = run_checks()
    for check in checks:
        label, color = symbols[check.level]
        typer.secho(f"  [{label}] {check.name}: ", fg=color, nl=False)
        typer.echo(check.message)
    if any(c.level == "fail" for c in checks):
        raise typer.Exit(1)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    """Run the web server (UI + API)."""
    import uvicorn

    uvicorn.run("verimind.web.app:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def worker() -> None:
    """Run the background job worker."""
    from verimind.worker.main import main as run_worker

    run_worker()


if __name__ == "__main__":
    app()
