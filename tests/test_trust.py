"""Negative tests for trustworthy state: no false completions, no untrusted evidence, no lost data."""

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select, text

from jobpilot.adapters.base import Adapter, Step
from jobpilot.adapters.workday import Workday
from jobpilot.answers import Resolver
from jobpilot.db import Job, Run
from jobpilot.discovery import add_job
from jobpilot.forms import FormSession
from jobpilot.schemas import JobInput, Profile
from mock_ats.gmail import FakeGmail
from test_browser import page as page  # noqa: F401

EMAIL = "alex@example.test"


def engine_for(service, page, tmp_path, url="https://careers.example.test/apply", mode="submit"):
    profile = Profile(name="Alex Example", email=EMAIL)
    form = FormSession(page, Resolver(profile, service.db, service.config), tmp_path / "resume.pdf", lambda *a: None)
    return SimpleNamespace(
        service=service,
        config=service.config,
        job={"url": url, "company": "Example", "title": "Engineer", "ats": "custom"},
        run_record={"mode": mode, "packet": {"resume_sha": "sha"}},
        profile=profile,
        form=form,
        run_id="run-1",
        armed=False,
        emit=lambda *a: None,
        trace=AsyncMock(),
        solve_captcha=AsyncMock(return_value=None),
        extend=lambda *a: None,
    )


async def test_job_page_thank_you_text_is_not_a_confirmation(service, page, tmp_path):
    await page.set_content(
        "<h1>Software Engineer</h1><p>Thank you for your interest. Thank you for applying!</p><a href='#'>Apply now</a>"
    )
    adapter = Adapter(engine_for(service, page, tmp_path))
    obs = await adapter.form.scan()
    assert adapter.classify(obs) == Step.JOB


async def test_confirmation_without_our_submit_is_never_recorded(service, page, tmp_path):
    await page.set_content("<h1>Thank you for applying!</h1><p>Your application has been submitted.</p>")
    adapter = Adapter(engine_for(service, page, tmp_path))
    obs = await adapter.form.scan()
    assert adapter.classify(obs) == Step.CONFIRMATION
    result = await adapter.on_confirmation(obs)
    assert result["state"] == "needs_review" and result["state"] not in {"confirmed", "already_applied"}


async def test_pre_existing_confirmation_text_does_not_confirm_a_submit(service, page, tmp_path, monkeypatch):
    # The review page already says "thank you for your application"; the submit fails silently.
    await page.set_content(
        "<h1>Review</h1><p>Thank you for your application interest in Example.</p>"
        "<button type=button onclick='window.clicked=true'>Submit Application</button>"
    )
    monkeypatch.setattr(service, "reserve_submission", lambda run_id: None)
    adapter = Adapter(engine_for(service, page, tmp_path))
    obs = await adapter.form.scan()
    final = adapter.find_final(obs)
    result = await adapter.commit(final)
    assert await page.evaluate("window.clicked")
    assert result["state"] == "submission_unknown"


async def test_confirmed_without_evidence_is_downgraded(service, prepared):
    service.db.set_setting("control", {"auto_submit": True, "paused": False})
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/evidence", company="Example", title="Engineer"))
    with service.db.session() as s:
        s.get(Job, job["id"]).status = "ready"
    run = await service.queue(job["id"], "submit", prepared["id"])
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "submitting"
    service.complete(run["id"], {"state": "confirmed", "reason": "clicked submit"}, 1)
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        assert row.state == "submission_unknown" and row.reason == "No confirmation evidence was captured"


def ack(gmail, sender, company="Example", title="Engineer"):
    return gmail.deliver(
        EMAIL, sender, f"Thank you for applying to {company}",
        f"Thank you for applying to {company} for the {title} position. We received your application.",
    )


async def test_restart_during_final_submit_never_resubmits_and_email_can_confirm(service, prepared):
    from jobpilot.receipts import check_receipts
    from jobpilot.workers import recover_interrupted

    gmail = FakeGmail(EMAIL).connect(service)
    service.db.set_setting("control", {"auto_submit": True, "paused": False})
    job, _ = add_job(service.db, JobInput(url="https://careers.example.test/job/restart", company="Example", title="Engineer"))
    with service.db.session() as s:
        s.get(Job, job["id"]).status = "ready"
    run = await service.queue(job["id"], "submit", prepared["id"])
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "submitting"  # The worker died after clicking Submit.
    recover_interrupted(service)
    with service.db.session() as s:
        assert s.get(Run, run["id"]).state == "submission_unknown"
    try:
        await service.queue(job["id"], "submit", prepared["id"], retry=True)
        raise AssertionError("An uncertain submission was queued again")
    except ValueError as exc:
        assert "uncertain submission" in str(exc)
    # An authenticated acknowledgement from an unrelated sender is evidence, not a confirmation.
    ack(gmail, "promo@deals.shopping.test")
    await check_receipts(service.mail, run["id"])
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        assert row.state == "submission_unknown" and row.receipt.get("email_confirmed") is False
        row.receipt = {}
    # The employer's own acknowledgement confirms it, as email evidence only.
    ack(gmail, "no-reply@example.test")
    await check_receipts(service.mail, run["id"])
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        assert row.state == "confirmed"
        assert row.receipt["email_confirmed"] is True and row.receipt["website_confirmed"] is False


CAPTCHA_PAGE = """<form><label>Full name<input name=name required></label>
<input name=website_hp id=honey-pot class=hp_field tabindex=-1>
<div class=h-captcha data-sitekey=x><iframe title="hCaptcha checkbox" src="https://newassets.hcaptcha.com/captcha/v1/a/static/hcaptcha.html#frame=checkbox"></iframe>
<textarea name=h-captcha-response></textarea><button type=button>Skip</button></div>
<textarea name=g-recaptcha-response></textarea>
<iframe title="Verification challenge" src="https://client-api.arkoselabs.com/fc/gc/?token=1"></iframe>
<button type=button>Next</button></form>"""


async def test_captcha_widgets_never_become_applicant_fields_or_navigation(service, page, tmp_path):
    widget = "<p>Please click each image</p><button>Skip</button><button aria-label='Refresh Challenge.'>R</button><button>Accessibility</button><input type=text placeholder=Answer><button>Submit</button>"
    await page.route("https://newassets.hcaptcha.com/**", lambda r: r.fulfill(content_type="text/html", body=widget))
    await page.route("https://client-api.arkoselabs.com/**", lambda r: r.fulfill(content_type="text/html", body=widget))
    await page.route("https://careers.example.test/**", lambda r: r.fulfill(content_type="text/html", body=CAPTCHA_PAGE))
    await page.goto("https://careers.example.test/apply")
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "r.pdf", lambda *a: None)
    obs = await form.scan()
    assert [f["label"] for f in obs["fields"]] == ["Full name"]
    assert [c["label"] for c in obs["controls"]] == ["Next"]


async def test_open_reset_dialog_takes_precedence_over_the_page_behind(service, page, tmp_path):
    url = "https://acme.wd5.myworkdayjobs.com/External/job/X_R1"
    await page.route("**/*", lambda r: r.fulfill(content_type="text/html", body="""
      <a data-automation-id="adventureButton" role="button" href="#">Apply</a>
      <label>Email Address<input type=email data-automation-id="email"></label>
      <label>Password<input type=password data-automation-id="password"></label>
      <button data-automation-id="signInSubmitButton">Sign In</button>
      <div role="dialog" aria-modal="true" aria-label="Reset Password"><h2>Reset Password</h2>
        <label>Email Address<input type=email data-automation-id="email" id=r></label>
        <button data-automation-id="resetPasswordButton">Reset Password</button></div>"""))
    await page.goto(url)
    engine = engine_for(service, page, tmp_path, url=url)
    engine.job["ats"] = "workday"
    adapter = Workday(engine)
    obs = await adapter.form.scan()
    assert adapter.classify(obs) == Step.RESET_PASSWORD
    view = adapter.dialog_view(obs)
    assert [f["label"] for f in view["fields"]] == ["Email Address"]
    assert [c["label"] for c in view["controls"]] == ["Reset Password"]


def test_profile_round_trip_keeps_unknown_keys_and_reviewed_answers(service):
    stored = {
        "name": "Alex", "email": EMAIL, "application_source": "LinkedIn", "autonomous": True,
        "allow_application_consents": True, "facts": {"street_address": "1 Main St"},
        "reviewed_answers": [{"id": "r1", "question": "Q?", "answer": "A", "scope": "personal", "layer": "fact"}],
        "future_setting": {"x": 1},
    }
    profile = Profile.model_validate(stored)
    dumped = profile.model_dump()
    assert dumped["future_setting"] == {"x": 1} and dumped["autonomous"] and dumped["application_source"] == "LinkedIn"
    assert dumped["reviewed_answers"][0]["answer"] == "A" and dumped["address"]["line1"] == "1 Main St"


def test_account_password_alias_and_generated_fallback(tmp_path):
    from jobpilot.config import Settings

    legacy = Settings(_env_file=None, data_dir=tmp_path, account_password="Legacy!Pass9x")
    assert legacy.application_password == "Legacy!Pass9x"
    assert Settings(_env_file=None, data_dir=tmp_path).application_password == ""


def test_legacy_branch_tables_migrate_once_without_secrets(service):
    import hashlib
    import hmac

    service.config.application_password = "Legacy!Pass9x"
    fingerprint = hmac.new(
        service.config.app_token.encode(), b"Legacy!Pass9x", hashlib.sha256
    ).hexdigest()[:16]
    with service.db.engine.begin() as c:
        c.execute(text("CREATE TABLE knowledge (id TEXT, question TEXT, norm TEXT, options TEXT, answer TEXT, scope TEXT)"))
        c.execute(text("INSERT INTO knowledge VALUES ('k1','Years of Python?','years of python','[]','6','global')"))
        c.execute(text("INSERT INTO knowledge VALUES ('k2','Why Globex?','why employer','[]','Mission','employer:globex')"))
        c.execute(text("CREATE TABLE accounts (id TEXT, realm TEXT, ats TEXT, email TEXT, state TEXT, password_hash TEXT)"))
        c.execute(text(f"INSERT INTO accounts VALUES ('a1','workday:acme.wd5.myworkdayjobs.com','workday','{EMAIL}','active','{fingerprint}')"))
        c.execute(text(f"INSERT INTO accounts VALUES ('a2','icims:careers-x.icims.com','icims','{EMAIL}','active','stale')"))
    service.migrate_legacy()
    service.migrate_legacy()  # Idempotent.
    answers = service.db.get_setting("profile")["reviewed_answers"]
    assert {(a["question"], a["scope"]) for a in answers} == {("Years of Python?", "personal"), ("Why Globex?", "employer")}
    from jobpilot.accounts import AccountStore

    store = AccountStore(service, EMAIL)
    assert store.get("https://acme.wd5.myworkdayjobs.com")["password"] == "Legacy!Pass9x"
    assert store.get("https://careers-x.icims.com") is None  # Password not provably the same.
    raw = json.dumps(service.db.get_setting(store.key("https://acme.wd5.myworkdayjobs.com")))
    assert "Legacy!Pass9x" not in raw


async def test_no_secret_reaches_events_or_traces(service, prepared, tmp_path):
    """Codes, links and passwords live only in the vault; events carry kinds and states."""
    from jobpilot.db import Event, MailChallenge

    gmail = FakeGmail(EMAIL).connect(service)
    job, _ = add_job(service.db, JobInput(url="https://acme.wd5.myworkdayjobs.com/External/job/S_R1", company="Acme"))
    run = await service.queue(job["id"], resume_id=prepared["id"])
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "running"
    challenge = await service.inbox.arm(run["id"], {"url": job["url"], "ats": "workday", "company": "Acme"}, EMAIL, "code")
    gmail.deliver(EMAIL, "acme@myworkday.com", "Code", "Your verification code is 739104.")
    await service.mail.poll_once()
    with service.db.session() as s:
        stored = s.get(MailChallenge, challenge["id"]).payload
    assert stored and "739104" not in stored  # Sealed.
    token = await service.inbox.wait(challenge)
    assert token == {"kind": "code", "value": "739104"}
    with service.db.session() as s:
        events = json.dumps([[e.kind, e.message, e.data] for e in s.scalars(select(Event))])
        assert "739104" not in events
        assert s.get(MailChallenge, challenge["id"]).payload == ""  # Consumed and erased.
    assert time.time() > 0 and datetime.now(UTC)


async def test_existing_confirmations_survive_upgrade_and_receipt_checks(service, prepared):
    """The six live confirmations recorded by main keep their state and evidence."""
    from jobpilot.receipts import check_receipts
    from jobpilot.service import Service

    gmail = FakeGmail(EMAIL).connect(service)
    companies = ["Blend", "My Funded Futures", "Cargomatic", "Zip Co", "May Mobility", "Ramp"]
    ids = {}
    for i, company in enumerate(companies):
        job, _ = add_job(service.db, JobInput(url=f"https://jobs.example.test/{i}", company=company, title="AI Engineer"))
        with service.db.exclusive() as s:
            run = Run(
                job_id=job["id"], mode="submit", state="confirmed", reason="Website confirmation captured",
                packet={"profile": {"email": EMAIL}}, resume_id=prepared["id"],
                receipt={"type": "explicit_confirmation_page", "url": f"https://jobs.example.test/{i}/thanks"}
                if i % 2 else {"type": "explicit_confirmation_page", "email": {"message_id": f"m{i}"}},
            )
            s.add(run)
            s.flush()
            s.get(Job, job["id"]).status = "confirmed"
            ids[run.id] = dict(run.receipt)
    Service(service.db, service.config)  # Startup migrations on an upgraded database.
    gmail.deliver(EMAIL, "promo@deals.shopping.test", "Thank you for applying to Ramp",
                  "Thank you for applying to Ramp for the AI Engineer position. We received your application.")
    await check_receipts(service.mail)
    with service.db.session() as s:
        for run_id, receipt in ids.items():
            row = s.get(Run, run_id)
            assert row.state == "confirmed"
            assert {k: row.receipt[k] for k in receipt} == receipt  # Original evidence untouched.
            assert row.receipt.get("email_confirmed") in (None, False) or "email" in receipt
    # None of them can be applied to again.
    for run_id in ids:
        with service.db.session() as s:
            job_id = s.get(Run, run_id).job_id
        try:
            await service.queue(job_id, "submit", prepared["id"], retry=True)
            raise AssertionError("A confirmed job was queued again")
        except ValueError as exc:
            assert "submitted" in str(exc) or "uncertain" in str(exc)
