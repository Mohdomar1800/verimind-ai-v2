"""Milestone 2: ingest real verification artifacts and metadata from OSS RTL repos."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import requests


@dataclass(frozen=True)
class SourceRepo:
    name: str
    full_name: str
    clone_url: str


SOURCE_REPOS: tuple[SourceRepo, ...] = (
    SourceRepo("opentitan", "lowRISC/opentitan", "https://github.com/lowRISC/opentitan.git"),
    SourceRepo("ibex", "lowRISC/ibex", "https://github.com/lowRISC/ibex.git"),
    SourceRepo("veer_eh1", "chipsalliance/Cores-VeeR-EH1", "https://github.com/chipsalliance/Cores-VeeR-EH1.git"),
    SourceRepo("cva6", "openhwgroup/cva6", "https://github.com/openhwgroup/cva6.git"),
)


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest real RTL verification artifacts and metadata")
    parser.add_argument(
        "--mode",
        choices=["metadata", "clone", "all"],
        default="metadata",
        help="metadata: GitHub commits/issues; clone: shallow clone + artifact scan; all: both",
    )
    parser.add_argument("--raw-root", default=os.getenv("VERIMIND_DATA_ROOT", "./data"), help="Data root path")
    parser.add_argument(
        "--db-path",
        default=os.getenv("VERIMIND_DB_PATH", "./data/verimind4.duckdb"),
        help="DuckDB database path",
    )
    parser.add_argument("--max-commits", type=int, default=250, help="Max commits per repo")
    parser.add_argument("--max-issues", type=int, default=250, help="Max issues per repo")
    parser.add_argument(
        "--repos",
        nargs="*",
        default=[repo.name for repo in SOURCE_REPOS],
        help="Subset of repo names to ingest",
    )
    parser.add_argument(
        "--skip-clone-if-exists",
        action="store_true",
        help="Skip clone/fetch if local repo directory already exists",
    )
    return parser.parse_args()


def ensure_paths(raw_root: Path, db_path: Path) -> tuple[Path, Path, Path]:
    raw_root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    github_raw = raw_root / "raw" / "github"
    repos_raw = raw_root / "raw" / "repos"
    github_raw.mkdir(parents=True, exist_ok=True)
    repos_raw.mkdir(parents=True, exist_ok=True)
    return github_raw, repos_raw, db_path


def init_db(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commits (
            repo TEXT,
            sha TEXT,
            author_name TEXT,
            author_email TEXT,
            commit_date TIMESTAMP,
            message TEXT,
            html_url TEXT,
            ingested_at TIMESTAMP,
            PRIMARY KEY (repo, sha)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issues (
            repo TEXT,
            issue_id BIGINT,
            issue_number BIGINT,
            title TEXT,
            state TEXT,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            closed_at TIMESTAMP,
            labels_json TEXT,
            html_url TEXT,
            ingested_at TIMESTAMP,
            PRIMARY KEY (repo, issue_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_files (
            repo TEXT,
            source TEXT,
            category TEXT,
            relative_path TEXT,
            size_bytes BIGINT,
            ingested_at TIMESTAMP,
            PRIMARY KEY (repo, source, relative_path)
        )
        """
    )


def select_repos(repo_names: list[str]) -> list[SourceRepo]:
    by_name = {repo.name: repo for repo in SOURCE_REPOS}
    selected: list[SourceRepo] = []
    for name in repo_names:
        if name not in by_name:
            raise ValueError(f"Unknown repo '{name}'. Valid values: {', '.join(sorted(by_name.keys()))}")
        selected.append(by_name[name])
    return selected


def github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "verimind4-ingestor"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, headers=github_headers(), params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def is_rate_limited(exc: requests.HTTPError) -> bool:
    response = exc.response
    if response is None:
        return False
    if response.status_code != 403:
        return False

    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining == "0":
        return True

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = str(payload.get("message", "")).lower()
    return "rate limit" in message


def rate_limit_reset_hint(exc: requests.HTTPError) -> str:
    response = exc.response
    if response is None:
        return "unknown reset time"
    reset_raw = response.headers.get("X-RateLimit-Reset")
    if not reset_raw:
        return "unknown reset time"
    try:
        reset_ts = int(reset_raw)
        reset_dt = dt.datetime.fromtimestamp(reset_ts, tz=dt.timezone.utc)
        return reset_dt.isoformat()
    except ValueError:
        return "unknown reset time"


def fetch_commits(full_name: str, max_items: int) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    page = 1
    per_page = 100

    while len(commits) < max_items:
        try:
            batch = github_get(
                f"https://api.github.com/repos/{full_name}/commits",
                params={"per_page": per_page, "page": page},
            )
        except requests.HTTPError as exc:
            if is_rate_limited(exc):
                print(
                    f"  warning: GitHub rate limit reached while fetching commits for {full_name}; "
                    f"continuing with partial commit data. Reset (UTC): {rate_limit_reset_hint(exc)}"
                )
                break
            raise
        if not batch:
            break
        commits.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return commits[:max_items]


def fetch_issues(full_name: str, max_items: int) -> list[dict[str, Any]]:
    # Use Search API to request issues directly (excluding pull requests),
    # which avoids deep pagination through PR-heavy issue listings.
    issues: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    query = f"repo:{full_name} is:issue"

    while len(issues) < max_items:
        try:
            payload = github_get(
                "https://api.github.com/search/issues",
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if is_rate_limited(exc):
                print(
                    f"  warning: GitHub rate limit reached while fetching issues for {full_name}; "
                    f"continuing with partial issue data. Reset (UTC): {rate_limit_reset_hint(exc)}"
                )
                break
            if status == 422:
                print(
                    f"  warning: GitHub returned 422 for issues page={page} on {full_name}; "
                    "continuing with partial issue data."
                )
                break
            raise

        batch = payload.get("items", [])
        if not batch:
            break
        issues.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return issues[:max_items]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def upsert_commits(conn: duckdb.DuckDBPyConnection, repo: str, commits: list[dict[str, Any]]) -> int:
    if not commits:
        return 0

    ingested_at = now_utc_iso()
    rows: list[tuple[Any, ...]] = []
    for item in commits:
        commit = item.get("commit", {})
        author = commit.get("author") or {}
        rows.append(
            (
                repo,
                item.get("sha"),
                author.get("name"),
                author.get("email"),
                author.get("date"),
                commit.get("message"),
                item.get("html_url"),
                ingested_at,
            )
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO commits
        (repo, sha, author_name, author_email, commit_date, message, html_url, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_issues(conn: duckdb.DuckDBPyConnection, repo: str, issues: list[dict[str, Any]]) -> int:
    if not issues:
        return 0

    ingested_at = now_utc_iso()
    rows: list[tuple[Any, ...]] = []
    for item in issues:
        labels = item.get("labels") or []
        rows.append(
            (
                repo,
                item.get("id"),
                item.get("number"),
                item.get("title"),
                item.get("state"),
                item.get("created_at"),
                item.get("updated_at"),
                item.get("closed_at"),
                json.dumps([lbl.get("name") for lbl in labels if isinstance(lbl, dict)]),
                item.get("html_url"),
                ingested_at,
            )
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO issues
        (repo, issue_id, issue_number, title, state, created_at, updated_at, closed_at, labels_json, html_url, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def classify_artifact(path: Path) -> str:
    lower = str(path).lower()
    name = path.name.lower()

    if any(token in lower for token in ["coverage", "cov", "cov_report", "/cov/", "_cov", "fcov"]):
        return "coverage"
    if any(token in lower for token in ["regress", "regression", "testlist", "results", "junit", "dvsim"]):
        return "regression"
    if any(token in lower for token in ["verif", "dv/", "/dv/", "formal", "strategy"]):
        return "metadata"
    if name.endswith(".log") or "sim" in lower or "trace" in lower:
        return "simulation_log"
    if name.endswith((".xml", ".json", ".hjson", ".yaml", ".yml")):
        return "metadata"
    return "other"


def scan_artifact_files(repo_dir: Path, repo_name: str) -> list[tuple[Any, ...]]:
    patterns = [
        "*.log",
        "*.txt",
        "*.xml",
        "*.json",
        "*.hjson",
        "*.yaml",
        "*.yml",
        "*.ucis",
        "*.info",
        "*.rpt",
        "*.cfg",
        "*.tcl",
        "*.sh",
        "*.py",
    ]
    candidates: dict[str, tuple[Any, ...]] = {}
    ingested_at = now_utc_iso()

    for pattern in patterns:
        for path in repo_dir.rglob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_dir).as_posix()
            if any(skip in rel.lower() for skip in [".git/", "vendor/", "third_party/"]):
                continue
            category = classify_artifact(Path(rel))
            candidates[rel] = (
                repo_name,
                "local_clone",
                category,
                rel,
                path.stat().st_size,
                ingested_at,
            )

    return list(candidates.values())


def upsert_artifact_files(conn: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO artifact_files
        (repo, source, category, relative_path, size_bytes, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def clone_repo(repo: SourceRepo, repos_raw_dir: Path, skip_if_exists: bool) -> Path:
    repo_path = repos_raw_dir / repo.name
    if repo_path.exists() and skip_if_exists:
        return repo_path

    if repo_path.exists() and (repo_path / ".git").exists():
        subprocess.run(["git", "-C", str(repo_path), "fetch", "--depth", "1", "origin"], check=True)
    else:
        subprocess.run(["git", "clone", "--depth", "1", repo.clone_url, str(repo_path)], check=True)

    return repo_path


def ingest_metadata(
    conn: duckdb.DuckDBPyConnection,
    github_raw_dir: Path,
    repo: SourceRepo,
    max_commits: int,
    max_issues: int,
) -> dict[str, int]:
    commits = fetch_commits(repo.full_name, max_commits)
    issues = fetch_issues(repo.full_name, max_issues)

    write_json(github_raw_dir / repo.name / "commits.json", commits)
    write_json(github_raw_dir / repo.name / "issues.json", issues)

    n_commits = upsert_commits(conn, repo.name, commits)
    n_issues = upsert_issues(conn, repo.name, issues)
    return {"commits": n_commits, "issues": n_issues}


def ingest_clone(conn: duckdb.DuckDBPyConnection, repos_raw_dir: Path, repo: SourceRepo, skip_if_exists: bool) -> int:
    local_repo = clone_repo(repo, repos_raw_dir, skip_if_exists)
    artifacts = scan_artifact_files(local_repo, repo.name)
    return upsert_artifact_files(conn, artifacts)


def main() -> None:
    args = parse_args()

    raw_root = Path(args.raw_root)
    db_path = Path(args.db_path)
    github_raw_dir, repos_raw_dir, _ = ensure_paths(raw_root, db_path)
    selected = select_repos(args.repos)

    with duckdb.connect(str(db_path)) as conn:
        init_db(conn)

        print(f"Starting Milestone 2 ingestion in mode={args.mode}")
        for repo in selected:
            print(f"Processing {repo.name} ({repo.full_name})")

            if args.mode in {"metadata", "all"}:
                stats = ingest_metadata(conn, github_raw_dir, repo, args.max_commits, args.max_issues)
                print(f"  metadata: commits={stats['commits']} issues={stats['issues']}")

            if args.mode in {"clone", "all"}:
                count = ingest_clone(conn, repos_raw_dir, repo, args.skip_clone_if_exists)
                print(f"  clone scan: artifact_files={count}")

    print(f"Ingestion complete. DuckDB: {db_path}")


if __name__ == "__main__":
    main()
