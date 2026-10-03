"""Account credentials stay outside model prompts and answer ledgers."""

import hashlib
import secrets
import re

from .db import Setting, now
from .mail import origin


class AccountStore:
    def __init__(self, service, email):
        self.service, self.email = service, email.strip().lower()

    def key(self, url):
        return "employer_account:" + hashlib.sha256((origin(url) + ":" + self.email).encode()).hexdigest()

    def shared(self):
        key = "application_credentials:" + hashlib.sha256(self.email.encode()).hexdigest()
        configured = self.service.config.application_password
        with self.service.db.exclusive() as s:
            row = s.get(Setting, key)
            if row:
                value = self.service.mail.vault.open(row.value["credentials"])
                if configured and value.get("password") != configured:
                    # APPLICATION_PASSWORD/ACCOUNT_PASSWORD changed: new accounts and resets use it.
                    # Each employer keeps its own saved password until its reset succeeds.
                    value = {"email": self.email, "password": configured}
                    row.value = {**row.value, "credentials": self.service.mail.vault.seal(value)}
                return value
            password = configured or ("Ap9!" + secrets.token_urlsafe(18))
            value = {"email": self.email, "password": password}
            s.add(Setting(key=key, value={"credentials": self.service.mail.vault.seal(value)}))
            return value

    def get(self, url):
        row = self.service.db.get_setting(self.key(url), {})
        return self.service.mail.vault.open(row["credentials"]) if row else None

    def save(self, url, credentials, state, **extra):
        self.service.db.set_setting(
            self.key(url),
            {
                **self.service.db.get_setting(self.key(url), {}),
                "origin": origin(url),
                "credentials": self.service.mail.vault.seal(credentials),
                "state": state,
                "updated_at": now(),
                **extra,
            },
        )


class AccountFlow:
    """Discover ordinary account forms, including on previously unknown ATS sites."""

    def __init__(self, engine):
        self.e = engine
        self.store = AccountStore(engine.service, engine.form.resolver.profile.email)
        self.secret_fields = []
        self.attempts = set()

    async def clear(self):
        for el in self.secret_fields:
            try:
                await el.fill("", timeout=300)
            except Exception:
                pass
        self.secret_fields = []

    async def handle(self):
        e = self.e
        if not e.form.resolver.profile.allow_account_creation:
            return False
        obs = await e.form.scan()
        passwords = [
            f
            for f in obs["fields"]
            if f["type"] == "password" and not re.search(r"code|otp|one.time", f["label"], re.I)
        ]
        if not passwords:
            return False
        frame = e.form.frames[passwords[0]["id"]]
        if any(e.form.frames[f["id"]] != frame for f in passwords):
            raise ValueError("Account password fields span different frames")
        account_origin = origin(frame.url)
        creating = len(passwords) > 1 or any(f.get("autocomplete") == "new-password" for f in passwords)
        saved = self.store.get(account_origin)
        if not creating and not saved:
            links = [
                b
                for b in obs["controls"]
                if re.fullmatch(
                    r"create (?:an? )?(?:account|profile)|register|sign up|new user", b["label"], re.I
                )
            ]
            if len(links) == 1:
                await e.form.click(links[0]["id"], auth_control=True)
                e.page = e.form.page
                return True
        signature = (account_origin, creating)
        if signature in self.attempts:
            raise ValueError("The employer did not accept the saved account credentials")
        creds = self.store.shared() if creating or not saved else saved
        # Fill profile and consent fields through the ordinary evidence resolver.
        report = await e.form.fill_current()
        unresolved = [
            q
            for q in report["pending"]
            if q.get("key") != "session" and not re.fullmatch(r"user ?name|login", q["question"], re.I)
        ]
        if unresolved:
            e.result = {
                "state": "needs_review",
                "reason": "Account setup needs a confirmed profile answer",
                "reviews": unresolved,
            }
            return True
        obs = await e.form.scan()
        for f in obs["fields"]:
            if e.form.frames[f["id"]] != frame:
                continue
            value = None
            if f["type"] == "password":
                value = creds["password"]
            elif f["type"] == "email" or re.fullmatch(
                r"e.?mail(?: address)?|user ?name|login", f["label"], re.I
            ):
                value = creds["email"]
            if value is not None:
                el = await e.form.locator(f["id"]).element_handle()
                if f["type"] == "password":
                    self.secret_fields.append(el)
                await el.fill(value)
                await el.evaluate("e=>e.blur()")
        labels = (
            r"create (?:an? )?(?:account|profile)|register|sign up|continue|save and continue|submit profile"
            if creating
            else r"sign in|log in|login|continue"
        )
        buttons = [
            b
            for b in obs["controls"]
            if e.form.frames[b["id"]] == frame and re.fullmatch(labels, b["label"], re.I)
        ]
        if len(buttons) != 1:
            raise ValueError("Account action is ambiguous; no credentials were submitted")
        self.store.save(account_origin, creds, "created_locally" if creating else "signing_in")
        self.attempts.add(signature)
        await e.email_verification.prepare_submission()
        e.emit("account", "Creating an employer account" if creating else "Signing in with the saved account")
        await e.form.click(buttons[0]["id"], auth_control=True)
        e.page = e.form.page
        await e.page.wait_for_timeout(700)
        if challenge := await e.browser_challenge():
            e.result = challenge
            return True
        after = await e.form.scan()
        if not any(f["type"] == "password" for f in after["fields"]):
            self.store.save(account_origin, creds, "authenticated")
        return True


# ----- adapter view ----------------------------------------------------------------------------
from urllib.parse import urlsplit  # noqa: E402

from .inbox import host_matches  # noqa: E402

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
    """Adapter-facing view of the encrypted store: one shared login, one record per employer origin.

    States: created_locally (form submitted), authenticated, reset_requested, password_reset, locked."""

    def __init__(self, service, profile):
        self.service, self.config, self.profile = service, service.config, profile
        self.email = (self.config.account_email or profile.email).strip().lower()
        self.store = AccountStore(service, self.email) if self.email else None

    @property
    def ready(self):
        return bool(self.store)

    @property
    def may_create(self):
        return bool(self.profile.allow_account_creation)

    def saved(self, url):
        return self.store.get(url) if self.store else None

    def record(self, url):
        return self.service.db.get_setting(self.store.key(url), {}) if self.store else {}

    def credentials(self, url):
        """The employer's saved login, else the shared one (same email + password everywhere)."""
        return self.saved(url) or self.store.shared()

    def mark(self, url, state, credentials=None, **extra):
        self.store.save(url, credentials or self.credentials(url), state, **extra)

    def password_allowed(self, ats, frame_url, job_url):
        """Type the shared password only on the employer's site or its ATS's own auth hosts."""
        frame = (urlsplit(frame_url).hostname or "").lower()
        job = (urlsplit(job_url).hostname or "").lower()
        if urlsplit(frame_url).scheme != "https" and not self.config.allow_private_urls:
            return False
        if frame == job or registrable(frame) == registrable(job):
            return True
        return host_matches(frame, AUTH_HOSTS.get(ats, []))


def summary(service):
    """Employer account records for the dashboard; never credentials."""
    from sqlalchemy import select

    with service.db.session() as s:
        rows = [
            {"id": r.key, "origin": r.value.get("origin", ""), "state": r.value.get("state", ""),
             "updated_at": r.value.get("updated_at", ""), "reset_requested_at": r.value.get("reset_requested_at")}
            for r in s.scalars(select(Setting).where(Setting.key.like("employer_account:%")))
        ]
    return {"accounts": sorted(rows, key=lambda r: r["origin"])}
