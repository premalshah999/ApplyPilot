"""One login identity for every employer portal. The password stays in the environment."""

import hashlib
import hmac
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select

from .db import Account, now, record
from .inbox import host_matches

# Hosts where an ATS legitimately asks for the candidate password.
AUTH_HOSTS = {
    "workday": ["myworkdayjobs.com", "myworkdaysite.com", "myworkday.com", "workday.com"],
    "oracle": ["oraclecloud.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net"],
    "successfactors": ["successfactors.com", "successfactors.eu", "sapsf.com", "jobs2web.com"],
    "eightfold": ["eightfold.ai"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
    "jobvite": ["jobvite.com"],
}


def registrable(host):
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class Accounts:
    def __init__(self, service):
        self.service, self.db, self.config = service, service.db, service.config

    @property
    def email(self):
        profile = self.db.get_setting("profile", {}) or {}
        return self.config.login_email or profile.get("email", "")

    @property
    def password(self):
        return self.config.account_password

    @property
    def ready(self):
        return bool(self.email and self.password)

    def fingerprint(self):
        return hmac.new(self.config.app_token.encode(), self.password.encode(), hashlib.sha256).hexdigest()[
            :16
        ]

    @staticmethod
    def realm(ats, url):
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        if ats == "successfactors":
            company = dict(parse_qsl(p.query)).get("company", "")
            return f"{ats}:{host}:{company}" if company else f"{ats}:{host}"
        return f"{ats}:{host}"

    def get(self, realm):
        with self.db.session() as s:
            row = s.scalar(select(Account).where(Account.realm == realm))
            return record(row) if row else None

    def mark(self, realm, ats, state, error=""):
        with self.db.exclusive() as s:
            row = s.scalar(select(Account).where(Account.realm == realm))
            if not row:
                row = Account(realm=realm, ats=ats, email=self.email, resets=0, state="unknown")
                s.add(row)
            row.state, row.last_error, row.updated_at = state, error[:500], now()
            if state in {"created", "verified", "active"}:
                row.password_hash = self.fingerprint()
            if state == "reset":
                row.resets += 1
                row.password_hash = self.fingerprint()
            s.flush()
            return record(row)

    def resets_used(self, realm):
        row = self.get(realm)
        return row["resets"] if row else 0

    def password_allowed(self, ats, frame_url, job_url):
        """Type the shared password only on the employer's site or its ATS's own auth hosts."""
        frame = (urlsplit(frame_url).hostname or "").lower()
        job = (urlsplit(job_url).hostname or "").lower()
        if urlsplit(frame_url).scheme != "https" and not self.config.allow_private_urls:
            return False
        if frame == job or registrable(frame) == registrable(job):
            return True
        return host_matches(frame, AUTH_HOSTS.get(ats, []))

    def summary(self):
        with self.db.session() as s:
            rows = [
                {k: v for k, v in record(a).items() if k != "password_hash"}
                for a in s.scalars(select(Account).order_by(Account.updated_at.desc()).limit(500))
            ]
        current = self.fingerprint() if self.password else ""
        for row in rows:
            with self.db.session() as s:
                stored = s.scalar(select(Account.password_hash).where(Account.realm == row["realm"]))
            row["password_current"] = bool(current and stored == current)
        return {"email": self.email, "password_configured": bool(self.password), "accounts": rows}
