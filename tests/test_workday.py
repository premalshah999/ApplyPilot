from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.answers import Resolver
from jobpilot.browser import GUARD
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from jobpilot.workday import WorkdayAuth
from test_browser import page as page


async def test_workday_account_is_encrypted_and_password_never_enters_answers(service, page, tmp_path):
    url = "https://example.wd1.myworkdayjobs.com/Careers/job/Engineer_123"
    html = """<form onsubmit="event.preventDefault();document.body.innerHTML='<label>Resume<input type=file></label>'"><label>Email Address<input data-automation-id="email"></label>
    <input type="password" data-automation-id="password">
    <input type="password" data-automation-id="verifyPassword">
    <div role="button" aria-label="Create Account" data-automation-id="click_filter"
     onclick="this.closest('form').requestSubmit()">Create Account</div>
    <button type="submit" data-automation-id="createAccountSubmitButton" tabindex="-2"
     aria-hidden="true">Create Account</button></form>"""
    await page.context.add_init_script(GUARD)
    await page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html))
    await page.goto(url)
    profile = Profile(email="alex@example.test", allow_account_creation=True)
    form = FormSession(
        page, Resolver(profile, service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )

    async def prepare():
        pass

    events = []
    e = SimpleNamespace(
        job={"url": url},
        page=page,
        form=form,
        db=service.db,
        service=service,
        email_verification=SimpleNamespace(prepare_submission=prepare, state=lambda o: (None, [])),
        emit=lambda *args: events.append(args),
    )
    auth = WorkdayAuth(e)
    assert await auth.run()
    creds = auth.credentials()
    saved = service.db.get_setting(auth.account_key())
    assert saved["state"] == "authenticated"
    assert creds["password"] not in str(saved) + str(events) + str(form.ledger)
    assert await page.locator('input[type="file"]').count() == 1


async def test_expired_account_recovers_via_email_without_exposing_password(
    service, page, tmp_path, monkeypatch
):
    url = "http://127.0.0.1:31999/job"
    login = """<button onclick="document.body.innerHTML='<input data-automation-id=email><button data-automation-id=resetPasswordButton>Reset Password</button>'">Forgot your password?</button>"""
    reset = """<form onsubmit="event.preventDefault();if(a.value===b.value && a.value.length>12)document.body.innerHTML='Your password has been changed';">
    <input id=a type=password><input id=b type=password><button>Reset Password</button></form>"""
    await page.context.add_init_script(GUARD)
    await page.route(
        "**/*",
        lambda r: r.fulfill(content_type="text/html", body=reset if "/reset?" in r.request.url else login),
    )
    await page.goto(url)
    events, outcomes = [], []
    monkeypatch.setattr(service.mail, "begin", lambda *a: {"id": "reset-challenge"})
    monkeypatch.setattr(
        service.mail,
        "wait",
        AsyncMock(return_value={"kind": "link", "value": url.replace("/job", "/reset?token=one-use-secret")}),
    )
    monkeypatch.setattr(service.mail, "outcome", lambda *a: outcomes.append(a))
    profile = Profile(email="alex@example.test", allow_account_creation=True)
    e = SimpleNamespace(
        job={"url": url},
        page=page,
        run_id="fixture",
        db=service.db,
        config=service.config,
        service=service,
        form=FormSession(
            page, Resolver(profile, service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
        ),
        email_verification=SimpleNamespace(rule={"link_origins": ["http://127.0.0.1:31999"]}),
        emit=lambda *a: events.append(a),
    )
    auth = WorkdayAuth(e)
    await auth.recover()
    credentials = auth.credentials()
    saved = service.db.get_setting(auth.account_key())
    assert saved["state"] == "password_reset"
    assert credentials["password"] not in str(saved) + str(events) + str(outcomes)
    assert "one-use-secret" not in str(events) + str(outcomes) + page.url
    assert outcomes[-1][1] == "verified"
