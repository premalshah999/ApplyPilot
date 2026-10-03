"""Deterministic application driver: observe -> classify step -> act -> verify, with progress limits.

The model is used only to answer questions the profile/knowledge base cannot. Navigation, accounts,
email verification and submission are code paths with explicit success checks."""

import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from enum import StrEnum
from urllib.parse import urlsplit

from ..accounts import Accounts
from ..forms import AUTH, COOKIE_JS, FINAL

APPLY = re.compile(
    r"^(?:apply|apply now|apply here|apply for (?:this )?(?:job|position|role|opportunity)|apply online|"
    r"apply for this job online|start (?:your |my )?application|apply to (?:this )?(?:job|position)|"
    r"i'?m interested|begin (?:your )?application|easy apply|apply on company (?:site|website)|apply today)$",
    re.I,
)
NOT_APPLY = re.compile(r"linkedin|indeed|seek\b|glassdoor|google|facebook|refer|share|save job|alert", re.I)
NEXT = re.compile(
    r"^(?:next|continue|save (?:and|&) continue|save (?:and|&) next|next step|proceed|continue to (?:next step|"
    r"application|apply)|review(?: (?:my )?application)?|go to review|next page|start|get started|continue application)"
    r"(?:\s*[>›→»])?$",
    re.I,
)
CONFIRM_TEXT = re.compile(
    r"thank you for (?:applying|your (?:job )?application|your interest|submitting)|"
    r"application (?:has been |was )?(?:successfully )?(?:submitted|received|complete)|"
    r"we(?:'ve| have) received your application|successfully (?:applied|submitted)|"
    r"your application (?:is|has been) (?:complete|submitted|on its way|received)|"
    r"you(?:'ve| have) (?:successfully )?applied|application submitted|"
    r"congratulations.{0,60}(?:submitted|applied)",
    re.I,
)
ALREADY = re.compile(
    r"(?:you(?:'ve| have) )?already applied|previously applied|already submitted an application|"
    r"you have an application in progress for this|application for this (?:job|position) already",
    re.I,
)
CLOSED = re.compile(
    r"no longer (?:accepting (?:applications)?|available|active|open)|position has been filled|"
    r"job (?:posting )?(?:has been )?(?:closed|expired|removed)|this (?:job|position|requisition) (?:is )?"
    r"(?:closed|unavailable|no longer)|job not found|the page you are looking for (?:does not|doesn't) exist",
    re.I,
)
CHECK_EMAIL = re.compile(
    r"check your (?:email|inbox)|(?:verification|confirmation|activation) (?:email|link) (?:has been |was )?sent|"
    r"we(?:'ve| have)? (?:just )?sent (?:you )?(?:an? )?(?:email|link|verification email)|verify your (?:email|account)|"
    r"activate your account|confirm your email|account (?:needs to be|must be) verified",
    re.I,
)
CODE_TEXT = re.compile(
    r"verification code|security code|one.time (?:code|passcode|password|pin)|enter (?:the |your )?(?:\d.digit )?"
    r"(?:code|pin)|confirm your identity|we(?:'ve)? sent (?:a |you a )?(?:\d.digit )?(?:code|pin)|passcode",
    re.I,
)
CODE_LABEL = re.compile(
    r"^(?:(?:enter|type)(?: the| your)? )?(?:(?:email|verification|security|authentication|confirmation|one.time|"
    r"access)(?: verification)? )?(?:code|passcode|otp|pin(?: code)?)(?: \d+)?[: ]*$|^digit \d+$|^pin code \d$",
    re.I,
)
SMS = re.compile(
    r"(?:sent|texted).{0,60}(?:your (?:phone|mobile)|sms|text message)|(?:sms|text message) (?:code|verification)|"
    r"authenticator app|passkey|security key",
    re.I,
)
CREATE_LINK = re.compile(
    r"^(?:create (?:an? )?(?:account|profile)|sign up|register|new user|join(?: now)?|new candidate|"
    r"don'?t have an account\??.{0,20}|create account)$",
    re.I,
)
SIGNIN_LINK = re.compile(
    r"^(?:sign in|log ?in|already have an account\??.{0,20}|returning (?:user|candidate)|existing (?:user|candidate))$",
    re.I,
)
FORGOT = re.compile(
    r"forgot (?:your )?password|reset (?:your )?password|can'?t sign in|trouble signing in", re.I
)
AUTH_BUTTON = re.compile(
    r"^(?:sign in|log ?in|create (?:an? )?account|register|sign up|submit|continue|next|verify|confirm|"
    r"send|reset(?: password)?|save|create profile|set password|change password)$",
    re.I,
)
BAD_CREDENTIALS = re.compile(
    r"invalid (?:user ?name|e-?mail|credentials|login|password|sign.?in)|incorrect (?:e-?mail|password|user|credentials)|"
    r"wrong (?:e-?mail|password)|(?:e-?mail|password|credentials|user ?name) (?:is |are |was )?(?:incorrect|invalid|not valid)|"
    r"does(?: not|n'?t) match|unable to sign in|sign.?in failed|no account|account (?:was )?not found|not registered|"
    r"couldn'?t find (?:an |your )?account",
    re.I,
)
EXISTS = re.compile(
    r"already (?:exists|registered|in use|associated|taken|have an account)|account (?:already )?exists|"
    r"account with this e-?mail|e-?mail (?:address )?(?:is )?already|user ?name (?:is )?(?:already|taken|not available)",
    re.I,
)
LOCKED = re.compile(
    r"account (?:is |has been )?locked|too many (?:failed )?(?:attempts|tries)|temporarily (?:locked|disabled)",
    re.I,
)
PASSWORD_RULES = re.compile(
    r"password (?:must|should|needs to|requirements)|at least \d+ characters|must contain|not meet.{0,40}requirements",
    re.I,
)
NOT_APPLICATION = re.compile(
    r"search|keyword|filter|sort by|language|cookie|subscribe|job alert|newsletter", re.I
)


# Account states meaning "an account exists here": sign in rather than create.
KNOWN_ACCOUNT = {
    "created_locally", "signing_in", "authenticated", "verification_pending", "reset_requested",
    "password_reset", "exists", "locked",
}


class Step(StrEnum):
    JOB = "job_details"
    METHOD = "apply_method"
    EMAIL_ENTRY = "email_entry"
    SIGN_IN = "sign_in"
    CREATE_ACCOUNT = "create_account"
    VERIFY_LINK = "verify_link"
    EMAIL_CODE = "email_code"
    RESET_PASSWORD = "reset_password"
    CONSENT = "consent"
    FORM = "form"
    REVIEW = "review"
    CONFIRMATION = "confirmation"
    ALREADY_APPLIED = "already_applied"
    CLOSED = "closed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Adapter:
    id = "custom"
    name = "Career site"
    max_steps = 45
    # Labels that mean "submit the whole application" on this ATS.
    final = FINAL
    next_ids: tuple[str, ...] = ()
    apply_ids: tuple[str, ...] = ()

    def __init__(self, engine, ats_id=None, name=None):
        if ats_id:
            self.id = ats_id
        if name:
            self.name = name
        self.e = engine
        self.service, self.config = engine.service, engine.config
        self.job, self.run_record = engine.job, engine.run_record
        self.accounts = Accounts(self.service, engine.profile)
        self.inbox = self.service.inbox
        self.signatures = Counter()
        self.attempts = Counter()
        self.step_no = 0
        self.challenge_since = time.time() - 90
        self.challenge = None  # The open mail request window (MailService challenge).
        self.committed = False
        self.before_commit = None
        self.post_commit_steps = 0
        self.apply_url = None
        self.last_step = None

    # ----- plumbing -----------------------------------------------------------------------------
    @property
    def form(self):
        return self.e.form

    @property
    def page(self):
        return self.e.form.page

    def emit(self, kind, message, data=None):
        self.e.emit(kind, message, data)

    def defer(self, reason, reviews=None, state="needs_review"):
        return {"state": state, "reason": reason[:1000], "reviews": reviews or []}

    @staticmethod
    def prepare_url(url):
        return url

    async def dismiss_overlays(self):
        for frame in self.page.frames[:3]:
            try:
                if await frame.evaluate(COOKIE_JS):
                    await asyncio.sleep(0.2)
            except Exception:
                continue

    def handoff(self, url):
        """Career sites hand the application to the real ATS (Phenom -> Workday, site -> Taleo)."""
        from ..ats import detect
        from . import REGISTRY

        current = detect(url).id
        if current != self.id and current in REGISTRY and not self.committed:
            return current
        return None

    def signature(self, step, obs):
        parts = [
            step.value,
            urlsplit(obs["url"]).path,
            "|".join(obs.get("headings", [])[:4]),
            "|".join(sorted(f["label"] for f in obs["fields"])[:40]),
            "|".join(sorted(set(obs.get("errors", [])))[:5]),
        ]
        return hashlib.sha1(json.dumps(parts).encode()).hexdigest()

    def app_fields(self, obs):
        return [
            f
            for f in obs["fields"]
            if f["type"] == "file"
            or (not NOT_APPLICATION.search(f["label"]) and f["label"] != "Unlabelled field")
        ]

    def buttons(self, obs, pattern, ids=()):
        hits = [
            b
            for b in obs["controls"]
            if (b["automation_id"] and b["automation_id"] in ids)
            or pattern.search(b["label"])
            or (b["aria_label"] and pattern.search(b["aria_label"]))
        ]
        return hits

    def find_next(self, obs):
        hits = [
            b
            for b in self.buttons(obs, NEXT, self.next_ids)
            if not self.final.search(b["label"])
            and not re.search(r"^back|previous|cancel|save (?:as )?draft", b["label"], re.I)
        ]
        if not hits:
            return None
        rank = ["save and continue", "save & continue", "continue", "next", "review"]

        def score(b):
            label = b["label"].lower()
            return (
                0 if b["automation_id"] in self.next_ids else 1,
                next((i for i, r in enumerate(rank) if label.startswith(r)), len(rank)),
            )

        best = sorted(hits, key=score)
        top = [b for b in best if score(b) == score(best[0])]
        return top[-1]  # bottom navigation when duplicated top/bottom

    def find_final(self, obs):
        hits = [
            b
            for b in obs["controls"]
            if self.final.search(b["label"])
            and not re.search(r"code|search|feedback|question|draft|resume only|cancel", b["label"], re.I)
        ]
        labels = {b["label"].strip().lower() for b in hits}
        if not hits:
            return None
        if len(labels) == 1:
            return hits[-1]
        return None

    def find_apply(self, obs):
        hits = [
            b
            for b in obs["controls"]
            if (
                b["automation_id"] in self.apply_ids
                or APPLY.search(b["label"])
                or APPLY.search(b["aria_label"])
            )
            and not NOT_APPLY.search(b["label"] + " " + b["href"])
        ]
        if not hits:
            return None
        preferred = [b for b in hits if b["automation_id"] in self.apply_ids]
        return (preferred or hits)[0]

    def code_fields(self, obs):
        codes = [
            f
            for f in obs["fields"]
            if f["type"] in {"text", "tel", "number", "password"}
            and (CODE_LABEL.search(f["label"]) or f.get("autocomplete") == "one-time-code")
        ]
        if not codes and CODE_TEXT.search(obs["text"]):
            digits = [
                f for f in obs["fields"] if f["maxlength"] == 1 and f["type"] in {"text", "tel", "number"}
            ]
            if 4 <= len(digits) <= 10:
                codes = digits
        return codes

    # ----- classification -----------------------------------------------------------------------
    def classify(self, obs):
        dialog = self.dialog_view(obs)
        if dialog is not None:
            # An open dialog (reset password, sign in, terms) is the step, not the page behind it.
            return self.classify_page(dialog)
        return self.classify_page(obs)

    @staticmethod
    def dialog_view(obs):
        """The observation restricted to the top-most open modal dialog, if it holds controls."""
        dialogs = [d for d in obs.get("dialogs", []) if d.get("field_ids") or d.get("control_ids")]
        if not dialogs:
            return None
        top = dialogs[-1]
        fields = [f for f in obs["fields"] if f["id"] in top["field_ids"]]
        controls = [c for c in obs["controls"] if c["id"] in top["control_ids"]]
        if not fields and not [c for c in controls if not re.fullmatch(r"close|x|×|cancel", c["label"], re.I)]:
            return None
        return {
            **obs,
            "fields": fields,
            "controls": controls,
            "text": top.get("text", ""),
            "headings": [top["title"]] if top.get("title") else [],
            "automation": top.get("automation", []),
            "dialog": True,
        }

    def classify_page(self, obs):
        text = obs["text"]
        fields = self.app_fields(obs)
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        meaningful = [f for f in fields if f["type"] != "checkbox"]
        # A job page saying "thank you for your interest" still has an Apply button.
        if (
            CONFIRM_TEXT.search(text)
            and len(meaningful) <= 1
            and not passwords
            and (self.committed or not self.find_apply(obs))
        ):
            return Step.CONFIRMATION
        if ALREADY.search(text) and not passwords and len(meaningful) <= 1:
            return Step.ALREADY_APPLIED
        codes = self.code_fields(obs)
        if codes:
            return Step.BLOCKED if SMS.search(text) else Step.EMAIL_CODE
        if (
            not passwords
            and FORGOT.search(text + " " + " ".join(obs.get("headings", [])))
            and [f for f in meaningful if f["type"] == "email" or re.search(r"e-?mail|user ?name", f["label"], re.I)]
            and self.auth_button(obs, "reset")
        ):
            return Step.RESET_PASSWORD
        if passwords:
            # A sign-up form asks for the password twice.
            return Step.CREATE_ACCOUNT if len(passwords) >= 2 else Step.SIGN_IN
        if CHECK_EMAIL.search(text) and len(meaningful) <= 1 and not self.find_apply(obs):
            return Step.VERIFY_LINK
        if not meaningful and CLOSED.search(text) and not self.find_apply(obs):
            return Step.CLOSED
        emails = [f for f in meaningful if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I)]
        if (
            emails
            and len(meaningful) == len(emails)
            and (self.find_next(obs) or self.buttons(obs, AUTH_BUTTON))
        ):
            return Step.EMAIL_ENTRY
        if meaningful or any(f["type"] == "file" for f in fields):
            return Step.FORM
        if self.find_final(obs) and not self.find_apply(obs):
            return Step.REVIEW
        if self.find_apply(obs):
            return Step.JOB
        if [
            b
            for b in obs["controls"]
            if re.search(r"^(?:i )?(?:accept|agree)(?: and continue)?$", b["label"], re.I)
        ]:
            return Step.CONSENT
        if self.find_next(obs):
            return Step.FORM
        return Step.UNKNOWN

    # ----- main loop ----------------------------------------------------------------------------
    async def drive(self):
        try:
            return await self._drive()
        finally:
            self.release_mail()

    async def _drive(self):
        for _ in range(self.max_steps):
            await self.form.settle(timeout=10)
            await self.dismiss_overlays()
            obs = await self.form.scan()
            moved = self.handoff(obs["url"])
            if moved:
                return {"handoff": moved}
            # Modal precedence: an open dialog is the current step; act only on its controls.
            obs = self.dialog_view(obs) or obs
            step = self.classify_page(obs)
            if self.committed and step not in {Step.CONFIRMATION, Step.ALREADY_APPLIED}:
                self.post_commit_steps += 1
                if self.post_commit_steps > 4:
                    return {
                        "state": "submission_unknown",
                        "reason": "Submitted, but no confirmation page appeared",
                    }
            sig = self.signature(step, obs)
            self.signatures[sig] += 1
            self.step_no += 1
            await self.e.trace(self.step_no, step.value, obs)
            if self.signatures[sig] > 3:
                return self.defer(
                    f"No progress on the {step.value.replace('_', ' ')} step: " + "; ".join(obs["errors"][:3])
                )
            if step != self.last_step:
                self.emit(
                    "step", f"{self.name}: {step.value.replace('_', ' ')}", {"url": self.e.redact(obs["url"])}
                )
            self.last_step = step
            result = await getattr(self, "on_" + step.value)(obs)
            if isinstance(result, dict):
                return result
        return self.defer("The application has more steps than the configured limit")

    # ----- step handlers ------------------------------------------------------------------------
    async def on_job_details(self, obs):
        button = self.find_apply(obs)
        if not button:
            return {"fallback": True, "reason": "No apply control found"}
        self.attempts["apply"] += 1
        if self.attempts["apply"] > 3:
            return self.defer("The apply button did not open an application")
        await self.click(button)
        self.apply_url = self.page.url

    async def on_apply_method(self, obs):
        return {"fallback": True, "reason": "Unknown application method chooser"}

    async def on_consent(self, obs):
        buttons = [b for b in obs["controls"] if re.search(r"^(?:i )?(?:accept|agree)", b["label"], re.I)]
        for f in obs["fields"]:
            if f["type"] == "checkbox" and f["value"] != "true":
                await self.form.fill(f, "true")
        if not buttons:
            return {"fallback": True}
        await self.click(buttons[-1])

    async def on_email_entry(self, obs):
        if not self.accounts.email:
            return self.defer("Set ACCOUNT_EMAIL (or your profile email) so I can identify you to employers")
        self.attempts["email_entry"] += 1
        if self.attempts["email_entry"] > 2:
            return self.defer("The employer did not accept the email address step")
        for f in obs["fields"]:
            if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I):
                await self.form.fill(f, self.accounts.email)
            elif f["type"] == "checkbox" and f["value"] != "true":
                answer = (await self.form.resolver.resolve([f]))[0]
                if answer.disposition == "answer" and answer.value == "true":
                    await self.form.fill(f, "true")
                elif f["required"]:
                    return self.defer(
                        "A required agreement on the email step needs your approval", [self.review_for(f)]
                    )
        await self.arm_mail()
        target = self.find_next(obs) or next(iter(self.buttons(obs, AUTH_BUTTON)), None)
        if not target:
            return {"fallback": True}
        await self.click(target, auth=True)
        await self.after_auth_dialogs()

    async def after_auth_dialogs(self):
        """Terms/privacy modals that must be accepted before continuing (Oracle, iCIMS)."""
        await self.form.settle(timeout=4)
        obs = await self.form.scan()
        agree = [
            b
            for b in obs["controls"]
            if re.fullmatch(r"(?:i )?(?:agree|accept)(?: and continue)?", b["label"], re.I)
        ]
        if agree and len(self.app_fields(obs)) <= 2:
            await self.click(agree[-1], auth=True)

    async def on_sign_in(self, obs):
        return await self.authenticate(obs, "sign_in")

    async def on_create_account(self, obs):
        return await self.authenticate(obs, "create")

    async def on_verify_link(self, obs):
        if not self.inbox.configured:
            return self.defer(
                "This employer emailed a verification link. Connect Gmail (GMAIL_APP_PASSWORD)."
            )
        self.attempts["verify_link"] += 1
        if self.attempts["verify_link"] > 2:
            return self.defer("The employer kept asking for email verification")
        token = await self.wait_email("link")
        if not token:
            return self.defer(self.mail_problem("verification link"))
        if token["kind"] != "link":
            self.finish_mail("failed", "Expected a verification link, received a code")
            return self.defer("The employer sent a code where the page expected a link")
        await self.open_link(token["value"])
        self.finish_mail("verified", "Website accepted the verification link")
        self.accounts.mark(self.page.url, "authenticated")
        self.emit("email_verified", "Employer email verification completed")
        await self.form.settle(timeout=8)
        obs = await self.form.scan()
        if self.classify(obs) in {Step.CONFIRMATION, Step.UNKNOWN, Step.VERIFY_LINK} or not obs["fields"]:
            await self.return_to_application()

    async def on_email_code(self, obs):
        if not self.inbox.configured:
            return self.defer("This employer emailed a one-time code. Connect Gmail (GMAIL_APP_PASSWORD).")
        self.attempts["email_code"] += 1
        if self.attempts["email_code"] > 2:
            return self.defer("The verification code was not accepted")
        token = await self.wait_email("code")
        if not token:
            resend = [
                b
                for b in obs["controls"]
                if re.search(r"resend|send (?:a )?new code|send again", b["label"], re.I)
            ]
            if resend and self.attempts["email_code"] == 1:
                self.release_mail()
                await self.arm_mail("code")
                await self.click(resend[0], auth=True)
                token = await self.wait_email("code")
            if not token:
                return self.defer(self.mail_problem("verification code"))
        if token["kind"] != "code":
            await self.open_link(token["value"])
            self.finish_mail("verified", "Website accepted the verification link")
            return None
        obs = await self.form.scan()
        fields = self.code_fields(obs)
        code = token["value"]
        if not fields:
            self.finish_mail("failed", "Code field disappeared")
            return self.defer("A code arrived but the page no longer shows a code field")
        if len(fields) > 1 and len(fields) != len(code):
            self.finish_mail("failed", "Code length mismatch")
            return self.defer("The code length does not match the website")
        for i, f in enumerate(fields):
            frame_url = self.form.frames[f["id"]].url
            if not self.accounts.password_allowed(self.id, frame_url, self.job["url"]):
                self.finish_mail("failed", "Code field on an unexpected website")
                return self.defer("The code field is on an unexpected website; not entering the code")
            await self.form.locator(f["id"]).fill(code if len(fields) == 1 else code[i], timeout=4000)
        await asyncio.sleep(0.3)
        obs = await self.form.scan()
        if self.code_fields(obs):
            confirm = [
                b
                for b in obs["controls"]
                if re.search(r"^(?:verify|confirm|continue|next|submit|sign in|log in)", b["label"], re.I)
            ]
            if confirm:
                await self.click(confirm[-1], auth=True)
        await self.form.settle(timeout=6)
        after = await self.form.scan()
        if self.code_fields(after) and self.signature(Step.EMAIL_CODE, after) == self.signature(Step.EMAIL_CODE, obs):
            self.finish_mail("failed", "Website did not accept the code")
            return None  # The loop sees the same step again and retries or defers.
        self.finish_mail("verified", "Website accepted the verification code")
        self.emit("email_verified", "Entered the emailed verification code")

    async def on_reset_password(self, obs):
        """A reset dialog/page that asks for the account email (e.g. after Forgot password)."""
        return await self.request_reset(obs, "The password reset was requested from this page")

    async def on_form(self, obs):
        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            return report
        obs = await self.form.scan()
        nxt = self.find_next(obs)
        final = self.find_final(obs)
        if not nxt and final:
            return await self.on_review(obs, report)
        if not nxt:
            return {"fallback": True, "reason": "No way forward from this page"}
        if challenge := await self.solve_captcha():
            return challenge
        before = self.signature(Step.FORM, obs)
        await self.click(nxt, step=True)
        after = await self.form.scan()
        if self.signature(self.classify(after), after) == before or after["errors"]:
            # Server-side validation: answer what it flagged, then try once more.
            if after["errors"] or any(f["error"] for f in after["fields"]):
                repaired = await self.repair(after)
                if isinstance(repaired, dict):
                    return repaired

    async def on_review(self, obs, report=None):
        if report is None:
            report = await self.form.verify()
        problems = [
            p
            for p in report["problems"]
            if not p.startswith("Resume attachment") or self.form.resume_fields()
        ]
        if problems:
            filled = await self.fill_page()
            if isinstance(filled, dict) and filled.get("state"):
                return filled
            report = await self.form.verify()
            problems = [p for p in report["problems"] if not p.startswith("Resume attachment")]
            if problems:
                return self.defer("; ".join(problems[:6]), report.get("pending"))
        if self.form.resume_fields() and not self.form.upload_verified:
            return self.defer("Resume attachment was not accepted")
        if self.run_record["mode"] == "dry_run":
            return {
                "state": "dry_run_passed",
                "reason": f"Reached the final {self.name} submit step with verified answers; nothing submitted",
                "report": report,
            }
        obs = await self.form.scan()
        final = self.find_final(obs)
        if not final:
            return self.defer("Final submit control was not unique")
        return await self.commit(final)

    async def on_confirmation(self, obs):
        if self.committed:
            receipt = await self.form.proof()
            if receipt and receipt != self.before_commit:
                receipt["resume_sha256"] = self.run_record["packet"]["resume_sha"]
                return {"state": "confirmed", "reason": "Website confirmation captured", "receipt": receipt}
            return None  # Wait for a confirmation that this submission produced.
        # A confirmation-looking page this run did not produce is never recorded as an application.
        return self.defer(
            "The employer shows a confirmation, but this run did not submit; check whether you already applied",
            [
                {
                    "question": "Confirm whether you already applied to this job",
                    "options": [],
                    "key": "manual",
                    "reason": "Confirmation without a submission",
                }
            ],
        )

    async def on_already_applied(self, obs):
        if self.committed:
            return await self.on_confirmation(obs)
        return {"state": "already_applied", "reason": "The employer says you already applied to this job"}

    async def on_closed(self, obs):
        return {"state": "closed", "reason": "The posting is closed or no longer available"}

    async def on_blocked(self, obs):
        return self.defer(
            "This employer requires SMS/passkey verification. Run `jobpilot login URL` once to save a session.",
            [
                {
                    "question": "Sign in to this employer and import the browser session",
                    "options": [],
                    "key": "session",
                    "reason": "SMS or passkey verification",
                }
            ],
        )

    async def on_unknown(self, obs):
        return {"fallback": True, "reason": "Unrecognized page"}

    # ----- actions ------------------------------------------------------------------------------
    async def click(self, button, auth=False, step=True):
        """Adapters click controls they classified themselves; the final submit goes through commit()."""
        if (
            self.final.search(button["label"])
            and not auth
            and not self.committed
            and self.last_step
            not in {
                Step.EMAIL_CODE,
                Step.CONSENT,
                Step.JOB,
                Step.METHOD,
            }
        ):
            raise ValueError("Refusing to click a final submit control outside the commit step")
        await self.dismiss_overlays()
        before = set(self.page.context.pages)
        result = await self.form.click(button["id"], auth_control=auth, step_control=step or auth)
        if set(self.page.context.pages) - before:
            self.emit("new_tab", "The application opened in a new tab")
        return result

    async def fill_page(self):
        report = await self.form.fill_current(accept_prefilled=True)
        if report["pending"]:
            asked = await self.e.ask(report["pending"])
            if asked:
                report = await self.form.fill_current(accept_prefilled=True)
            if report["pending"]:
                return self.defer("Required answers need your review", report["pending"])
        if not report["ok"]:
            # One more pass catches fields revealed or re-rendered by the previous answers.
            report = await self.form.fill_current(accept_prefilled=True)
            if report["pending"]:
                return self.defer("Required answers need your review", report["pending"])
            fatal = [p for p in report["problems"] if not p.startswith("Resume attachment")]
            if fatal:
                return self.defer("; ".join(fatal[:6]))
        return report

    async def repair(self, obs):
        self.attempts["repair:" + urlsplit(obs["url"]).path] += 1
        if self.attempts["repair:" + urlsplit(obs["url"]).path] > 2:
            return self.defer("The employer rejected the page: " + "; ".join(obs["errors"][:4]))
        flagged = [f for f in obs["fields"] if f["error"] or not f["valid"]]
        for f in flagged:
            self.form.expected.pop(f["id"], None)
        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            return report
        obs = await self.form.scan()
        nxt = self.find_next(obs)
        if nxt:
            await self.click(nxt, step=True)
        return None

    async def solve_captcha(self):
        """None when the page is clear; a waiting_browser result when a check stays unsolved."""
        return await self.e.solve_captcha()

    async def commit(self, final):
        if challenge := await self.solve_captcha():
            return challenge  # Unsolved check before anything was submitted.
        self.before_commit = await self.form.proof()
        if not self.committed:
            self.service.reserve_submission(self.e.run_id)
        self.e.armed = True
        self.committed = True
        for frame in self.page.frames:
            try:
                await frame.evaluate("window.__jpArmed=true")
            except Exception:
                pass
        self.emit("submitting", "Commit step started; automatic retries are disabled")
        await self.form.click(final["id"], step_control=True)
        for _ in range(40):
            await asyncio.sleep(0.5)
            receipt = await self.form.proof()
            if receipt and receipt != self.before_commit:
                receipt["resume_sha256"] = self.run_record["packet"]["resume_sha"]
                return {"state": "confirmed", "reason": "Website confirmation captured", "receipt": receipt}
            if rejection := await self.form.rejection():
                return {
                    "state": "failed",
                    "reason": "Employer website explicitly rejected the submission",
                    "receipt": rejection,
                }
            if await self.solve_captcha():
                return {
                    "state": "submission_unknown",
                    "reason": "Submission attempted but a security check was not accepted; "
                    "automatic resubmission is disabled",
                }
            obs = await self.form.scan()
            step = self.classify(obs)
            if step == Step.EMAIL_CODE:
                return None  # Post-submit verification (e.g. Greenhouse security code).
            if step in {Step.FORM, Step.REVIEW} and self.app_fields(obs) and not obs["errors"]:
                # Some ATSs show optional pages after the commit (survey, EEO). Continue the loop.
                return None
            if obs["errors"] and step in {Step.FORM, Step.REVIEW}:
                break
        # An authenticated acknowledgement email can still confirm it later (receipts.py).
        return {
            "state": "submission_unknown",
            "reason": "Submission attempted, but no explicit receipt was captured",
        }

    async def open_link(self, url):
        # Navigations during the link visit may only reach the employer rule's origins.
        rule = (self.challenge or {}).get("rule") or {}
        self.e.verification_origins = set(rule.get("link_origins", [])) or None
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        finally:
            self.e.verification_origins = None
        await self.form.settle(timeout=8)
        # Remove tokens from the address bar before anything else observes the URL.
        try:
            await self.page.evaluate(
                "() => {const u=new URL(location.href);for(const k of [...u.searchParams.keys()])"
                "if(/token|code|secret|ticket|key/i.test(k))u.searchParams.delete(k);history.replaceState(null,'',u);}"
            )
        except Exception:
            pass

    async def return_to_application(self):
        target = self.apply_url or self.job["url"]
        await self.page.goto(target, wait_until="domcontentloaded", timeout=25000)

    # ----- email ------------------------------------------------------------------------------
    async def arm_mail(self, kind="auto"):
        """Open the request window BEFORE the action that makes the employer send mail.

        MailService serializes runs whose employers share a sender domain (the lease), so one
        run can never consume another's code."""
        self.challenge_since = time.time() - 5
        if self.inbox.live(self.challenge) or not self.inbox.configured:
            return
        try:
            self.challenge = await self.inbox.arm(
                self.e.run_id, self.job, self.accounts.email, kind, since=self.challenge_since
            )
        except ValueError as exc:
            self.challenge = None
            self.emit("mail_wait", str(exc)[:200])

    def release_mail(self):
        if self.inbox.live(self.challenge):
            self.inbox.finish(self.challenge, "cancelled", "No email was needed")
        self.challenge = None

    def finish_mail(self, state, reason):
        if self.challenge:
            self.inbox.finish(self.challenge, state, reason)
        self.challenge = None

    def mail_problem(self, what):
        reason = self.inbox.reason(self.challenge) if self.challenge else ""
        if self.challenge is None and not self.mail_rule_known:
            return f"No trusted sender is known for this employer's {what}; add a rule in Settings › Email"
        return f"The {what} email did not arrive or was not unique" + (f" ({reason})" if reason else "")

    @property
    def mail_rule_known(self):
        return bool(self.service.mail.rule_for(self.job["url"]))

    async def wait_email(self, kind):
        if not self.inbox.live(self.challenge):
            # The site may have sent the mail during navigation; the window starts before that.
            self.challenge = None
            try:
                self.challenge = await self.inbox.arm(
                    self.e.run_id, self.job, self.accounts.email, kind, since=self.challenge_since
                )
            except ValueError as exc:
                self.emit("mail_wait", str(exc)[:200])
                return None
        if not self.challenge:
            return None
        self.service.mail.narrow(self.challenge["id"], kind)
        self.emit("email_wait", f"Waiting for the employer's verification {kind}")
        self.e.extend(min(self.config.mail_wait_seconds, 120))
        token = await self.inbox.wait(self.challenge)
        if token:
            self.emit("email_matched", "Matched a trusted verification email", {"kind": token["kind"]})
        return token

    # ----- accounts ---------------------------------------------------------------------------
    def review_for(self, field, reason="Needs your approval"):
        from ..answers import answer_key

        return {
            "question": field["label"],
            "options": field["options"],
            "key": answer_key(field),
            "reason": reason,
        }

    def auth_url(self, obs):
        """The page that owns the password fields (the account belongs to that origin)."""
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        # The observation may be older than the page (a failed sign-in reloads it).
        return (passwords[0].get("frame_url") if passwords else None) or self.page.url

    async def authenticate(self, obs, mode):
        if not self.accounts.ready:
            return self.defer("Add your email to the profile so I can identify you to employers")
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        known = record.get("state") in KNOWN_ACCOUNT
        if mode == "create" and not self.accounts.may_create and not known:
            return self.defer(
                "This employer requires an account. Enable “Create and reuse employer accounts” in your profile.",
                [{"question": "Allow account creation for employers that require one", "options": [],
                  "key": "session", "reason": "Account required"}],
            )
        self.attempts[mode] += 1
        if self.attempts[mode] > 2 or self.attempts["sign_in"] + self.attempts["create"] > 4:
            return await self.recover(obs, "Signing in did not succeed")
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        for f in passwords:
            frame_url = self.form.frames[f["id"]].url
            if not self.accounts.password_allowed(self.id, frame_url, self.job["url"]):
                return self.defer("A password was requested on an unexpected website; not entering it")
        if (
            mode == "create"
            and known
            and not self.attempts["signin_switch"]
            and await self.switch(obs, SIGNIN_LINK)
        ):
            # A known account on a sign-up page: sign in instead of re-creating it.
            self.attempts["signin_switch"] += 1
            self.attempts["create"] -= 1
            return None
        credentials = self.accounts.credentials(url)
        # Identity fields on sign-up forms (name, country, consent) come from the profile.
        others = [
            f
            for f in obs["fields"]
            if f["type"] not in {"password"}
            and not self.is_login_field(f)
            and not AUTH.search(f["label"])
            and (f["required"] or f["type"] == "checkbox")
        ]
        if others:
            answers = await self.form.resolver.resolve(others)
            for a in answers:
                f = self.form.fields.get(a.field_id)
                if not f:
                    continue
                if a.disposition == "answer":
                    try:
                        await self.form.fill(f, a.value)
                    except Exception:
                        pass
                elif f["required"]:
                    return self.defer("The account form needs an answer", [self.review_for(f, a.reason)])
        for f in obs["fields"]:
            if self.is_login_field(f):
                await self.form.locator(f["id"]).fill(credentials["email"], timeout=4000)
        for f in passwords:
            await self.form.locator(f["id"]).fill(credentials["password"], timeout=4000)
        button = self.auth_button(obs, mode)
        if not button:
            return {"fallback": True, "reason": "No sign-in control found"}
        resume = any(f["type"] == "file" for f in obs["fields"])
        if self.final.search(button["label"]) and (resume or len(others) > 6):
            # An application form with an embedded "create a password" section: its Submit is the
            # application submit, so it goes through review/commit like any other final step.
            self.emit("auth", "Account fields are part of the application form")
            return await self.on_form(await self.form.scan())
        if mode == "create":
            await self.arm_mail()
            # Saved before the click: the same shared login, so a crash cannot lose it.
            self.accounts.mark(url, "created_locally", credentials)
        await self.click(button, auth=True)
        outcome, detail = await self.auth_outcome()
        self.emit("auth", f"{mode.replace('_', ' ')}: {outcome}")
        if outcome == "ok":
            self.release_mail()
            self.accounts.mark(url, "authenticated", credentials)
            return None
        if outcome == "verify":
            self.accounts.mark(url, "verification_pending", credentials)
            return None
        self.release_mail()
        if outcome == "locked":
            self.accounts.mark(url, "locked", credentials, error=detail[:200])
            return self.defer("The employer locked the account after failed sign-ins: " + detail)
        if outcome == "password_rules":
            return self.defer("The shared account password does not meet this employer's rules: " + detail)
        if outcome == "exists" and mode == "create":
            self.accounts.mark(url, "exists", credentials)
            if await self.switch(obs, SIGNIN_LINK):
                return None
            return await self.recover(obs, detail)
        if outcome == "bad_credentials" and mode == "sign_in":
            if not known and self.accounts.may_create and self.attempts["create"] == 0:
                if await self.switch(obs, CREATE_LINK):
                    return None
            return await self.recover(obs, detail)
        return None

    def is_login_field(self, f):
        return f["type"] == "email" or (
            f["type"] in {"text", "email"}
            and re.search(
                r"e-?mail|user ?name|user ?id|login(?: id)?|sign.?in name",
                f["label"] + " " + f["automation_id"],
                re.I,
            )
            and not re.search(r"first|last|name of|full name", f["label"], re.I)
        )

    def auth_button(self, obs, mode):
        prefer = {
            "sign_in": r"^(?:sign in|log ?in|continue|next|submit)$",
            "create": r"^(?:create (?:an? )?account|register|sign up|create profile|submit|continue|next)$",
            "reset": r"^(?:submit|send|send (?:a |the )?(?:reset )?(?:link|email|code|instructions)|send reset link|"
            r"reset(?: (?:my )?password)?|request (?:a )?reset|continue|set password|change password|save|"
            r"update password)$",
        }[mode]
        hits = [
            b
            for b in obs["controls"]
            if re.search(prefer, b["label"], re.I) or re.search(prefer, b["aria_label"], re.I)
        ]
        if not hits:
            hits = [b for b in obs["controls"] if AUTH_BUTTON.search(b["label"])]
        # Workday overlays the real button with a click_filter that must receive the click.
        filters = [b for b in hits if b["automation_id"] == "click_filter"]
        primary = [b for b in hits if "submit" in b["automation_id"].lower()]
        return (filters or primary or hits or [None])[0]

    async def auth_outcome(self, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self.form.settle(timeout=4)
            obs = await self.form.scan()
            text = " ".join(obs["errors"]) + "\n" + obs["text"]
            errors = " ".join(obs["errors"]) or text
            if LOCKED.search(errors):
                return "locked", LOCKED.search(errors).group()
            if EXISTS.search(errors):
                return "exists", EXISTS.search(errors).group()
            if obs["errors"] and PASSWORD_RULES.search(errors):
                return "password_rules", obs["errors"][0][:200]
            if BAD_CREDENTIALS.search(errors) and (
                obs["errors"] or any(f["type"] == "password" for f in obs["fields"])
            ):
                return "bad_credentials", BAD_CREDENTIALS.search(errors).group()
            if CHECK_EMAIL.search(text) and not any(f["type"] == "password" for f in obs["fields"]):
                return "verify", ""
            if self.code_fields(obs):
                return "verify", ""
            if not any(f["type"] == "password" for f in obs["fields"]):
                return "ok", ""
            await asyncio.sleep(0.4)
        return "unknown", "; ".join(obs["errors"][:2])

    async def switch(self, obs, pattern):
        # The page usually changed since `obs` (a failed sign-in reloads it); observe it again.
        obs = await self.form.scan()
        links = [b for b in obs["controls"] if pattern.search(b["label"]) or pattern.search(b["aria_label"])]
        if not links:
            return False
        await self.click(links[0], auth=True)
        return True

    async def recover(self, obs, detail):
        """Forgot-password through the connected mailbox, then set the shared password.

        Requested once per employer per run and at most once every five minutes; a reset whose
        email never arrived does not count against the limit. Saved credentials are kept until
        the website confirms the new password."""
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        manual = [{"question": "Sign in to this employer and import the browser session", "options": [],
                   "key": "session", "reason": detail[:200]}]
        if not self.config.account_password_reset or self.attempts["reset"]:
            return self.defer(f"Could not sign in to this employer ({detail})", manual)
        if not self.inbox.configured or not await self.inbox.rule(self.job, self.accounts.email):
            # Without a trusted sender, a reset email could never be matched: do not burn a reset.
            return self.defer(
                f"Could not sign in ({detail}). Connect Gmail and an email rule for this employer to "
                "recover the account automatically.",
                manual,
            )
        if time.time() - record.get("reset_requested_at", 0) < 300:
            return self.defer("A password reset was requested recently; retry in five minutes", manual)
        if record.get("resets", 0) >= 3:
            return self.defer("Password resets for this employer are exhausted; sign in manually once", manual)
        self.attempts["reset"] += 1
        obs = await self.form.scan()
        forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot and await self.switch(obs, SIGNIN_LINK):
            # "Forgot password" usually lives on the sign-in page, not the sign-up page.
            obs = await self.form.scan()
            forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot:
            return self.defer(f"Could not sign in ({detail}) and no password reset was offered")
        self.emit("auth", "Resetting the employer password through your mailbox")
        await self.click(forgot[0], auth=True)
        await self.form.settle(timeout=6)
        return await self.request_reset(await self.form.scan(), detail)

    async def request_reset(self, obs, detail):
        """Explicit reset-email step: fill the email, open the request window, click, then wait."""
        url = self.auth_url(obs) if any(f["type"] == "password" for f in obs["fields"]) else self.page.url
        view = self.dialog_view(obs) or obs
        emails = [f for f in view["fields"] if self.is_login_field(f)]
        if not emails:
            return self.defer("The password reset step has no email field")
        for f in emails:
            await self.form.locator(f["id"]).fill(self.accounts.email, timeout=4000)
        button = self.auth_button(view, "reset")
        if not button:
            return self.defer("The password reset step has no submit control")
        self.release_mail()
        await self.arm_mail("password_reset")
        if not self.challenge:
            return self.defer("The reset email could not be matched safely right now; retry later")
        previous = self.accounts.saved(url)
        self.accounts.mark(url, "reset_requested", previous, reset_requested_at=time.time())
        await self.click(button, auth=True)
        self.emit("auth", "Employer password reset requested")
        token = await self.wait_email("password_reset")
        if not token:
            reason = self.inbox.reason(self.challenge)
            self.finish_mail("failed", "No reset email")
            # Nothing was delivered: allow another request after the cooldown, keep the count.
            return self.defer("The password reset email did not arrive or was not unique" + (f" ({reason})" if reason else ""))
        self.emit("auth", "Employer password reset email received")
        if token["kind"] != "link":
            self.finish_mail("failed", "Reset email had no link")
            return self.defer("The reset email did not contain a usable link")
        await self.open_link(token["value"])
        obs = await self.form.scan()
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        if not passwords:
            self.finish_mail("failed", "Reset link did not open a password form")
            return self.defer("The password reset link did not open a new-password form")
        credentials = self.accounts.store.shared()
        for f in passwords:
            if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                self.finish_mail("failed", "Reset form on an unexpected website")
                return self.defer("The reset page is on an unexpected website; not entering the password")
            await self.form.locator(f["id"]).fill(credentials["password"], timeout=4000)
        button = self.auth_button(obs, "reset")
        if not button:
            self.finish_mail("failed", "No new-password submit control")
            return self.defer("The new-password form had no submit control")
        await self.click(button, auth=True)
        outcome, detail = await self.auth_outcome()
        self.emit("auth", f"reset: {outcome}")
        if outcome == "password_rules":
            self.finish_mail("failed", "Password rules")
            return self.defer("The shared account password does not meet this employer's rules: " + detail)
        if outcome != "ok":
            self.finish_mail("failed", "New password not accepted")
            return self.defer("The employer did not accept the new password: " + (detail or outcome))
        self.finish_mail("verified", "Employer accepted the password reset")
        record = self.accounts.record(url)
        self.accounts.mark(url, "password_reset", credentials, resets=record.get("resets", 0) + 1)
        self.attempts["sign_in"] = self.attempts["create"] = 0
        self.emit("auth", "Password reset to the shared account password")
        await self.form.settle(timeout=6)
        obs = await self.form.scan()
        if self.classify(obs) not in {Step.SIGN_IN, Step.FORM, Step.METHOD}:
            await self.return_to_application()
        return None
