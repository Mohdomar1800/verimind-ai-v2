"""End-to-end tests: a real browser clicks through a running VeriMind.

Run against a live instance (e.g. after ./start.sh):

    VERIMIND_E2E_URL=http://localhost:8000 pytest tests/e2e

They use the Microsoft Edge or Chrome already installed on the machine by default (no browser
download). In CI, set E2E_BROWSER_CHANNEL=chromium after `playwright install chromium`.
Every test fails on any browser console error, which also proves the strict Content Security Policy
doesn't block anything the pages need.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from verimind.synth import PRESETS, generate_dataset

BASE_URL = os.environ.get("VERIMIND_E2E_URL", "").rstrip("/")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="set VERIMIND_E2E_URL to run browser tests against a live VeriMind"),
]

if BASE_URL:
    from playwright.sync_api import Browser, Page, expect, sync_playwright


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    channel = os.environ.get("E2E_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "chrome")
    with sync_playwright() as p:
        b = p.chromium.launch(channel=None if channel == "chromium" else channel)
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(base_url=BASE_URL)
    pg = context.new_page()
    pg.set_default_timeout(30_000)
    errors: list[str] = []
    pg.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    yield pg
    context.close()
    assert not errors, f"browser console errors: {errors}"


def newest_run_ready(page: Page) -> None:
    """Wait (the runs table refreshes itself via HTMX) until the newest run is analyzed."""
    first_row = page.locator("#runs-table tbody tr").first
    expect(first_row.locator(".badge")).to_have_text("Ready", timeout=120_000)


def test_demo_to_root_cause(page: Page) -> None:
    page.goto("/")
    demo_button = page.get_by_role("button", name=re.compile("demo data", re.I))
    if demo_button.count():  # the demo loads once; reruns against the same instance reuse it
        demo_button.first.click()
        expect(page.get_by_text("Demo data loaded")).to_be_visible()
    newest_run_ready(page)

    page.locator("#runs-table tbody tr").first.get_by_role("link").click()
    expect(page.get_by_text("come down to")).to_be_visible()

    # Problem #1 -> triage saved in place (HTMX: no page reload).
    page.locator(".issue-title a").first.click()
    expect(page.get_by_role("heading", name="Problem #1")).to_be_visible()
    issue_url = page.url
    page.locator("#triage-card select[name=status]").select_option("investigating")
    page.locator("#triage-card input[name=owner]").fill("e2e-bot")
    page.locator("#triage-card").get_by_role("button", name="Save").click()
    expect(page.locator("#triage-card").get_by_text("Saved.")).to_be_visible()
    assert page.url == issue_url

    # An affected test opens the log viewer on its root cause.
    page.locator("table").last.get_by_role("link").first.click()
    root = page.locator(".logline.root").first
    expect(root).to_be_visible()
    expect(root).to_be_in_viewport()  # app.js scrolled it into view


def test_upload_with_progress(page: Page, tmp_path: Path) -> None:
    generate_dataset(replace(PRESETS["quick"], tests_per_run=12, lines_per_test=30), tmp_path)
    logs = sorted((tmp_path / "runs" / "regression_01").glob("*.log"))

    page.goto("/upload")
    page.set_input_files("#files", [str(p) for p in logs])
    expect(page.locator("#file-count")).to_have_text("12 files selected")
    page.fill("input[name=name]", "E2E upload")
    page.get_by_role("button", name="Upload and analyze").click()

    page.wait_for_url(re.compile(r"/runs/\d+$"))
    expect(page.get_by_role("heading", name="E2E upload")).to_be_visible()
    # The progress card polls and the page reloads itself when the analysis is done.
    expect(page.get_by_text(re.compile("come down to|tests passed"))).to_be_visible(timeout=120_000)


def test_compare_and_delete(page: Page) -> None:
    page.goto("/compare")
    expect(page.get_by_text("new problems", exact=True)).to_be_visible()
    options = page.locator("select[name=base] option")
    if options.count() > 2:  # data-autosubmit: changing a select reloads the comparison
        page.select_option("select[name=base]", index=2)
        page.wait_for_url(re.compile(r"base="))

    # Delete the run created by the upload test (confirmation dialog, then a notice).
    page.goto("/")
    page.get_by_role("link", name="E2E upload").first.click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_text("More").click()
    page.get_by_role("button", name="Delete run").click()
    expect(page.get_by_text("The run and its log files were deleted.")).to_be_visible()
