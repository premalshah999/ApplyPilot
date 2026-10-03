"""Bounded email challenges in the owning browser; secrets never go to an LLM."""

import asyncio
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .mail import origin
from .network import public_url

CODE = re.compile(
    r"verification code|security code|one.time (?:code|password)|authentication code|\botp\b|enter (?:the |your )?code",
    re.I,
)
CODE_LABEL = re.compile(
    r"^(?:(?:enter|type)(?: the| your)? )?(?:(?:email|verification|security|authentication|confirmation|one.time)(?: verification)? )?(?:code|passcode|otp)[: ]*$",
    re.I,
)
WAITING = re.compile(
    r"check your (?:email|inbox)|(?:verification|confirmation|sign.in) (?:email|link) (?:has been |was )?sent|(?:sent|emailed) (?:you )?(?:a |an )?(?:verification |confirmation )?(?:link|code)",
    re.I,
)
SEND = re.compile(
    r"^(?:send|email|request)(?: me| a| the| my)?(?: verification| confirmation| sign.in| security)? (?:code|link|email)$",
    re.I,
)
CONFIRM = re.compile(
    r"^(?:verify(?: email| code| email address)?|confirm(?: email| code)?|continue|sign in|log in|submit(?: code)?)$",
    re.I,
)


class VerificationGuard:
    """CDP sees every redirected request; Playwright routes only intercept the first hop."""

    def __init__(self, page, allowed, allow_private=False):
        self.page, self.allowed, self.allow_private = page, allowed, allow_private
        self.blocked = False
        self.tasks = set()

    async def start(self):
        self.session = await self.page.context.new_cdp_session(self.page)
        self.session.on("Fetch.requestPaused", self.schedule)
        await self.session.send(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]},
        )
        return self

    def schedule(self, event):
        task = asyncio.create_task(self.check(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def check(self, event):
        permitted = False
        try:
            url = event["request"]["url"]
            permitted = origin(url) in self.allowed
            if permitted:
                await public_url(url, self.allow_private)
        except Exception:
            permitted = False
        try:
            if permitted:
                await self.session.send("Fetch.continueRequest", {"requestId": event["requestId"]})
            else:
                self.blocked = True
                await self.session.send(
                    "Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"}
                )
        except Exception:
            self.blocked = True

    async def close(self):
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        await self.session.detach()


class EmailBrowser:
    def __init__(self, engine):
        self.engine = engine
        self.mail = engine.service.mail
        self.rule = self.mail.rule_for(engine.job["url"])
        self.challenge_id = None
        self.attempts = 0
        self.secret_fields = []

    def state(self, observation):
        fields = observation["fields"]
        codes = [
            f
            for f in fields
            if f["type"] in {"text", "tel", "number", "password"}
            and (CODE_LABEL.fullmatch(f["label"]) or f.get("autocomplete") == "one-time-code")
        ]
        if (not codes or all(f["maxlength"] == 1 for f in codes)) and CODE.search(observation["text"]):
            digits = [f for f in fields if f["maxlength"] == 1 and f["type"] in {"text", "tel", "number"}]
            if 4 <= len(digits) <= 10:
                codes = digits
        if codes:
            return "code", codes
        if WAITING.search(observation["text"]):
            return "link", []
        return None, []

    def begin(self, kind="auto", since=None):
        if not self.rule:
            raise ValueError("Add an email verification rule for this employer in Settings")
        recipient = self.engine.run_record["packet"]["profile"]["email"]
        c = self.mail.begin(self.engine.run_id, self.rule, recipient, kind, since=since)
        self.challenge_id = c["id"]
        self.engine.emit(
            "email_wait",
            "Waiting for a matching employer verification email",
            {"challenge_id": c["id"], "kind": kind},
        )

    async def prepare_submission(self):
        if not self.rule or self.challenge_id:
            return
        # Shared ATS senders are serialized only while sending/receiving a code.
        for _ in range(60):
            try:
                self.begin()
                return
            except ValueError as exc:
                if "Another application is verifying" not in str(exc):
                    raise
                await asyncio.sleep(1)
        raise ValueError("Another verification is still active; retry this application later")

    def greenhouse_submission(self, obs):
        return (
            self.engine.job.get("ats") == "greenhouse"
            and self.engine.armed
            and bool(re.search(r"to submit your application.{0,100}code", obs["text"], re.I | re.S))
        )

    async def before_click(self, control_id):
        if not self.rule or self.challenge_id:
            return
        obs = await self.engine.form.scan()
        button = next((b for b in obs["controls"] if b["id"] == control_id), None)
        ordinary = [f for f in obs["fields"] if f["type"] != "checkbox"]
        email_only = ordinary and all(
            f["type"] == "email" or f["label"].lower() in {"email", "email address"} for f in ordinary
        )
        if button and email_only and (SEND.fullmatch(button["label"]) or CONFIRM.fullmatch(button["label"])):
            self.begin()  # Persist request window before the website sends the email.
            return True

    async def clear_secrets(self):
        for el in self.secret_fields:
            try:
                await el.fill("", timeout=500)
            except Exception:
                pass
        self.secret_fields = []

    async def handle(self):
        e = self.engine
        obs = await e.form.scan()
        kind, fields = self.state(obs)
        if not kind:
            return False
        if re.search(
            r"(?:sent|texted).{0,60}(?:your (?:phone|mobile)|sms)|(?:sms|text message) (?:code|verification)",
            obs["text"],
            re.I,
        ):
            raise ValueError("SMS verification requires an employer session; Gmail cannot supply this code")
        if not self.rule:
            from datetime import datetime

            started = e.run_record.get("started_at")
            since = datetime.fromisoformat(started).timestamp() if started else time.time()
            e.emit("email_wait", "Identifying this employer’s verification email automatically")
            for _ in range(6):
                self.rule = await self.mail.discover_rule(e.job, e.form.resolver.profile.email, since)
                if self.rule:
                    break
                await asyncio.sleep(3)
            if not self.rule:
                raise ValueError(
                    "No authenticated verification email could be linked uniquely to this employer"
                )
        if self.attempts:
            raise ValueError("Email verification did not complete; automatic resends are disabled")
        if origin(e.page.url) not in self.rule["link_origins"]:
            raise ValueError("Add this employer's login origin to its email verification rule")
        # Verification is allowed only on a dedicated authentication step.
        code_ids = {f["id"] for f in fields}
        other = [
            f for f in obs["fields"] if f["id"] not in code_ids and f["type"] not in {"email", "checkbox"}
        ]
        if other and not self.greenhouse_submission(obs):
            raise ValueError(
                "Email challenge is mixed with application fields; requires an employer-specific adapter"
            )
        self.attempts += 1
        if not self.challenge_id:
            # Navigation itself can have sent the challenge. Use this run's start, not an old inbox window.
            from datetime import datetime

            started = self.engine.run_record.get("started_at")
            since = datetime.fromisoformat(started).timestamp() if started else time.time()
            self.begin(kind, since=since)
        guard = None
        try:
            token = await self.mail.wait(self.challenge_id)
            if token["kind"] == "code":
                # Sites may rerender or redirect while delivery is pending. Reinspect before injecting a secret.
                obs = await e.form.scan()
                _, fields = self.state(obs)
                if origin(e.page.url) not in self.rule["link_origins"] or any(
                    origin(e.form.frames[f["id"]].url) not in self.rule["link_origins"] for f in fields
                ):
                    raise ValueError("Verification page changed to an origin outside the employer rule")
                code_ids = {f["id"] for f in fields}
                if not self.greenhouse_submission(obs) and any(
                    f["id"] not in code_ids and f["type"] not in {"email", "checkbox"} for f in obs["fields"]
                ):
                    raise ValueError("Verification page changed to a mixed application step")
                if not fields:
                    raise ValueError("Email contained a code but the website has no supported code input")
                code = token["value"]
                if len(fields) > 1 and len(code) != len(fields):
                    raise ValueError("Verification code length does not match the website")
                buttons = [b for b in obs["controls"] if CONFIRM.fullmatch(b["label"])]
                if self.greenhouse_submission(obs):
                    buttons = [
                        b for b in obs["controls"] if re.fullmatch(r"submit application", b["label"], re.I)
                    ]
                if len(buttons) > 1:
                    raise ValueError("Verification control is ambiguous")
                button = await e.form.locator(buttons[0]["id"]).element_handle() if buttons else None
                filled = 0
                for i, f in enumerate(fields):
                    el = e.form.locator(f["id"])
                    # Hold the original DOM node, never a locator that could match a new page.
                    handle = await el.element_handle()
                    self.secret_fields.append(handle)
                    await el.fill(code if len(fields) == 1 else code[i], timeout=3000)
                    filled += 1
                e.emit(
                    "email_step",
                    "Verification code entered in employer form",
                    {
                        "fields": len(fields),
                        "filled": filled,
                    },
                )
                if self.greenhouse_submission(obs) and not button:
                    # Greenhouse enables Submit only after all code digits are filled.
                    refreshed = await e.form.scan()
                    if (
                        not self.greenhouse_submission(refreshed)
                        or origin(e.page.url) not in self.rule["link_origins"]
                    ):
                        raise ValueError("Application verification changed before confirmation")
                    enabled = [
                        b
                        for b in refreshed["controls"]
                        if re.fullmatch(r"submit application", b["label"], re.I)
                    ]
                    if len(enabled) == 1:
                        button = await e.form.locator(enabled[0]["id"]).element_handle()
                if button and await button.is_visible():
                    # Capability is scoped to this inspected auth control, not the application's submit guard.
                    await button.evaluate("el => el.dataset.jpAuthControl='true'")
                    try:
                        await button.click(timeout=5000)
                    finally:
                        try:
                            await button.evaluate("el => delete el.dataset.jpAuthControl")
                        except Exception:
                            pass
                    e.emit("email_step", "Employer verification control clicked")
                # Zero buttons supports code inputs which auto-submit on the final character.
            else:
                e.verification_origins = set(self.rule["link_origins"])
                guard = await VerificationGuard(
                    e.page, e.verification_origins, e.config.allow_private_urls
                ).start()
                await e.page.goto(token["value"], wait_until="domcontentloaded", timeout=15000)
            for _ in range(60):
                if guard and guard.blocked:
                    raise ValueError("Verification redirect is outside the employer rule")
                await asyncio.sleep(0.25)
                after = await e.form.scan()
                pending, _ = self.state(after)
                personal = [
                    f
                    for f in after["fields"]
                    if f["type"] == "file"
                    or re.search(r"^(?:first name|last name|full name|phone|resume|cv)$", f["label"], re.I)
                ]
                explicit = re.search(
                    r"email (?:address )?(?:has been |is )?verified|verification (?:complete|successful)|successfully (?:verified|signed in)|thank you for applying|application (?:has been |was )?(?:successfully )?submitted|(?:your )?application (?:has been |was )?received|received your application",
                    after["text"],
                    re.I,
                )
                if not pending and (personal or explicit):
                    secret_keys = []
                    if token["kind"] == "link":
                        link = urlsplit(token["value"])
                        secret_keys = [k for k, _ in parse_qsl(link.query)]
                        if not link.query and not link.fragment and urlsplit(e.page.url).path == link.path:
                            # A one-use token may be embedded in the path. Leave that URL before an agent observes it.
                            await e.page.goto(e.job["url"], wait_until="domcontentloaded", timeout=15000)
                            restored = await e.form.scan()
                            if self.state(restored)[0] or not restored["fields"]:
                                raise ValueError(
                                    "Email was accepted but the application did not resume; requires an employer adapter"
                                )
                    self.mail.outcome(self.challenge_id, "verified", "Website accepted email verification")
                    e.emit(
                        "email_verified",
                        "Employer email verification completed",
                        {"challenge_id": self.challenge_id},
                    )
                    await e.page.evaluate(
                        """keys => {const u=new URL(location.href);for(const k of [...u.searchParams.keys()])if(keys.includes(k)||/token|code|secret|ticket/i.test(k))u.searchParams.delete(k);u.hash='';history.replaceState(null,'',u);} """,
                        secret_keys,
                    )
                    return True
            after = await e.form.scan()
            e.emit(
                "email_step",
                "Verification did not reach a confirmed transition",
                {
                    "code_fields_remaining": len(self.state(after)[1]),
                    "confirmation_found": bool(await e.form.proof()),
                    "submit_controls": sum(
                        bool(re.fullmatch(r"submit application", b["label"], re.I)) for b in after["controls"]
                    ),
                },
            )
            raise ValueError("No verified transition after entering the code or opening the link")
        except Exception as exc:
            self.mail.outcome(
                self.challenge_id, "failed", "Verification did not complete; inspect the employer session"
            )
            if guard and guard.blocked:
                raise ValueError("Verification redirect is outside the employer rule") from None
            if isinstance(exc, ValueError):
                raise
            # Browser exceptions include the tokenized URL. Never pass them to the model or log.
            raise ValueError(
                "Email verification failed in the browser; inspect the employer session"
            ) from None
        except BaseException:
            self.mail.outcome(self.challenge_id, "cancelled", "Browser attempt interrupted")
            raise
        finally:
            if guard:
                try:
                    await guard.close()
                except Exception:
                    pass
            e.verification_origins = None
            await self.clear_secrets()

    def close(self):
        if self.challenge_id:
            from .db import MailChallenge

            with self.mail.db.session() as s:
                c = s.get(MailChallenge, self.challenge_id)
                pending = c and c.state in {"pending", "matched", "consumed"}
            if pending:
                self.mail.outcome(self.challenge_id, "cancelled", "Browser attempt ended")


def redacted_url(url):
    p = urlsplit(url)
    return urlunsplit(
        (
            p.scheme,
            p.netloc,
            p.path,
            urlencode(
                [
                    (k, "REDACTED" if re.search("token|code|secret", k, re.I) else v)
                    for k, v in parse_qsl(p.query)
                ]
            ),
            "",
        )
    )
