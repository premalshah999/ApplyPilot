"""Adapter-facing view of MailService: every code, link and receipt goes through its trust checks.

MailService (mail.py) is the only reader of the mailbox. It accepts a message only when Google's
receiving Authentication-Results shows aligned DMARC for the sender, the sender belongs to the
employer's rule, the recipient is exactly the applicant, the message arrived inside this run's
request window, the tenant does not conflict, and exactly one message matches. A request window
(challenge) is opened BEFORE the employer is asked to send mail, and runs that share a sender
domain are serialized (the shared-sender lease), so one run can never consume another's code.
Codes and links stay in the vault until the browser claims them; they never reach a model."""

import asyncio
import time

from sqlalchemy import select

from .db import Mailbox, MailChallenge

LEASE_BUSY = "Another application is verifying"


def host_matches(host, domains):
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


class Inbox:
    def __init__(self, service):
        self.service = service
        self.config = service.config

    @property
    def mail(self):
        return self.service.mail

    def mailbox(self, recipient=""):
        with self.service.db.session() as s:
            query = select(Mailbox).where(Mailbox.state == "connected")
            if recipient:
                query = query.where(Mailbox.email == recipient.lower())
            box = s.scalar(query)
            return box.id if box else None

    @property
    def configured(self):
        return bool(self.mailbox())

    async def rule(self, job, recipient):
        """The employer's sender rule: saved, built from the ATS family, or none."""
        return self.mail.rule_for(job["url"]) or await self.mail.ensure_rule(job, recipient)

    async def arm(self, run_id, job, recipient, kind="auto", since=None, patience=90):
        """Persist the request window before the website sends mail; holds the sender lease.

        Returns the challenge, or None when this employer has no usable rule."""
        rule = await self.rule(job, recipient)
        if not rule:
            return None
        deadline = time.monotonic() + patience
        while True:
            try:
                challenge = self.mail.begin(run_id, rule, recipient, kind, since=since)
                challenge["rule"] = rule
                return challenge
            except ValueError as exc:
                if LEASE_BUSY not in str(exc) or time.monotonic() > deadline:
                    raise
            await asyncio.sleep(1)

    def live(self, challenge):
        if not challenge:
            return False
        with self.service.db.session() as s:
            row = s.get(MailChallenge, challenge["id"])
            return bool(row and row.state in {"pending", "matched"} and row.expires > time.time())

    async def wait(self, challenge):
        """One claimed {kind, value}, or None on expiry, ambiguity, or cancellation.

        Polls the mailbox directly so verification never waits for the background poll interval."""
        if not challenge:
            return None
        cid = challenge["id"]
        while True:
            with self.service.db.session() as s:
                row = s.get(MailChallenge, cid)
                if not row or row.state not in {"pending", "matched"}:
                    return None
                expires = row.expires
            if token := self.mail.claim(cid):
                return token
            if time.time() >= expires:
                self.mail.outcome(cid, "expired", "No uniquely matching verification email arrived in time")
                return None
            try:
                await self.mail.poll_once()
            except Exception:
                pass  # Mailbox status carries the diagnostic; errors may contain provider text.
            if token := self.mail.claim(cid):
                return token
            await asyncio.sleep(min(self.config.mail_poll_seconds, max(0.05, expires - time.time())))

    def finish(self, challenge, state, reason=""):
        """Release the sender lease: verified, failed, cancelled or expired."""
        if challenge:
            self.mail.outcome(challenge["id"], state, reason)

    def reason(self, challenge):
        if not challenge:
            return ""
        with self.service.db.session() as s:
            row = s.get(MailChallenge, challenge["id"])
            return row.reason if row else ""
