# Validation record

Updated 2026-10-01 UTC after adding executable deployment acceptance checks.

| Check | Result |
| --- | --- |
| Clean Python installation from `uv.lock` | Passed with Python 3.12 |
| TypeScript and Vite production build | Passed, Vite 8.3.1 |
| Ruff lint and format check | Passed |
| Automated suite with real Chromium | **32 passed in 22.67 seconds** |
| `jobpilot verify` against a running native installation | Passed: authenticated API, database, dashboard assets, dry run, received submission, PDF checksum, saved evidence |
| Actual server process restart, SQLite | Passed: the same two runs and their receipt, ledger, and screenshot hashes survived the restart |
| Frontend dependency audit | Zero known advisories reported by `npm audit` at validation time |
| Compose and GitHub workflow YAML | Parsed successfully |
| Python wheel build | Passed |
| Docker image | Build and runtime acceptance configured in CI; no workflow run observed at publication; not built locally |
| PostgreSQL deployment | Not executed locally: no Docker daemon; runtime validation remains pending |

The suite covers:

- ATS host boundaries and canonical job identity, including tracking URLs, Workday apply paths, Greenhouse host aliases, and Oracle SPA routes.
- Private-address URL rejection, dashboard authentication, cookie flags, origin checks, token exclusion from responses, and PDF validation/deduplication.
- Applicant facts, exact employer-scoped answers, demographic decline, disclosure polarity, invalid model evidence, and review option validation.
- Queue admission races, immutable profile packets, duplicate workflow delivery, atomic daily reservations, pause-before-commit, uncertain-submission retry blocking, reconciliation, and startup recovery.
- Shared model budgeting, MiMo JSON-mode/token-parameter normalization, and real PydanticAI structured-output parsing against a local scripted provider.
- Telegram sender allowlisting and authorized pause control with the delivery API mocked.
- Real browser filling of native fields, file upload, radio groups, native selects, committed ARIA comboboxes, newly required conditional fields, altered-value detection, and the final-submit guard.
- A **real Browser Use agent** connected over CDP, invoking the actual restricted tools through the actual metered MiMo-compatible HTTP client. The provider responses are scripted locally, so this validates integration rather than model reasoning quality.
- Durable DBOS delivery through Chromium to a local submission receipt, a dry run with no final submission, saved screenshots/answer evidence, and exclusion of demo data from real success metrics.
- Deployment verification that checks server-side submission counts, all received synthetic answers, the received PDF checksum, and evidence integrity. A corrupted screenshot fails verification. A paused installation is reported as paused rather than silently resumed.
- Dashboard login, all navigation pages, desktop rendering, mobile overflow, and browser JavaScript errors.

The initial execution environment could not download Playwright's bundled browser from its CDN. Tests used a separately obtained Chromium 153 executable via `CHROMIUM_PATH`; deployment and CI install the browser matched to the pinned Playwright 1.58.0 package. This difference is why CI and the deployment's local demo are part of the setup checks.

The separate process-restart check launched the actual `jobpilot serve` command, ran `jobpilot verify`, stopped and restarted that server using the same data directory, and ran `jobpilot verify --recheck` against the saved manifest. This used SQLite; it is not a PostgreSQL result. CI now starts the Docker/PostgreSQL stack and runs the same acceptance and restart checks. Its outcome must be read from an actual workflow run, not inferred from this configuration.

**Not validated with live accounts:** real MiMo inference and billing, Telegram delivery, CapSolver challenge solving, employer-specific application submissions, 80–100 applications/day sustained throughput, or a sub-minute service-level guarantee. No real employer applications were submitted during these tests.

After adding credentials, validate one representative application per employer flow in dry-run mode, inspect its ledger and screenshot, then verify an authorized submission receipt before increasing volume. Complex account, OTP, repeated-history, and custom-widget flows may still require employer-specific work or manual review.
