# Handoff: ApplyPilot integration branch

Branch: `claude/tender-carson-fkhswq` (main is untouched). Read `docs/integration.md` first:
it holds the migration note, the trust rules and the live-results table.

## State

- main (ef1a26c, running on the user's Mac) is merged with the adapter engine (merge 98b567a), then hardened.
- All findings from a 4-dimension adversarial review were fixed with regression tests (`tests/test_review_fixes.py`).
- Last full-suite run: 173 passed, 1 failed, before the final review fixes. The failure was a real bug (a stale mail window being reused) and has since been fixed.
- After the final fixes, only targeted runs were done: review fixes 20/20, plus engine/trust/knowledge/workday 58 passed (one more then failed on a test-setup error, fixed before the 20/20 run).
- **Run the full suite once before deploying:**
  `CHROMIUM_PATH=/opt/pw-browsers/chromium .venv/bin/python -m pytest -q` (~8 min).
- **Nothing has been run against live employers.** The sandbox cannot reach them. Every live result is still open.

## Next steps (in order)

1. Run the full test suite (above). `ruff format` was applied in the last commit, so CI's `ruff format --check` should pass.
2. On the user's Mac, run dry runs (they never submit) for the three authorized URLs and read the traces:
   ```sh
   python -m jobpilot.cli browser
   docker compose up --build -d
   docker compose exec app jobpilot probe "https://mimecast.wd5.myworkdayjobs.com/en-US/Mimecast-Careers/job/Machine-Learning-Engineer-II_R6424-1" --company Mimecast
   docker compose exec app jobpilot probe "https://careers-healthedge.icims.com/jobs/8601/machine-learning-engineer/job" --company HealthEdge
   docker compose exec app jobpilot probe "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/job/210771693/" --company "JPMorgan Chase"
   docker compose exec app jobpilot trace RUN_ID
   ```
3. Fill the live-results table in `docs/integration.md` with real outcomes. Make no claim from mocks.
4. Enable submit mode per ATS only after a clean dry run. If a tenant misbehaves, `ENGINE=guided` restores main's flow.

## Where things are

| Area | Code |
| --- | --- |
| Routing per ATS | `jobpilot/browser.py` `run()` and `use_adapters()`. Greenhouse/Lever/Ashby always use main's guided flow. |
| Workday account access (both engines) | `jobpilot/workday.py` `WorkdayAuth`: create, activation, reverify, reset, bounded retries |
| Multi-page drivers | `jobpilot/adapters/` (base state machine, plus workday/oracle/icims/others) |
| Mail trust (the only mailbox reader) | `jobpilot/mail.py` (DMARC, rule senders, tenant identity, exact recipient, window, lease) and `jobpilot/inbox.py` (adapter facade) |
| CAPTCHA | `jobpilot/capsolver.py`: CapSolver tokens; 2Captcha puzzle crop with rounds; hCaptcha checkbox; image text |
| Evidence and state | `jobpilot/service.py` `complete()` (confirmed needs evidence), `jobpilot/receipts.py` (trusted senders) |
| Answers and knowledge | `jobpilot/answers.py`, `jobpilot/knowledge.py`; the store is `Profile.reviewed_answers` |
| Scanner | `jobpilot/js/scan.js`; `jobpilot/forms.py` handles fill, verify and click |

## Known risks / unverified

- hCaptcha puzzle types 2Captcha workers cannot solve. When this happens, the run ends `waiting_browser` with `last_reason`.
- The live selector for the hCaptcha task area (`.challenge-view`, `.task-grid`, canvas) is unconfirmed. If none match, the code falls back to the whole frame minus the bottom 90 px.
- Oracle and iCIMS may send codes from employer-owned domains. These are accepted only when the domain label exactly equals the tenant or company name.
- WorkdayAuth was written for live automation ids. The mocks follow them but are not proof. A legacy unverified Workday account is recovered with a reset email when there is no resend control.
- P3 is not done: typed services and events, vault handles for every secret, LangGraph-style checkpoints.

## Credentials and policies

- Never resubmit Blend, My Funded Futures, Cargomatic, Zip Co, May Mobility or Ramp. `queue()` already refuses confirmed and uncertain jobs.
- Never print secrets. Keep `.env`, the volumes and the `mail-key` as they are.
