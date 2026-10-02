# Operating the application

## Configuration and clocks

Source cron expressions have five fields and run in `TIMEZONE`. The scheduler evaluates due sources once per minute. The daily cap and model budget use that same timezone, including daylight-saving changes. Three application workers and one discovery worker are enabled by default. One employer host has at most one active application, even if other hosts are running concurrently.

Set the application port and `BASE_URL` consistently. With the default settings, open `http://127.0.0.1:8080`, not `http://localhost:8080`. Origin checks use an exact match. Native `jobpilot serve --port 8090` also requires `BASE_URL=http://127.0.0.1:8090`.

## Source URL examples

Use a real company slug in these templates:

| Type | URL shape |
| --- | --- |
| Greenhouse | `https://boards.greenhouse.io/company` or `https://job-boards.greenhouse.io/company` |
| Lever | `https://jobs.lever.co/company` |
| Ashby | `https://jobs.ashbyhq.com/company` |
| SmartRecruiters | `https://jobs.smartrecruiters.com/Company` |
| Workday | `https://tenant.wd5.myworkdayjobs.com/en-US/Site` |
| Career page | Public page whose returned HTML contains job links |

Workday's conventional CXS endpoint is derived from the hostname tenant and final board path segment. Custom tenants, redirects, and `myworkdaysite.com` layouts may need imported job URLs or an employer-specific discovery adapter. Oracle/iCIMS/Taleo feeds are not reverse-engineered private submission APIs; use public links or import individual jobs.

Enabling auto-queue classifies only newly discovered jobs in that sweep. Jobs skipped because required configuration is missing remain in Applications for manual classification and queueing. Existing imported jobs are not silently submitted later when you enable the global switch.

## Readiness and results

Run `docker compose exec app jobpilot verify` against a running deployment before configuring real applications. Native installations use `uv run jobpilot verify`. The command creates two synthetic runs, requires zero server receipts for the dry run and exactly one for the submission, checks every submitted demo answer and the actual PDF checksum, and saves a private evidence manifest. It does not alter your profile, resumes, pause switch, automatic-submission setting, or real application counts. Resume paused workers explicitly before this check. No MiMo or CapSolver calls are made; an already configured Telegram bot may send the usual run notifications.

After restarting, run `docker compose exec app jobpilot verify --recheck /app/.data/verification.json`. This reads the same runs and compares hashes of their receipts, answer ledgers, and screenshots. It creates no new applications. Both commands exit nonzero if checks fail. The manifest contains only synthetic run IDs, timestamps, and evidence hashes, with file permissions restricted to its owner.

`jobpilot doctor` checks configuration presence only. A present key is not evidence that a provider works.

- **Ready:** classifier found an eligible match at or above the profile's minimum score, with a selected resume and no declared uncertainties, or the applicant explicitly approved the match.
- **Dry run passed:** the worker verified fields and an attachment and withheld the final commit action. This is not an employer submission. Some websites may still save draft field values as you type.
- **Needs review:** missing evidence, authentication, an unsupported control, or an incomplete final verification.
- **Submission unknown:** the commit may have happened; automatic retry is blocked. Verify externally and reconcile through run details.
- **Confirmed:** an explicit changed confirmation page was captured, or the applicant reconciled it as submitted with evidence. Receipts identify which kind.

The pause switch blocks admission to execution and the final commit check. It does not retract a request already sent. Active navigation stops at its next stop callback; browser cleanup then runs. Cancelling a queued run marks it cancelled even if DBOS later delivers the workflow.

## Telegram

Create a bot, set its token and your numeric Telegram user ID, restart, and send `/start`. The bot uses long polling, not a public webhook. Every command and callback checks the sender ID. Replies always go to the configured private chat ID.

`/apply URL` imports and classifies the role, then queues a dry run when automatic submission is disabled, or a submission if the role passes admission checks and the switch is enabled. `/answer` stores exact scoped answers; it never automatically requeues the application. Up to five unresolved questions are included in a run notification; the full inbox remains in the dashboard.

Offsets are persisted before command processing to avoid replaying a side effect. A command interrupted by a crash may need to be sent again. Notifications are best-effort; the dashboard remains the durable record.

## Credentials and employer state

The dashboard token is generated in `.data/access-token` unless `APP_TOKEN` is configured. Print it with `jobpilot token`; never commit or send it with a job application. Rotate by setting a new `APP_TOKEN` and restarting, which also invalidates existing dashboard cookies.

Session captures include employer cookies and local storage, not a browser profile or passwords. Storage state is indexed by the exact employer hostname. Separate hosts may need separate captures. Session import rejects unrelated domains and oversized files; the worker is still subject to session expiration, MFA, and cross-domain login flows.

The container runs as an unprivileged user and Chromium is launched with `--no-sandbox` for container portability. Keep this dedicated application isolated from sensitive local services; the browser process is not a hardened multi-tenant sandbox. Do not expose CDP ports or mount your home directory into the container.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Origin error | Browser URL must exactly match `BASE_URL`; recreate the app after changing `.env` |
| Chromium missing | `uv run playwright install --with-deps chromium`, or rebuild the Docker image |
| Authentication review | Capture/import a session for that employer and requeue |
| Required question unresolved | Add a scoped fact, exact consent, or review answer; do not approve a guessed value |
| MiMo failures | Verify model/base URL/key, provider availability, token budget, and the run event timeline |
| Daily model budget reached | Check configured token prices and recent failures; uncertain transport spend stays reserved |
| Submission cap reached | Reservations reset on the next configured local day; uncertain submissions still reserve slots |
| Discovery found zero | Check company board slug, source type, public API response, and the source error field |
| Captcha unresolved | Only the documented challenge types get one attempt; handle remaining challenges manually |
| Run was interrupted | Startup puts it into review or uncertain submission; inspect before requeueing |
| UI assets missing | Build `frontend` or use the Docker image |

View service output with `docker compose logs --tail=100 app`. Routine run evidence is in the dashboard and `.data/runs/RUN_ID/`. Avoid sharing screenshots or answer ledgers publicly.

## Backup and upgrade

Stop the app before taking a native `.data` backup. For Docker, stop the app and back up the applicant-data volume and a PostgreSQL dump. Restart after the backup is complete. Keep recovery copies encrypted and access-controlled.

This is schema version 1. New installs create tables automatically; there is no migration from the legacy CLI database. Future schema changes need an explicit migration and a tested backup/restore path. Do not downgrade a live data volume across incompatible schema changes.

The workflow is configured to run SQLite/Chromium integration tests, start the Docker/PostgreSQL deployment, and verify receipts and evidence persistence after an app restart. Its `deployment-evidence` artifact contains the manifest and container logs. No GitHub workflow run was observed at publication, and this development environment has no Docker daemon. PostgreSQL runtime validation is still pending until that workflow or your deployment check passes. Before sustained use, verify representative real applications with your own provider credentials.

## Email connections and MCP

See [email verification](email-verification.md) for Google OAuth setup, trusted employer rules, diagnostics, and key backup. Back up the generated `DATA_DIR/mail-key` securely alongside the database, or supply a separately managed `MAIL_ENCRYPTION_KEY`. Existing database tables are unchanged; the email feature adds mailbox, rule, challenge, message-consumption, and OAuth-state tables on startup.

The [MCP server](mcp.md) runs locally over stdio and reads from the existing authenticated API. It does not need a second provider key or a public listener.
