# Integration of `main` and the adapter engine

Branch `claude/tender-carson-fkhswq` merges `main` (ef1a26c, the app running on your Mac) with the
deterministic ATS engine (b544d64). Nothing in `main` was overwritten; `main` itself is untouched.

## Upgrade (migration note)

1. Keep `.env` and the Docker volumes as they are. No manual database step is needed.
2. Check out the branch on the Mac and rebuild: `docker compose up --build -d`
   (keep running `python -m jobpilot.cli browser` first, as today).
3. Optional new settings in `.env` (defaults are fine):

| Setting | Default | Meaning |
| --- | --- | --- |
| `ENGINE` | `adapters` | `adapters`: deterministic drivers for multi-page portals (Workday wizard, Oracle, iCIMS, Taleo, SuccessFactors, Eightfold, career sites), falling back to main's guided flow. `guided`: main's flow everywhere, exactly as before. |
| `CAPTCHA_MAX_ROUNDS` | `4` | Paid 2Captcha image rounds per run (one hCaptcha challenge is often 2-3 rounds). `CAPTCHA_MAX_ATTEMPTS` still bounds challenges per page. |
| `APPLICATION_PASSWORD` | empty | Unchanged meaning. `ACCOUNT_PASSWORD` is accepted as the same setting. |
| `ACCOUNT_EMAIL` | profile email | Optional login email override. |
| `ACCOUNT_PASSWORD_RESET` | `true` | Allow recovery through the reset email when an employer rejects the saved password. |
| `MULTIPAGE_TIMEOUT` | `480` | Seconds for multi-page portals (single-page forms keep `APPLICATION_TIMEOUT`). |
| `TELEGRAM_WAIT_SECONDS` | `120` | How long a running application keeps its page open for a live Telegram answer before deferring. |
| `TRACE_STEPS` | `true` | Per-step summaries (and screenshots/HTML for non-sensitive steps) under `DATA_DIR/runs/<id>/steps`. |

Removed: `AUTO_REQUEUE` (an answered question always resumes its application), `ACCOUNT_AUTO_CREATE`
(the profile switch "Create and reuse employer accounts" decides), `IMAP_HOST/PORT`.

What happens automatically at startup (idempotent, safe to restart):

- **Profile.** Every stored key is kept, including keys this version does not know. Saved address
  facts (`street_address`, `postal_code`, `county`, `country_of_residence`, ...) fill the new
  structured address only where it is empty. Reviewed answers, `application_source` (LinkedIn), the
  consent switches and `autonomous` are untouched. Saving the profile from the dashboard never
  drops reviewed answers.
- **Credentials.** The shared login already stored encrypted in your database keeps being used for
  every employer. `APPLICATION_PASSWORD`/`ACCOUNT_PASSWORD` only seeds a new shared login, and an
  unset variable never disables account creation (one strong password is generated once).
  Per-employer records keep their saved password until a reset is confirmed.
- **Earlier adapter branch data** (only if that branch ever ran against this database): its
  `knowledge` table is copied into reviewed answers with scope, and its `accounts` table into
  encrypted records only where its fingerprint proves the configured password. The old tables
  are left in place; `migrated:*` settings record the one-time import.
- **Email rules.** Existing rules are kept; Workday rules gain `otp.workday.com`. Oracle, iCIMS,
  Taleo, SuccessFactors, Eightfold, SmartRecruiters, Avature and Jobvite rules are created on first
  use (Greenhouse and Workday already were).

## How each ATS is driven (`ENGINE=adapters`)

| ATS | Path |
| --- | --- |
| Greenhouse, Lever, Ashby (+ SmartRecruiters, Workable, BambooHR) | main's guided flow and email verification, unchanged; desktop navigator as fallback |
| Workday | main's `WorkdayAuth` for account access (both engines), then the Workday adapter for the wizard |
| Oracle, iCIMS, Taleo, SuccessFactors, Eightfold, unknown career sites | adapter state machine; unrecognized pages fall back to the guided flow, never after a submit was attempted |

## Trust rules

- **Confirmed** needs a website confirmation captured after this run's submit (it must differ from
  anything shown before the click), an authenticated acknowledgement email from the employer or an
  ATS sending for it, or your reconciliation. Anything else becomes `submission_unknown` (if a
  submit was attempted) or needs review. A confirmation-looking page this run did not produce is
  never recorded as an application.
- Receipts keep **`website_confirmed`** and **`email_confirmed`** separately. An authenticated email
  from an unrelated sender is kept as evidence but cannot confirm.
- `submission_unknown` is never retried automatically. A restart during the final submit marks the
  run `submission_unknown`; it cannot be queued again; a trusted acknowledgement email can still
  confirm it later.
- **Codes and links** come only from main's MailService: Google's receiving `Authentication-Results`
  with aligned DMARC, a sender in the employer's rule (aligned subdomains; employer-owned domains
  for families that send that way), the exact applicant address, the request window opened before
  the website sent the mail, no conflicting tenant (e.g. `globex@myworkday.com` never verifies an
  Acme application), exactly one matching message, consumed once. Runs whose employers share a
  sender are serialized (shared-sender lease), so a code cannot reach the wrong application.
- **Secrets** stay out of model prompts, events and traces: codes are sealed in the vault until the
  browser claims them; auth-field values are masked in every observation; account and
  verification steps keep only a text summary (no screenshot or HTML).

## Answers (autonomous policy)

The AI chooses wording, examples and emphasis without asking when the resume/profile and job
description support it, and may synthesize why-company/why-role answers. With a known fact it
picks the closest valid option; it skips unsupported optional fields; consent and the LinkedIn
source follow your switches. It never invents personal facts, qualifications, dates, identity,
legal disclosures or eligibility. Only a genuinely missing required fact is asked on Telegram (one
question at a time, a running application first); the reply is saved with provenance and the
application resumes. Relationship and motivation answers stay scoped to their employer.

## Test evidence

Run on this branch (Linux, Chromium 1194, no network access to employers):

```
.venv/bin/python -m pytest -q        # 162 passed (main's tests + the adapter suite + trust tests)
.venv/bin/ruff check jobpilot tests  # clean
cd frontend && npm run build         # type check + build pass
```

Owned (mock) coverage relevant to the handoff: dry runs on every mock portal record zero
submissions; owned submit runs record exactly one submission and one website receipt; decoy
verification emails (spoofed, look-alike domain, other recipient, other tenant) are never entered;
a restart during submit cannot resubmit; CAPTCHA widgets never become fields or navigation; an open
Reset Password dialog takes precedence over the page behind it; geolocation is denied in pages
and popups; resume after a Telegram answer. **These are mock results, not evidence that live
tenants work.**

## Live results

No live application was attempted from this environment: it cannot reach employer sites or your
Mac. The table records your last live results and what changed; please run the checks below on the
Mac and send the run IDs or traces back.

| ATS (authorized test URL) | Your last live result | What changed for it | Live status now |
| --- | --- | --- | --- |
| Workday — Mimecast `mimecast.wd5.myworkdayjobs.com/.../R6424-1` | Stopped during account access | Reset only after an explicit wrong-password message; activation email followed before any reset; account form waited for (a vanishing password field is no longer progress); cookie/overlay-safe clicks; Reset Password dialog scoped; response status recorded; states for reset requested / reset email / reset / activation / authenticated; reset cooldown only when the site acknowledged sending | **Not yet run live** |
| iCIMS — HealthEdge `careers-healthedge.icims.com/jobs/8601/...` | CAPTCHA remained after two solver rounds | Multi-round hCaptcha loop within `CAPTCHA_MAX_ROUNDS` (re-observed each round), puzzle-only crop with the printed instruction and example image sent separately, acceptance only when the challenge closes with a token | **Not yet run live** |
| Oracle — JPMC `jpmc.fa.oraclecloud.com/.../job/210771693/` | Solver returned unusable CAPTCHA coordinates | Coordinates are relative to the puzzle crop and mapped by the measured image size (fixes 2× Retina screenshots from the attached Chrome) after scrolling the puzzle into view; out-of-bounds or stale answers are rejected without clicking | **Not yet run live** |

Run on the Mac (dry runs never submit):

```sh
python -m jobpilot.cli browser                      # if the ApplyPilot Chrome is not open
docker compose exec app jobpilot probe "https://mimecast.wd5.myworkdayjobs.com/en-US/Mimecast-Careers/job/Machine-Learning-Engineer-II_R6424-1" --company Mimecast
docker compose exec app jobpilot probe "https://careers-healthedge.icims.com/jobs/8601/machine-learning-engineer/job" --company HealthEdge
docker compose exec app jobpilot probe "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/job/210771693/" --company "JPMorgan Chase"
docker compose exec app jobpilot trace RUN_ID       # step-by-step summary of any run
```

Expected blockers to report exactly if they appear: an hCaptcha puzzle type 2Captcha workers
cannot answer (`last_reason` in the run events), SMS/passkey sign-in, an employer sender domain the
rules do not know yet (the run says so and names the step), and Workday tenants that require a
phone verification.

## Not done (later work)

The handoff's P3 items are only partly in place: the shared-sender lease, sealed secrets in the
vault, and persisted waiting states (`waiting_answer`, `waiting_browser` with the open desktop
tab) exist; typed service boundaries, typed events, vault handles for every secret, and
LangGraph-style checkpoints do not. DBOS still owns queueing, retries and the single commit.
