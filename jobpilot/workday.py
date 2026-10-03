"""Workday account setup uses deterministic controls and an encrypted credential store."""

import asyncio
import hashlib
import re
import time

from .ats import detect
from .db import Setting
from .mail import origin
from .accounts import AccountStore


class WorkdayAuth:
    def __init__(self, engine):
        self.engine = engine
        self.secrets = []
        self.attempted = False
        self.account_state = None
        self.recovery_attempted = False

    def account_key(self):
        e = self.engine
        return (
            "employer_account:"
            + hashlib.sha256(
                (origin(e.job["url"]) + ":" + e.form.resolver.profile.email.lower()).encode()
            ).hexdigest()
        )

    def credentials(self, create=False):
        e = self.engine
        key = self.account_key()
        # Generate outside the employer transaction: shared() has its own lock.
        shared = self.shared_credentials() if create else None
        with e.db.exclusive() as s:
            row = s.get(Setting, key)
            if row:
                self.account_state = row.value.get("state")
                return e.service.mail.vault.open(row.value["credentials"])
            if not create:
                return None
            value = shared
            s.add(
                Setting(
                    key=key,
                    value={
                        "origin": origin(e.job["url"]),
                        "credentials": e.service.mail.vault.seal(value),
                        "state": "created_locally",
                    },
                )
            )
            self.account_state = "created_locally"
            return value

    def shared_credentials(self):
        return AccountStore(self.engine.service, self.engine.form.resolver.profile.email).shared()

    async def clear(self):
        for field in self.secrets:
            try:
                await field.fill("", timeout=300)
            except Exception:
                pass
        self.secrets = []

    async def click(self, el):
        handle = await el.element_handle()
        await handle.evaluate("e=>{e.dataset.jpAuthControl='true';const f=e.closest('form');if(f)f.dataset.jpAuthControl='true'}")
        clicked_overlay = None
        try:
            # Workday renders an accessible click_filter over some submit buttons.
            # Click the visible control that a user actually reaches.
            label = (await handle.inner_text()).strip()
            overlay = (
                self.engine.page.locator(f'[data-automation-id="click_filter"][aria-label="{label}"]')
                if label in {"Create Account", "Sign In", "Reset Password", "Submit"}
                else None
            )
            if overlay and await overlay.count() == 1 and await overlay.is_visible():
                clicked_overlay = await overlay.element_handle()
                await clicked_overlay.evaluate("e=>e.dataset.jpAuthControl='true'")
                await overlay.click(timeout=8000)
            else:
                await handle.click(timeout=8000)
        finally:
            try:
                await handle.evaluate("e=>{delete e.dataset.jpAuthControl;const f=e.closest('form');if(f)delete f.dataset.jpAuthControl}")
                if clicked_overlay:
                    await clicked_overlay.evaluate("e=>delete e.dataset.jpAuthControl")
            except Exception:
                pass

    def save_credentials(self, value, state, **extra):
        with self.engine.db.exclusive() as s:
            row = s.get(Setting, self.account_key())
            saved = {
                "origin": origin(self.engine.job["url"]),
                "credentials": self.engine.service.mail.vault.seal(value),
                "state": state,
                **extra,
            }
            if row:
                row.value = {**row.value, **saved}
            else:
                s.add(Setting(key=self.account_key(), value=saved))
        self.account_state = state

    async def recover(self):
        """Recover this employer account through its authenticated reset email."""
        from .email_browser import VerificationGuard

        e, page = self.engine, self.engine.page
        mail = e.service.mail
        rule = e.email_verification.rule
        if self.recovery_attempted or not rule:
            raise ValueError("Workday account recovery requires a connected mailbox for this employer")
        saved = e.db.get_setting(self.account_key(), {})
        if time.time() - saved.get("reset_requested_at", 0) < 300:
            raise ValueError("A Workday password reset was recently requested; wait five minutes before retrying")
        self.recovery_attempted = True
        forgot = page.get_by_text("Forgot your password?", exact=True)
        if await forgot.count() != 1:
            raise ValueError("Workday password recovery control is unavailable")
        await self.click(forgot)
        reset_button = page.locator('[data-automation-id="resetPasswordButton"]')
        await reset_button.wait_for(state="visible", timeout=10000)
        email = page.locator('input[data-automation-id="email"]')
        await email.wait_for(state="visible", timeout=10000)
        await email.fill(e.form.resolver.profile.email)
        # Account creation and password reset must not reuse the same mail
        # challenge: a reset message has different matching rules.
        if getattr(e.email_verification, 'challenge_id', None):
            e.email_verification.close()
            e.email_verification.challenge_id = None
        challenge = mail.begin(e.run_id, rule, e.form.resolver.profile.email, "password_reset")
        credentials = self.shared_credentials()
        # Keep the working password until the website confirms its replacement.
        self.save_credentials(self.credentials() or credentials, "reset_requested", reset_requested_at=time.time())
        guard = None
        e.emit("account", "Recovering the employer account through the connected mailbox")
        try:
            await self.click(reset_button)
            if hasattr(e, 'browser_challenge') and (challenge_result := await e.browser_challenge()):
                e.result = challenge_result
                raise ValueError('Account recovery security challenge was not accepted')
            e.emit("account", "Employer password reset requested")
            token = await mail.wait(challenge["id"])
            e.emit("account", "Employer password reset email received")
            if token["kind"] != "link":
                raise ValueError("Workday recovery did not provide a password reset link")
            e.verification_origins = set(rule["link_origins"])
            guard = await VerificationGuard(page, e.verification_origins, e.config.allow_private_urls).start()
            await page.goto(token["value"], wait_until="domcontentloaded", timeout=25000)
            fields = page.locator('input[type="password"]:visible')
            await fields.first.wait_for(timeout=20000)
            e.emit("account", "Employer password reset form opened")
            if guard.blocked or origin(page.url) not in e.verification_origins or await fields.count() != 2:
                raise ValueError("Workday password reset form could not be verified")
            for field in await fields.all():
                self.secrets.append(await field.element_handle())
                await field.fill(credentials["password"])
                await field.blur()
            button = page.get_by_role("button", name=re.compile(r"^(Reset Password|Submit|Change Password)$", re.I))
            visible = [b for b in await button.all() if await b.is_visible()]
            if len(visible) != 1:
                raise ValueError("Workday password reset confirmation is ambiguous")
            await self.click(visible[0])
            for _ in range(60):
                await asyncio.sleep(0.25)
                body = await page.locator("body").inner_text()
                if re.search(
                    r"password (?:has been|was) (?:successfully )?(?:changed|reset)|"
                    r"password (?:reset|change) (?:was )?successful|"
                    r"password (?:successfully (?:changed|reset)|(?:changed|reset) successfully)",
                    body, re.I,
                ):
                    self.save_credentials(credentials, "password_reset")
                    mail.outcome(challenge["id"], "verified", "Employer accepted password reset")
                    e.emit("account", "Employer password recovered and saved securely")
                    self.attempted = False
                    return
            raise ValueError("Workday did not confirm the password reset")
        except Exception:
            mail.outcome(challenge["id"], "failed", "Employer account recovery did not complete")
            raise ValueError("Workday account recovery did not complete; see account and email status") from None
        finally:
            await self.clear()
            if guard:
                await guard.close()
            e.verification_origins = None
            # Never expose the one-time reset URL to the agent or screenshot ledger.
            if not getattr(e, 'result', None):
                await page.goto(e.job["url"], wait_until="domcontentloaded", timeout=25000)

    async def run(self):
        e, page = self.engine, self.engine.page
        if detect(e.job["url"]).id != "workday" or origin(page.url) != origin(e.job["url"]):
            raise ValueError("Workday account origin does not match this employer")
        if not e.form.resolver.profile.allow_account_creation:
            return False
        for _ in range(60):
            if hasattr(e, "browser_challenge") and (challenge := await e.browser_challenge()):
                e.result = challenge
                return False
            if re.search(r"Workday is currently unavailable|experiencing a service interruption", await page.locator('body').inner_text(), re.I):
                raise ValueError("Workday is temporarily unavailable due to a service interruption. No application was submitted.")
            if origin(page.url) != origin(e.job["url"]):
                raise ValueError("Workday changed login origin")
            cookie = page.get_by_role("button", name="Accept Cookies", exact=True)
            if await cookie.count() and await cookie.is_visible():
                await self.click(cookie)
            for name in ["Apply", "Continue Application", "Autofill with Resume", "Sign in with email"]:
                button = page.get_by_role("button", name=name, exact=True)
                if await button.count() == 1 and await button.is_visible():
                    await self.click(button)
                    await asyncio.sleep(0.4)
            password = page.locator('input[data-automation-id="password"]')
            if await password.count() and await password.is_visible():
                stored = self.credentials()
                if getattr(e, "imported_session", False) and self.account_state not in {"authenticated", "password_reset"}:
                    await self.recover()
                    continue
                if self.attempted:
                    await self.recover()
                    continue
                create = page.locator('[data-automation-id="createAccountLink"]')
                if self.account_state in {"authenticated", "password_reset"} and await page.locator('[data-automation-id="verifyPassword"]').count():
                    await self.click(page.locator('[data-automation-id="signInLink"]'))
                    await page.locator('[data-automation-id="verifyPassword"]').wait_for(state="detached", timeout=8000)
                if (not stored or self.account_state == "created_locally") and await create.count():
                    await self.click(create)
                    await page.locator('[data-automation-id="verifyPassword"]').wait_for(timeout=8000)
                creating = await page.locator('[data-automation-id="verifyPassword"]').count() > 0
                creds = stored or self.credentials(create=True)
                email = page.locator('[data-automation-id="email"]')
                if await email.count() != 1:
                    email = page.get_by_label("Email Address", exact=False)
                await email.fill(creds["email"])
                for field in [password, page.locator('[data-automation-id="verifyPassword"]')]:
                    if await field.count():
                        self.secrets.append(await field.element_handle())
                        await field.fill(creds["password"])
                if e.form.resolver.profile.accept_all_application_terms:
                    for checkbox in await page.locator('input[type="checkbox"]:visible').all():
                        await checkbox.check()
                if creating:
                    await e.email_verification.prepare_submission()
                button = page.locator(
                    '[data-automation-id="createAccountSubmitButton"]'
                    if creating
                    else '[data-automation-id="signInSubmitButton"]'
                )
                if await button.count() != 1:
                    raise ValueError("Workday authentication control was not unique")
                self.attempted = True
                e.emit(
                    "account",
                    "Creating an employer account"
                    if creating
                    else "Signing in with the saved employer account",
                )
                await self.click(button)
                if hasattr(e, 'browser_challenge') and (challenge := await e.browser_challenge()):
                    e.result = challenge
                    return False
                for _ in range(40):
                    await asyncio.sleep(0.25)
                    if not await password.count() or not await password.is_visible():
                        break
                await self.clear()
                observation = await e.form.scan()
                if e.email_verification.state(observation)[0]:
                    await e.email_verification.handle()
                if not await password.count() or not await password.is_visible():
                    # Workday briefly removes the password field during a redirect,
                    # including redirects back to Sign In. Wait for the actual form.
                    await asyncio.sleep(1)
            uploads = page.locator('input[type="file"]')
            # A saved draft can reopen on any section. Resume uploads and legal-name
            # fields may then be absent, even though the application form is ready.
            advance = page.get_by_role("button", name="Save and Continue", exact=True)
            ready = (await uploads.count() > 0
                     or await page.locator('[data-automation-id="legalNameSection_firstName"]').count() > 0
                     or (await advance.count() == 1 and await advance.is_visible()))
            if ready and not await page.locator('input[type="password"]:visible').count():
                if value := self.credentials():
                    self.save_credentials(value, 'authenticated')
                e.emit('account', 'Employer application form opened')
                return True
            await asyncio.sleep(0.5)
        raise ValueError("Workday did not reach an application form within the account setup window")
