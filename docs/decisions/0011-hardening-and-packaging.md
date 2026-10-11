# 0011: Hardening and packaging

**Status:** accepted

## Goal
Anyone can start VeriMind with one action on Windows, macOS or Linux; it's safe by default; and every
claim about it is checked automatically.

## One-click start
- **Start/stop scripts:** `start.bat` / `start.ps1` (Windows, double-click) and `start.sh` (macOS/Linux).
  The start script:
  1. checks that Docker is installed and running, with a plain-language message if not;
  2. creates `.env` from the example;
  3. builds and starts the stack and waits for the health endpoint;
  4. opens the browser.

  If startup is slow, it runs `verimind doctor` and shows what is wrong. `stop.*` keeps all data.
- **`verimind doctor`** checks the database connection, schema version, storage permissions, log-format
  files, whether the worker is keeping up, and AI configuration. Each problem comes with a fix hint.
- **Found while testing the scripts:**
  - Windows resolves `localhost` to IPv6 first while Docker published only on IPv4 `127.0.0.1`.
    Browsers fall back silently; PowerShell didn't. The scripts now poll `127.0.0.1` directly.
  - PowerShell 5.1 treats Docker's progress output as fatal under strict error handling when output
    is redirected. The script now checks exit codes explicitly instead.
- `.gitattributes` pins line endings, because a shell script checked out with Windows line endings
  fails on macOS/Linux.

## Security
- **Localhost by default:** the web port is published on `127.0.0.1`. Sharing on a network is an
  explicit choice (`VERIMIND_BIND=0.0.0.0`). Postgres is never published.
- **Content Security Policy:** pages may only run scripts served by VeriMind (`script-src 'self'`).
  Every inline script and `onclick`-style handler moved into `static/js/` and `data-` attributes.
  Swagger UI at `/api/docs` gets its own policy for its CDN assets. Also `X-Frame-Options: DENY`,
  `nosniff`, `Referrer-Policy` and `Permissions-Policy`.
- **Cross-site request check:** data-changing requests whose `Origin` is not VeriMind's own are
  refused (403). Browsers always send `Origin` on cross-site posts; scripts and `curl` don't, so the
  API stays scriptable. Extra origins (reverse proxies) are configured with `VERIMIND_ALLOWED_ORIGINS`.
- Existing protections from earlier phases: upload size limits checked before reading, zip-slip,
  symlink and zip-bomb defenses, path normalization, a non-root container user, and parameterized SQL.
- **Dependency audit:** `pip-audit` reports no known vulnerabilities in the environment or in the
  image (pip itself is upgraded during the build). It runs in CI on every push.

## Packaging
- **Smaller image:** removing test suites bundled inside third-party packages cut it from 723 MB to
  641 MB. Most of the rest is scipy/scikit-learn, which the risk model needs.
- **Multi-architecture:** `docker compose` builds natively on the user's machine, so Apple Silicon
  works directly. The ARM64 image was built under emulation and smoke-tested (`aarch64`, app starts,
  scikit-learn and the Postgres driver load). A release workflow publishes `linux/amd64` +
  `linux/arm64` images to GHCR when a version tag is pushed.

## Verification
- **Faster test suite:** 3 min → about 1 min. Profiling showed time going into regenerating the same
  demo dataset in every test; it's now generated once per session and reused (read-only).
- **End-to-end browser tests (Playwright):** they click through the live stack. Covered: demo →
  problem → triage saved in place → log viewer scrolled to the root cause; drag-and-drop upload →
  progress → results; compare (auto-submit); delete with a confirmation dialog. Each test fails on any
  browser console error, which proves the CSP doesn't break the pages. That check caught a missing
  favicon (a 404 on every first visit). Locally they use the installed Edge/Chrome; CI starts the
  real stack with `start.sh` and runs them in Chromium.
- **Load:** a 2,000-test / 2.46 M-line regression is analyzed and stored in about 5 s by the
  containerized worker. Run, tests, log viewer, coverage and export pages respond in under 50 ms;
  Trends (which retrains the risk model) in about 0.2 s.

## Deliberately not done
- **Authentication / multi-user accounts.** VeriMind runs on localhost or a trusted team network.
  Exposing it publicly would call for a reverse proxy with SSO in front of it.
- **Automatic demo data on first launch.** The first screen offers it as one click. Loading it
  automatically would mix fake runs into a real team's data.
