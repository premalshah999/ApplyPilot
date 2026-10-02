"""Real Chromium + owned HTTPS routes + a synthetic Gmail HTTP transport."""

import asyncio
import json
import time
from urllib.parse import parse_qs

import pytest
import httpx

from jobpilot.answers import Resolver
from jobpilot.browser import GUARD, BrowserEngine
from jobpilot.email_browser import EmailBrowser
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from test_browser import page as page
from test_mail import configured, message, provider

APPLICATION = """<h1>Application</h1><form action="/applications" method="post">
<label>Full name<input name="name" required></label>
<label>Email<input name="email" type="email" required></label>
<label>Resume<input name="resume" type="file" required></label>
<button>Submit application</button></form>"""


async def setup_engine(service, page, tmp_path, resume_pdf, initial, *, split=False, redirect=False):
    box, run, rule = await configured(service)
    cfg = service.config
    cfg.mail_poll_seconds = 0.1
    cfg.mail_wait_seconds = 5
    resume = tmp_path / "fixture-resume.pdf"
    resume.write_bytes(resume_pdf)
    profile = Profile(
        name="Alex Example", email="alex@example.test", facts={"first_name": "Alex", "last_name": "Example"}
    )
    run["packet"] = {"profile": profile.model_dump(), "resume_path": str(resume)}
    e = BrowserEngine(service, run["id"])
    e.job = {
        "url": "https://careers.example.test/start",
        "demo": False,
        "ats": "greenhouse",
        "company": "Example",
    }
    e.run_record, e.page, e.dns_cache = run, page, set()
    e.form = FormSession(page, Resolver(profile, service.db, cfg, run["id"], "Example"), resume, e.emit)
    e.email_verification = EmailBrowser(e)
    seen = []

    async def fixture(route):
        request = route.request
        seen.append((request.url, request.method))
        if request.url.endswith("/start"):
            return await route.fulfill(body=initial, content_type="text/html")
        if "/verify-code" in request.url:
            values = parse_qs(request.post_data or "")
            code = (
                "".join(values.get("digit" + str(i), [""])[0] for i in range(6))
                if split
                else values.get("code", [""])[0]
            )
            body = APPLICATION if code == "483921" else initial
            return await route.fulfill(body=body, content_type="text/html")
        if "/verify?" in request.url:
            if redirect:
                return await route.fulfill(
                    status=302, headers={"Location": "https://unexpected.example.test/stolen"}
                )
            assert "fixture-link-secret" in request.url
            return await route.fulfill(body=APPLICATION, content_type="text/html")
        return await route.fulfill(status=404, body="Unexpected request")

    await page.context.add_init_script(GUARD)
    await page.context.route("**/*", fixture)
    await page.context.route("**/*", e.network_guard)
    await page.goto(e.job["url"])
    return e, seen


@pytest.mark.parametrize("kind", ["code", "split", "link", "auto", "rerender"])
async def test_verification_then_application_fill_in_real_browser(service, page, tmp_path, resume_pdf, kind):
    if kind == "link":
        initial = "<h1>Check your email</h1><p>We sent you a verification link.</p>"
        msg = message(
            '<a href="https://careers.example.test/verify?token=fixture-link-secret">Verify email</a>',
            html=True,
        )
    else:
        fields = (
            "".join(f'<input name="digit{i}" aria-label="Digit {i + 1}" maxlength="1">' for i in range(6))
            if kind == "split"
            else '<label>Verification code<input autocomplete="one-time-code" name="code"></label>'
        )
        initial = f'<h1>Enter verification code</h1><form method="post" action="/verify-code">{fields}<button>Verify email</button></form>'
        msg = message()
    if kind == "auto":
        initial = (
            '<h1>Enter verification code</h1><label>Verification code<input name="code" autocomplete="one-time-code"></label><script>document.querySelector("input").addEventListener("input", e=>{if(e.target.value.length===6)document.body.innerHTML='
            + json.dumps(APPLICATION)
            + "})</script>"
        )
    e, seen = await setup_engine(service, page, tmp_path, resume_pdf, initial, split=kind == "split")
    msg["internalDate"] = str(int(time.time() * 1000))
    e.service.mail.transport = provider([msg])
    if kind == "rerender":
        fake = e.service.mail.transport

        async def rerendering_provider(request):
            if request.url.path.endswith("/messages"):
                await page.evaluate(
                    "() => {const old=document.querySelector('input[name=code]');const fresh=old.cloneNode();fresh.removeAttribute('data-jp-id');old.replaceWith(fresh);}"
                )
            return fake.handle_request(request)

        e.service.mail.transport = httpx.MockTransport(rerendering_provider)
    poller = asyncio.create_task(e.service.mail.poll())
    try:
        assert await e.handle_email()
        assert e.service.mail.summary()["challenges"][0]["state"] == "verified"
        # Original OTP nodes were detached; cleanup must not clear fields on the new document.
        report = await e.form.fill_current()
        assert report["ok"] and report["resume_attached"], report
        assert await page.get_by_label("Full name").input_value() == "Alex Example"
        result = await e.finish()
        assert result["state"] == "dry_run_passed", result
        assert not any("/applications" in url for url, _ in seen)
        summary = json.dumps(e.service.mail.summary()) + json.dumps(e.form.ledger)
        assert "483921" not in summary and "fixture-link-secret" not in summary
        assert "fixture-link-secret" not in page.url
        assert e.armed is False and e.verification_origins is None
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        e.email_verification.close()


async def test_link_redirect_outside_rule_is_blocked(service, page, tmp_path, resume_pdf):
    e, seen = await setup_engine(
        service, page, tmp_path, resume_pdf, "<h1>Check your email</h1>", redirect=True
    )
    service.mail.transport = provider(
        [
            message(
                '<a href="https://careers.example.test/verify?token=fixture-link-secret">Verify email</a>',
                html=True,
            )
        ]
    )
    poller = asyncio.create_task(service.mail.poll())
    try:
        # Chromium's Fetch guard rejects redirected requests before they reach the target.
        with pytest.raises(ValueError, match="outside the employer rule"):
            await e.email_verification.handle()
        assert all("unexpected.example.test" not in url for url, _ in seen)
        assert service.mail.summary()["challenges"][0]["state"] == "failed"
        assert e.verification_origins is None and e.armed is False
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)


async def test_email_request_window_is_persisted_before_send_and_submit_stays_guarded(
    service, page, tmp_path, resume_pdf
):
    html = '<form onsubmit="event.preventDefault();document.body.innerHTML=\'<h1>Check your email</h1>\'"><label>Email<input type="email" name="email"></label><button>Send code</button></form>'
    e, _ = await setup_engine(service, page, tmp_path, resume_pdf, html)
    obs = await e.form.scan()
    await page.get_by_label("Email").fill("alex@example.test")
    button = obs["controls"][0]
    assert await e.email_verification.before_click(button["id"])
    assert service.mail.summary()["challenges"][0]["state"] == "pending"
    await e.form.click(button["id"], auth_control=True)
    assert await page.get_by_role("heading", name="Check your email").count() == 1
    e.email_verification.close()
    assert service.mail.summary()["challenges"][0]["state"] == "cancelled"


async def test_mixed_application_and_code_input_cannot_unlock_submit(service, page, tmp_path, resume_pdf):
    html = APPLICATION.replace("<h1>Application</h1>", '<label>Verification code<input name="code"></label>')
    e, seen = await setup_engine(service, page, tmp_path, resume_pdf, html)
    assert not await e.handle_email()
    assert e.result["state"] == "needs_review" and "mixed" in e.result["reason"]
    assert service.mail.summary()["challenges"] == []
    await page.get_by_role("button", name="Submit application").click()
    assert not any("/applications" in url for url, _ in seen)


@pytest.mark.parametrize("kind", ["screening", "sms"])
async def test_auth_classifier_does_not_use_email_for_other_questions(
    service, page, tmp_path, resume_pdf, kind
):
    html = (
        "<label>Have you implemented OTP authentication?<textarea></textarea></label>"
        if kind == "screening"
        else '<h1>SMS verification</h1><p>We sent a code to your phone.</p><label>Verification code<input name="code" autocomplete="one-time-code"></label>'
    )
    e, _ = await setup_engine(service, page, tmp_path, resume_pdf, html)
    assert not await e.handle_email()
    if kind == "sms":
        assert e.result["state"] == "needs_review" and "SMS" in e.result["reason"]
    else:
        assert e.result is None
    assert service.mail.summary()["challenges"] == []
