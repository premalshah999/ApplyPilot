from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .db import Budget, Job, Resume, Review, Run, Setting, now, record
from .knowledge import learned_answer
from .schemas import Profile

ACTIVE = {"queued", "running", "submitting"}
RETRYABLE = {"needs_review", "timed_out", "failed"}
TERMINAL = {
    "confirmed",
    "dry_run_passed",
    "needs_review",
    "timed_out",
    "submission_unknown",
    "failed",
    "cancelled",
    "already_applied",
    "closed",
    "skipped",
    "waiting_answer",
    "waiting_browser",
}


class Service:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.dispatch = None
        from .inbox import Inbox
        from .mail import MailService

        self.mail = MailService(db, config)
        self.inbox = Inbox(self)
        self.migrate_reviewed_answers()
        self.migrate_legacy()

    @staticmethod
    def cooldown_key(job):
        parsed = urlsplit(job.url)
        employer = parsed.hostname or ""
        if job.ats in {"greenhouse", "lever", "ashby"}:
            employer += "/" + parsed.path.strip("/").split("/")[0]
        return "submission_cooldown:" + hashlib.sha256(employer.encode()).hexdigest()

    def check_cooldown(self, session, job):
        item = session.get(Setting, self.cooldown_key(job))
        if item and datetime.fromisoformat(item.value["until"]) > datetime.now(UTC):
            raise ValueError("This employer rejected an application as possible spam. Applications there are paused for six hours; other employers can continue.")

    def migrate_reviewed_answers(self):
        """Give existing applicant reviews provenance without changing their answers."""
        with self.db.exclusive() as s:
            item = s.get(Setting, "profile")
            if not item:
                return
            value = dict(item.value)
            learned = {a["id"]: a for a in value.get("reviewed_answers", [])}
            for review, job in s.execute(
                select(Review, Job)
                .join(Run, Review.run_id == Run.id)
                .join(Job, Run.job_id == Job.id)
                .where(Review.answer.is_not(None))
            ):
                # Only migrate actual answers which are still in the approved answer store.
                if (
                    review.id not in learned
                    and review.key not in {"manual", "session"}
                    and value.get("approved_answers", {}).get(review.key) == review.answer
                ):
                    learned[review.id] = learned_answer(
                        review.id, review.question, review.answer, review.options, job.company or job.url
                    )
            value["reviewed_answers"] = list(learned.values())
            item.value = value

    def migrate_legacy(self):
        """One-way, idempotent import of data written by the earlier adapter branch.

        Its `knowledge` table becomes reviewed answers with provenance; its `accounts` table
        becomes encrypted employer account records, but only where the password it recorded is
        provably the configured one (fingerprint match). Legacy tables are left untouched."""
        from sqlalchemy import inspect, text

        from .knowledge import save

        tables = set(inspect(self.db.engine).get_table_names())
        if "knowledge" in tables and not self.db.get_setting("migrated:knowledge"):
            with self.db.engine.connect() as c:
                rows = list(c.execute(text("SELECT * FROM knowledge")).mappings())
            for row in rows:
                if not row.get("question") or not row.get("answer"):
                    continue
                scope = row.get("scope") or "global"
                employer = "" if scope == "global" else scope.removeprefix("employer:")
                options = row.get("options") or []
                if isinstance(options, str):
                    options = json.loads(options or "[]")
                try:
                    save(
                        self.db, row["question"], row["answer"], options, employer,
                        "employer" if employer else None, review_id="legacy-" + str(row["id"])[:24],
                    )
                except (ValueError, KeyError, TypeError):
                    continue
            self.db.set_setting("migrated:knowledge", {"rows": len(rows), "at": now()})
        if "accounts" in tables and not self.db.get_setting("migrated:accounts"):
            import hashlib as _hashlib
            import hmac

            from .accounts import AccountStore

            password = self.config.application_password
            fingerprint = (
                hmac.new(self.config.app_token.encode(), password.encode(), _hashlib.sha256).hexdigest()[:16]
                if password and self.config.app_token
                else ""
            )
            with self.db.engine.connect() as c:
                rows = list(c.execute(text("SELECT * FROM accounts")).mappings())
            moved = 0
            for row in rows:
                host = (row.get("realm") or "").split(":")[1] if ":" in (row.get("realm") or "") else ""
                if not host or not row.get("email") or not fingerprint or row.get("password_hash") != fingerprint:
                    continue
                store = AccountStore(self, row["email"])
                url = "https://" + host
                if store.get(url):
                    continue  # Never overwrite a record main already keeps for this employer.
                state = {"active": "authenticated", "verified": "authenticated", "reset": "password_reset"}.get(
                    row.get("state"), "created_locally"
                )
                store.save(url, {"email": row["email"].lower(), "password": password}, state, migrated=True)
                moved += 1
            self.db.set_setting("migrated:accounts", {"rows": len(rows), "moved": moved, "at": now()})

    async def queue(self, job_id, mode="dry_run", resume_id=None, retry=False):
        profile = Profile.model_validate(self.db.get_setting("profile", {}))
        with self.db.exclusive() as s:
            job = s.get(Job, job_id)
            if not job:
                raise ValueError("Job not found")
            previous = list(s.scalars(select(Run).where(Run.job_id == job_id)))
            if any(x.state in ACTIVE for x in previous):
                raise ValueError("This job already has an active run")
            if any(x.state in {"confirmed", "submission_unknown", "already_applied"} for x in previous):
                raise ValueError("This job is submitted or has an uncertain submission. Reconcile it first.")
            self.check_cooldown(s, job)
            chosen = s.get(Resume, resume_id or job.resume_id or "")
            if not chosen:
                raise ValueError("Choose an uploaded resume or classify the job first")
            if chosen.demo != job.demo:
                raise ValueError("Demo resumes can only be used on local demo jobs")
            if not job.demo and (not profile.name or not profile.email):
                raise ValueError("Complete your profile name and email before applying")
            if not job.demo and not self.config.mimo_api_key:
                raise ValueError("Add MIMO_API_KEY to .env and restart before live browser runs")
            if mode == "submit" and not job.demo:
                control = s.get(Setting, "control").value
                if not control.get("auto_submit"):
                    raise ValueError("Enable automatic submission in Settings first")
                # An automatic retry after answered questions keeps the original approval.
                if job.status != "ready" and not (retry and job.status in RETRYABLE):
                    raise ValueError("Approve or classify this job before submission")
            packet_profile = profile.model_dump()
            if job.demo:
                packet_profile = {
                    "name": "Alex Example",
                    "email": "alex@example.test",
                    "phone": "2025550100",
                    "location": "New York",
                    "facts": {"first_name": "Alex", "last_name": "Example"},
                }
            path = self.config.data_dir / "resumes" / chosen.filename
            run = Run(
                job_id=job.id,
                mode=mode,
                resume_id=chosen.id,
                packet={
                    "profile": packet_profile,
                    "resume_path": str(path),
                    "resume_sha": chosen.sha256,
                    "resume_text": "" if job.demo else (chosen.text or "")[:16000],
                    "match_approved": job.status == "ready",
                },
            )
            s.add(run)
            s.flush()
            job.status = "queued"
            result = record(run)
        self.db.event(run.id, "queued", "Application queued", {"mode": mode, "resume": chosen.name})
        if self.dispatch:
            await self.dispatch(run.id)
        return result

    def reserve_submission(self, run_id):
        day = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            job = s.get(Job, run.job_id)
            control = s.get(Setting, "control").value
            if control.get("paused"):
                raise ValueError("Workers were paused before submission")
            if run.state != "running" or run.mode != "submit":
                raise ValueError("Submission is not authorized for this run")
            if not job.demo:
                if not control.get("auto_submit"):
                    raise ValueError("Automatic submission was disabled")
                budget = s.get(Budget, day)
                if not budget:
                    budget = Budget(day=day, spent=0, submissions=0)
                    s.add(budget)
                if budget.submissions >= self.config.daily_application_limit:
                    raise ValueError("Daily submission cap reached")
                budget.submissions += 1
            run.state = "submitting"

    def complete(self, run_id, result, elapsed):
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            if not run or run.state == "cancelled":
                return
            state = result.get("state", "failed")
            if state == "needs_review" and run.packet.get("profile", {}).get("autonomous"):
                answerable = any(q.get("key") not in {"manual", "session"} for q in result.get("reviews", []))
                state = "waiting_answer" if answerable and self.config.telegram_bot_token and self.config.telegram_user_id else "skipped"
                questions = [q["question"] for q in result.get("reviews", [])]
                if questions:
                    result["reason"] = (
                        result.get("reason", "Missing information") + ": " + "; ".join(questions)
                    )
            if state not in TERMINAL:
                state = "failed"
            evidence = result.get("receipt", {})
            if state == "confirmed" and not (
                evidence.get("type") in {"explicit_confirmation_page", "user_reconciled"} or evidence.get("email")
            ):
                # "Confirmed" needs a website confirmation, an employer email, or the applicant's word.
                state = "submission_unknown" if run.state == "submitting" else "needs_review"
                result = {**result, "reason": "No confirmation evidence was captured"}
            rejected = evidence.get("type") == "explicit_rejection_page"
            if run.state == "submitting" and state not in {"confirmed", "submission_unknown"} and not rejected:
                state = "submission_unknown"
            run.state, run.reason = state, result.get("reason", "")[:4000]
            run.finished_at, run.elapsed = now(), elapsed
            run.receipt = result.get("receipt", {})
            receipt = dict(result.get("receipt", {}))
            if receipt:
                # Separate evidence: what the employer's website showed vs. what its email said.
                receipt["website_confirmed"] = bool(
                    state == "confirmed" and receipt.get("type") == "explicit_confirmation_page"
                )
                receipt["email_confirmed"] = bool(receipt.get("email"))
                run.receipt = receipt
            if rejected and re.search(r"spam|too many requests|rate limit", str(run.receipt), re.I):
                key = self.cooldown_key(s.get(Job, run.job_id))
                cooldown = {"until": (datetime.now(UTC) + timedelta(hours=6)).isoformat(), "run_id": run_id}
                item = s.get(Setting, key)
                if item:
                    item.value = cooldown
                else:
                    s.add(Setting(key=key, value=cooldown))
            s.get(Job, run.job_id).status = (
                "ready" if state == "dry_run_passed" and run.packet.get("match_approved") else state
            )
            # Questions already asked live during the run (Telegram) are persisted; never duplicate.
            known = {
                r.key
                for r in s.scalars(select(Review).where(Review.run_id == run_id))
                if r.key not in {"manual", "session"}
            }
            for q in result.get("reviews", []) if state in {"needs_review", "waiting_answer"} else []:
                if q.get("key", "manual") in known:
                    continue
                s.add(
                    Review(
                        run_id=run_id,
                        question=q["question"][:8000],
                        options=q.get("options", []),
                        key=q.get("key", "manual"),
                        reason=q.get("reason", ""),
                    )
                )
        self.db.event(
            run_id, "finished", result.get("reason", state), {"state": state, "elapsed": round(elapsed, 2)}
        )

    def answer_review(self, review_id, answer):
        if not answer.strip() or len(answer) > 8000:
            raise ValueError("Provide an answer of 1–8000 characters")
        with self.db.exclusive() as s:
            review = s.get(Review, review_id)
            if not review:
                raise ValueError("Review not found")
            if review.answer is not None:
                raise ValueError("This review has already been resolved")
            if review.options and answer not in review.options:
                raise ValueError("Choose an offered option")
            review.answer = answer
            if review.key not in {"manual", "session"}:
                item = s.get(Setting, "profile")
                value = dict(item.value if item else {})
                value["approved_answers"] = {**value.get("approved_answers", {}), review.key: answer}
                run = s.get(Run, review.run_id)
                job = s.get(Job, run.job_id) if run else None
                if job:
                    value["reviewed_answers"] = [
                        *value.get("reviewed_answers", []),
                        learned_answer(
                            review.id, review.question, answer, review.options, job.company or job.url
                        ),
                    ]
                if item:
                    item.value = value
                else:
                    s.add(Setting(key="profile", value=value))
        return {"resolved": True, "message": "Answer saved to your knowledge base."}

    async def resolve_review(self, review_id, answer):
        result = self.answer_review(review_id, answer)
        with self.db.exclusive() as s:
            review = s.get(Review, review_id)
            run = s.get(Run, review.run_id)
            remaining = s.scalar(
                select(Review.id).where(Review.run_id == review.run_id, Review.answer.is_(None)).limit(1)
            )
            control = s.get(Setting, "control").value
            if not run or remaining or control.get("paused") or review.key in {"manual", "session"}:
                return result
            job = s.get(Job, run.job_id)
            newer = s.scalar(
                select(Run.id).where(Run.job_id == run.job_id, Run.created_at > run.created_at).limit(1)
            )
            if newer or run.state not in {"needs_review", "waiting_answer"}:
                return result
            if run.mode == "submit":
                if not control.get("auto_submit") or not run.packet.get("match_approved"):
                    return result
                job.status = "ready"
            job_id, mode, resume_id = run.job_id, run.mode, run.resume_id
        try:
            resumed = await self.queue(job_id, mode, resume_id)
        except ValueError as exc:
            result["message"] += " " + str(exc)
        else:
            result.update(run_id=resumed["id"], message="Answer saved. Application resumed automatically.")
        return result

    def reconcile(self, run_id, submitted, evidence):
        if not evidence.strip():
            raise ValueError("Provide confirmation evidence or an explanation of non-submission")
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            if not run or run.state not in {"submission_unknown", "already_applied"}:
                raise ValueError("Only uncertain submissions or already-applied notices can be reconciled")
            run.state = "confirmed" if submitted else "needs_review"
            run.receipt = {"type": "user_reconciled", "evidence": evidence[:4000], "at": now()}
            run.reason = "Submission reconciled by the applicant"
            s.get(Job, run.job_id).status = run.state
        self.db.event(run_id, "reconciled", "Applicant reconciled submission", {"submitted": submitted})

    async def execute(self, run_id):
        import time

        from .browser import BrowserEngine

        with self.db.exclusive() as s:
            row = s.get(Run, run_id)
            if not row or row.state in TERMINAL:
                return
            if row.state != "queued":
                # Duplicate deliveries do not own this run. Recovery is handled once at startup.
                return
            if s.get(Setting, "control").value.get("paused"):
                return
            row.state, row.started_at = "running", now()
            s.get(Job, row.job_id).status = "running"
            run, job = record(row), record(s.get(Job, row.job_id))
        started = time.monotonic()
        try:
            with self.db.session() as s:
                self.check_cooldown(s, s.get(Job, run["job_id"]))
            result = await BrowserEngine(self, run_id).run(job, run)
        except Exception as exc:
            # Keep provider payloads and secrets out of the event stream.
            known = isinstance(exc, (ValueError, RuntimeError)) and "api_key" not in str(exc).lower()
            result = {
                "state": "failed",
                "reason": str(exc)[:400] if known else f"Worker error: {type(exc).__name__}",
            }
        self.complete(run_id, result, time.monotonic() - started)
        from .telegram import notify_run

        await notify_run(self, run_id)
