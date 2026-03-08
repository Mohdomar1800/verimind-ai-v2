"""Milestone 3: parse artifacts into normalized verification datasets."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import duckdb


FAIL_TOKENS = ("fail", "error", "fatal", "timeout", "unsat?", "assert")


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{prefix}_{digest}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse artifacts into normalized VeriMind4 tables")
    parser.add_argument("--db-path", default="data/verimind4.duckdb")
    parser.add_argument("--raw-repos-root", default="data/raw/repos")
    return parser.parse_args()


def init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tests (
            test_id TEXT PRIMARY KEY,
            repo TEXT,
            module_name TEXT,
            test_name TEXT,
            test_type TEXT,
            status TEXT,
            total_events INTEGER,
            failure_events INTEGER,
            source_path TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS modules (
            module_id TEXT PRIMARY KEY,
            repo TEXT,
            module_name TEXT,
            module_path TEXT,
            module_type TEXT,
            source_path TEXT,
            register_count INTEGER,
            signal_count INTEGER,
            metadata_json TEXT,
            created_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failures (
            failure_id TEXT PRIMARY KEY,
            repo TEXT,
            test_id TEXT,
            module_name TEXT,
            severity TEXT,
            failure_type TEXT,
            message TEXT,
            source_path TEXT,
            source_line INTEGER,
            evidence_json TEXT,
            created_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coverage (
            coverage_id TEXT PRIMARY KEY,
            repo TEXT,
            module_name TEXT,
            coverage_type TEXT,
            coverage_value DOUBLE,
            coverage_gap DOUBLE,
            source_path TEXT,
            evidence_json TEXT,
            created_at TIMESTAMP
        )
        """
    )


def clear_previous_outputs(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM tests")
    conn.execute("DELETE FROM modules")
    conn.execute("DELETE FROM failures")
    conn.execute("DELETE FROM coverage")


def infer_module_name(relative_path: str) -> str:
    path = relative_path.replace("\\", "/")
    parts = path.split("/")

    if "hw" in parts and "ip" in parts:
        hw_idx = parts.index("hw")
        if hw_idx + 2 < len(parts) and parts[hw_idx + 1] == "ip":
            return parts[hw_idx + 2]

    if "verif" in parts and "tests" in parts:
        tests_idx = parts.index("tests")
        # Prefer the test bucket after /tests/, skip generic folder names.
        for i in range(tests_idx + 1, len(parts)):
            token = parts[i]
            if token.lower() not in {"custom", "tests", "dv", "verif", "docs"}:
                return token

    if "regress" in parts:
        return "regression"

    if "dv" in parts:
        dv_idx = parts.index("dv")
        if dv_idx + 1 < len(parts):
            token = parts[dv_idx + 1]
            if token.lower() not in {"dv", "env", "tests"}:
                return token

    stem = Path(path).stem
    stem = re.sub(r"\.(logs|rodata)$", "", stem)
    return stem


def parse_kv_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "addr" and current:
            blocks.append(current)
            current = {}
        current[key] = value
    if current:
        blocks.append(current)
    return blocks


def classify_status_from_text(text: str) -> str:
    lower = text.lower()
    return "fail" if any(token in lower for token in FAIL_TOKENS) else "pass"


def parse_simulation_log(repo: str, rel_path: str, full_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = full_path.read_text(encoding="utf-8", errors="ignore")
    created_at = utc_now_iso()
    module_name = infer_module_name(rel_path)
    test_name = Path(rel_path).stem.replace(".logs", "")

    if rel_path.endswith(".logs.txt"):
        blocks = parse_kv_blocks(text)
        failure_blocks = [
            b
            for b in blocks
            if b.get("severity", "").isdigit() and int(b.get("severity", "0")) >= 2
            or "fail" in b.get("format", "").lower()
        ]
        status = "fail" if failure_blocks else "pass"
        test_id = make_id("test", repo, rel_path, test_name)
        tests = [
            {
                "test_id": test_id,
                "repo": repo,
                "module_name": module_name,
                "test_name": test_name,
                "test_type": "simulation_log",
                "status": status,
                "total_events": len(blocks),
                "failure_events": len(failure_blocks),
                "source_path": rel_path,
                "metadata_json": json.dumps({"parser": "kv_blocks"}),
                "created_at": created_at,
            }
        ]
        failures: list[dict[str, Any]] = []
        for idx, block in enumerate(failure_blocks):
            line_no = int(block.get("line", "0")) if block.get("line", "0").isdigit() else None
            msg = block.get("format", "failure")
            failures.append(
                {
                    "failure_id": make_id("failure", repo, rel_path, str(idx), msg),
                    "repo": repo,
                    "test_id": test_id,
                    "module_name": module_name,
                    "severity": block.get("severity", "unknown"),
                    "failure_type": "log_failure",
                    "message": msg[:500],
                    "source_path": rel_path,
                    "source_line": line_no,
                    "evidence_json": json.dumps(block),
                    "created_at": created_at,
                }
            )
        return tests, failures

    # Generic text trace parser with line-level module/test extraction.
    lines = text.splitlines()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    failure_rows: list[dict[str, Any]] = []

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        kv_pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^|\s]+)", line)
        kv = {k.lower(): v for k, v in kv_pairs}
        line_module = kv.get("module") or module_name
        line_test = kv.get("test") or test_name

        key = (line_module, line_test)
        if key not in grouped:
            grouped[key] = {"total_events": 0, "failure_events": 0}
        grouped[key]["total_events"] += 1

        severity_token = ""
        # Expected format: "... | INFO | module=... | ..."
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2:
            sev_candidate = parts[1].strip().upper()
            if sev_candidate.isalpha():
                severity_token = sev_candidate

        is_fail = classify_status_from_text(line) == "fail" or severity_token in {"ERROR", "FATAL"}
        if is_fail:
            grouped[key]["failure_events"] += 1
            failure_rows.append(
                {
                    "failure_id": make_id("failure", repo, rel_path, str(idx), line),
                    "repo": repo,
                    "test_id": make_id("test", repo, rel_path, line_test, line_module),
                    "module_name": line_module,
                    "severity": severity_token.lower() if severity_token else "text_fail",
                    "failure_type": "trace_failure",
                    "message": line[:500],
                    "source_path": rel_path,
                    "source_line": idx,
                    "evidence_json": json.dumps({"line": line, "severity": severity_token, "kv": kv}),
                    "created_at": created_at,
                }
            )

    tests: list[dict[str, Any]] = []
    for (line_module, line_test), stats in grouped.items():
        test_id = make_id("test", repo, rel_path, line_test, line_module)
        status = "fail" if stats["failure_events"] > 0 else "pass"
        tests.append(
            {
                "test_id": test_id,
                "repo": repo,
                "module_name": line_module,
                "test_name": line_test,
                "test_type": "trace_text",
                "status": status,
                "total_events": int(stats["total_events"]),
                "failure_events": int(stats["failure_events"]),
                "source_path": rel_path,
                "metadata_json": json.dumps({"parser": "line_kv_grouped"}),
                "created_at": created_at,
            }
        )

    return tests, failure_rows


def parse_ibex_formal_json(repo: str, rel_path: str, full_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(full_path.read_text(encoding="utf-8", errors="ignore"))
    created_at = utc_now_iso()
    module_name = "ibex_formal"
    tests: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for idx, item in enumerate(payload if isinstance(payload, list) else []):
        if not isinstance(item, list) or len(item) < 4:
            continue
        properties = item[1] if isinstance(item[1], list) else []
        result_pack = item[3] if isinstance(item[3], list) and len(item[3]) >= 5 else []

        status_hint = str(result_pack[3]) if len(result_pack) > 3 else "UNKNOWN"
        result_line = str(result_pack[4]) if len(result_pack) > 4 else ""
        passed = "UNSAT" in result_line and status_hint.upper() == "OK"
        failed = ("SAT" in result_line and "UNSAT" not in result_line) or status_hint.upper() not in {"OK", "PASS"}
        status = "pass" if passed else "fail" if failed else "unknown"

        test_name = f"{Path(rel_path).stem}_case_{idx:03d}"
        test_id = make_id("test", repo, rel_path, test_name)
        tests.append(
            {
                "test_id": test_id,
                "repo": repo,
                "module_name": module_name,
                "test_name": test_name,
                "test_type": "formal_strategy",
                "status": status,
                "total_events": len(properties),
                "failure_events": 1 if status == "fail" else 0,
                "source_path": rel_path,
                "metadata_json": json.dumps({"tool": item[2] if len(item) > 2 else None}),
                "created_at": created_at,
            }
        )

        if status == "fail":
            failures.append(
                {
                    "failure_id": make_id("failure", repo, rel_path, str(idx), result_line),
                    "repo": repo,
                    "test_id": test_id,
                    "module_name": module_name,
                    "severity": "formal_fail",
                    "failure_type": "property_violation",
                    "message": result_line[:500],
                    "source_path": rel_path,
                    "source_line": None,
                    "evidence_json": json.dumps({"status": status_hint, "result": result_line}),
                    "created_at": created_at,
                }
            )

    return tests, failures


def parse_ipxact_xml(repo: str, rel_path: str, full_path: Path) -> list[dict[str, Any]]:
    created_at = utc_now_iso()
    root = ET.fromstring(full_path.read_text(encoding="utf-8", errors="ignore"))

    def find_text(tag_suffix: str) -> str | None:
        for node in root.iter():
            if node.tag.endswith(tag_suffix):
                return (node.text or "").strip() or None
        return None

    module_name = find_text("name") or infer_module_name(rel_path)
    vendor = find_text("vendor")
    version = find_text("version")

    register_count = sum(1 for node in root.iter() if node.tag.endswith("register"))
    signal_count = sum(1 for node in root.iter() if node.tag.endswith("field"))

    module_id = make_id("module", repo, module_name, rel_path)
    return [
        {
            "module_id": module_id,
            "repo": repo,
            "module_name": module_name,
            "module_path": rel_path,
            "module_type": "ipxact",
            "source_path": rel_path,
            "register_count": register_count,
            "signal_count": signal_count,
            "metadata_json": json.dumps({"vendor": vendor, "version": version}),
            "created_at": created_at,
        }
    ]


def parse_regression_descriptor(repo: str, rel_path: str, full_path: Path) -> list[dict[str, Any]]:
    """Create test rows from regression descriptors (testlists, scripts, yaml/hjson/json configs)."""
    created_at = utc_now_iso()
    module_name = infer_module_name(rel_path)
    test_rows: list[dict[str, Any]] = []
    text = full_path.read_text(encoding="utf-8", errors="ignore")
    lower = rel_path.lower()

    # Heuristic extraction from YAML/HJSON/JSON testlist-style files.
    candidate_names: list[str] = []
    if any(x in lower for x in ["testlist", "regress", "regression", "dvsim", "result"]):
        candidate_names.extend(re.findall(r"[-_a-zA-Z0-9./]*test[-_a-zA-Z0-9./]*", text))
        candidate_names.extend(re.findall(r"(?im)^\\s*test\\s*:\\s*['\\\"]?([a-zA-Z0-9_./-]+)", text))

    # Regression shell scripts still provide useful plan-level tests.
    if lower.endswith(".sh") and "regress" in lower:
        script_name = Path(rel_path).stem
        candidate_names.append(script_name)

    # Deduplicate while keeping order and trimming noisy tokens.
    seen: set[str] = set()
    cleaned: list[str] = []
    for name in candidate_names:
        n = name.strip().strip("'\"")
        if not n or len(n) < 3:
            continue
        if any(skip in n.lower() for skip in ["pytest", "unittest", "latest", "testlist"]):
            continue
        if n not in seen:
            seen.add(n)
            cleaned.append(n)

    if not cleaned:
        # Use descriptor file itself as one planning test node.
        cleaned = [Path(rel_path).stem]

    for idx, test_name in enumerate(cleaned[:400]):  # Hard cap per file for speed/control.
        test_id = make_id("test", repo, rel_path, test_name, str(idx))
        test_rows.append(
            {
                "test_id": test_id,
                "repo": repo,
                "module_name": module_name,
                "test_name": test_name,
                "test_type": "regression_descriptor",
                "status": "pass",
                "total_events": 1,
                "failure_events": 0,
                "source_path": rel_path,
                "metadata_json": json.dumps({"parser": "regression_descriptor"}),
                "created_at": created_at,
            }
        )
    return test_rows


def build_module_rows_from_tests(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    created_at = utc_now_iso()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in tests:
        key = (row["repo"], row["module_name"])
        if key not in grouped:
            grouped[key] = {"tests": 0, "fails": 0}
        grouped[key]["tests"] += 1
        grouped[key]["fails"] += 1 if row["status"] == "fail" else 0

    module_rows: list[dict[str, Any]] = []
    for (repo, module_name), stats in grouped.items():
        module_rows.append(
            {
                "module_id": make_id("module", repo, module_name, "derived_from_tests"),
                "repo": repo,
                "module_name": module_name,
                "module_path": "",
                "module_type": "derived",
                "source_path": "",
                "register_count": None,
                "signal_count": None,
                "metadata_json": json.dumps(stats),
                "created_at": created_at,
            }
        )
    return module_rows


def build_coverage_rows(tests: list[dict[str, Any]], artifact_rows: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    created_at = utc_now_iso()
    coverage_rows: list[dict[str, Any]] = []

    by_module: dict[tuple[str, str], dict[str, int]] = {}
    for row in tests:
        key = (row["repo"], row["module_name"])
        if key not in by_module:
            by_module[key] = {"total": 0, "pass": 0}
        by_module[key]["total"] += 1
        if row["status"] == "pass":
            by_module[key]["pass"] += 1

    for (repo, module_name), stats in by_module.items():
        total = stats["total"]
        pass_count = stats["pass"]
        value = (pass_count / total) * 100.0 if total else None
        gap = 100.0 - value if value is not None else None
        coverage_rows.append(
            {
                "coverage_id": make_id("coverage", repo, module_name, "test_pass_rate"),
                "repo": repo,
                "module_name": module_name,
                "coverage_type": "test_pass_rate",
                "coverage_value": value,
                "coverage_gap": gap,
                "source_path": "derived_from_tests",
                "evidence_json": json.dumps(stats),
                "created_at": created_at,
            }
        )

    for repo, rel_path, category in artifact_rows:
        if category != "coverage":
            continue
        full_module_name = infer_module_name(rel_path)
        full_path = Path("data/raw/repos") / repo / rel_path
        exclusion_count = 0
        if full_path.exists():
            text = full_path.read_text(encoding="utf-8", errors="ignore")
            exclusion_count = sum(1 for line in text.splitlines() if line.strip().startswith("instance"))
        coverage_rows.append(
            {
                "coverage_id": make_id("coverage", repo, rel_path, "coverage_exclusions"),
                "repo": repo,
                "module_name": full_module_name,
                "coverage_type": "coverage_exclusions",
                "coverage_value": None,
                "coverage_gap": None,
                "source_path": rel_path,
                "evidence_json": json.dumps({"exclusion_count": exclusion_count}),
                "created_at": created_at,
            }
        )

    return coverage_rows


def insert_rows(conn: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    keys = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(keys))
    columns = ", ".join(keys)
    values = [tuple(row[key] for key in keys) for row in rows]
    conn.executemany(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", values)
    return len(rows)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    repos_root = Path(args.raw_repos_root)

    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB not found at {db_path}. Run ingestion first.")

    with duckdb.connect(str(db_path)) as conn:
        init_tables(conn)
        clear_previous_outputs(conn)

        artifacts = conn.execute(
            """
            SELECT repo, relative_path, category
            FROM artifact_files
            ORDER BY repo, relative_path
            """
        ).fetchall()

        all_tests: list[dict[str, Any]] = []
        all_modules: list[dict[str, Any]] = []
        all_failures: list[dict[str, Any]] = []

        for repo, rel_path, category in artifacts:
            full_path = repos_root / repo / rel_path
            if not full_path.exists() or not full_path.is_file():
                continue

            lower = rel_path.lower()
            if category == "simulation_log":
                tests, failures = parse_simulation_log(repo, rel_path, full_path)
                all_tests.extend(tests)
                all_failures.extend(failures)
            elif repo == "ibex" and "dv/formal/strategies/" in lower and lower.endswith(".json"):
                tests, failures = parse_ibex_formal_json(repo, rel_path, full_path)
                all_tests.extend(tests)
                all_failures.extend(failures)
            elif category == "regression" or any(x in lower for x in ["testlist", "regress", "regression", "dvsim"]):
                all_tests.extend(parse_regression_descriptor(repo, rel_path, full_path))
            elif lower.endswith(".xml"):
                all_modules.extend(parse_ipxact_xml(repo, rel_path, full_path))

        derived_modules = build_module_rows_from_tests(all_tests)
        all_modules.extend(derived_modules)
        coverage_rows = build_coverage_rows(all_tests, artifacts)

        n_tests = insert_rows(conn, "tests", all_tests)
        n_modules = insert_rows(conn, "modules", all_modules)
        n_failures = insert_rows(conn, "failures", all_failures)
        n_coverage = insert_rows(conn, "coverage", coverage_rows)

        print("Milestone 3 parsing complete")
        print(f"  tests: {n_tests}")
        print(f"  modules: {n_modules}")
        print(f"  failures: {n_failures}")
        print(f"  coverage: {n_coverage}")
        print(f"  db: {db_path}")


if __name__ == "__main__":
    main()
