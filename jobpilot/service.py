from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .db import Budget, Job, Resume, Review, Run, Setting, now, record
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
}


class Service:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.dispatch = None
        from .inbox import Inbox
        from .mail import MailService

        self.mail = MailService(db, config)
        self.inbox = Inbox(self)

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
            if state not in TERMINAL:
                state = "failed"
            if run.state == "submitting" and state not in {"confirmed", "submission_unknown"}:
                state = "submission_unknown"
            run.state, run.reason = state, result.get("reason", "")[:4000]
            run.finished_at, run.elapsed = now(), elapsed
            run.receipt = result.get("receipt", {})
            s.get(Job, run.job_id).status = state
            known = {r.key for r in s.scalars(select(Review).where(Review.run_id == run_id))}
            for q in result.get("reviews", []):
                # Questions asked live during the run are already persisted.
                if q.get("key", "manual") in known and q.get("key") not in {"manual", "session"}:
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
            learn = None
            if review.key not in {"manual", "session"}:
                run = s.get(Run, review.run_id)
                job = s.get(Job, run.job_id) if run else None
                learn = (review.question, answer, list(review.options), job.company if job else "")
                item = s.get(Setting, "profile")
                value = dict(item.value if item else {})
                value["approved_answers"] = {**value.get("approved_answers", {}), review.key: answer}
                if item:
                    item.value = value
                else:
                    s.add(Setting(key="profile", value=value))
        if learn:
            from .knowledge import upsert

            question, value, options, employer = learn
            try:
                upsert(self.db, question, value, options, employer, source="review")
            except ValueError:
                pass
        message = "Answer saved to your knowledge base." + (
            " The job will retry automatically." if self.config.auto_requeue else " Requeue the job."
        )
        return {"resolved": True, "message": message}

    async def after_answer(self, review_id):
        """Requeue a deferred job once every question it raised has an answer."""
        if not self.config.auto_requeue:
            return None
        with self.db.session() as s:
            review = s.get(Review, review_id)
            run = s.get(Run, review.run_id) if review else None
            if not run or run.state not in RETRYABLE:
                return None
            open_reviews = s.scalar(
                select(func.count())
                .select_from(Review)
                .where(Review.run_id == run.id, Review.answer.is_(None))
            )
            if open_reviews:
                return None
            newer = s.scalar(select(Run.id).where(Run.job_id == run.job_id, Run.created_at > run.created_at))
            if newer:
                return None
            job_id, mode, resume_id = run.job_id, run.mode, run.resume_id
        try:
            queued = await self.queue(job_id, mode, resume_id, retry=True)
        except ValueError as exc:
            self.db.event(run.id, "requeue_skipped", str(exc)[:300])
            return None
        self.db.event(
            run.id, "requeued", "All questions answered; retrying automatically", {"run": queued["id"]}
        )
        return queued

    def reconcile(self, run_id, submitted, evidence):
        if not evidence.strip():
            raise ValueError("Provide confirmation evidence or an explanation of non-submission")
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            if not run or run.state != "submission_unknown":
                raise ValueError("Only uncertain submissions can be reconciled")
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
