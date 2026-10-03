"""Regressions for defects found by the adversarial review of the merge."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.adapters.base import Adapter, Step
from jobpilot.answers import Resolver
from jobpilot.db import Run
from jobpilot.discovery import add_job
from jobpilot.forms import FormSession
from jobpilot.mail import sender_allowed, tenant_tokens
from jobpilot.receipts import trusted_sender
from jobpilot.schemas import JobInput, Profile
from mock_ats.gmail import FakeGmail
from test_browser import page as page  # noqa: F401
from test_trust import engine_for

EMAIL = "alex@example.test"


def form(page, service, tmp_path, profile=None):
    return FormSession(page, Resolver(profile or Profile(), service.db, service.config), tmp_path / "Alex_Resume.pdf", lambda *a: None)


async def test_lever_radio_question_uses_its_application_label(page, service, tmp_path):
    await page.set_content("""<ul><li class="application-question custom-question">
      <div class="application-label"><div class="text">Will you now or in the future require visa sponsorship?<span>✱</span></div></div>
      <div class="application-field"><ul>
        <li><label><input type=radio name="cards[abc][field0]" value=Yes>Yes</label></li>
        <li><label><input type=radio name="cards[abc][field0]" value=No>No</label></li></ul></div></li></ul>""")
    f = form(page, service, tmp_path, Profile(facts={"requires_sponsorship": False}))
    obs = await f.scan()
    [radio] = [x for x in obs["fields"] if x["type"] == "radio"]
    assert radio["label"] == "Will you now or in the future require visa sponsorship?"
    assert radio["required"]
    report = await f.fill_current()
    assert report["ok"], report
    assert await page.locator("input[value=No]").is_checked()


async def test_lever_and_ashby_required_markers(page, service, tmp_path):
    await page.set_content("""
      <div class="application-question"><div class="application-label">Why do you want to work here?✱</div>
        <textarea name="cards[x][field1]"></textarea></div>
      <div class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title _required_abc" for=q2>Portfolio notes</label>
        <textarea id=q2></textarea></div>""")
    obs = await form(page, service, tmp_path).scan()
    labels = {x["label"]: x["required"] for x in obs["fields"]}
    assert labels == {"Why do you want to work here?": True, "Portfolio notes": True}


async def test_ashby_autofill_pane_is_not_the_resume_field(page, service, tmp_path):
    (tmp_path / "Alex_Resume.pdf").write_bytes(b"%PDF-1.4 resume")
    await page.set_content("""
      <div class="ashby-application-form-autofill-pane"><h3>Autofill from resume</h3>
        <p>Upload your resume here to autofill key application fields.</p><input type=file id=autofill></div>
      <div class="ashby-application-form-field-entry"><label for=_systemfield_resume>Resume</label>
        <input type=file id=_systemfield_resume name=_systemfield_resume></div>""")
    f = form(page, service, tmp_path)
    await f.fill_current()
    assert await page.evaluate("document.getElementById('_systemfield_resume').files.length") == 1
    assert await page.evaluate("document.getElementById('autofill').files.length") == 0


async def test_required_cover_letter_file_stops_before_submit(page, service, tmp_path):
    (tmp_path / "Alex_Resume.pdf").write_bytes(b"%PDF-1.4 resume")
    await page.set_content("""<label for=r>Resume/CV</label><input type=file id=r name=resume>
      <label for=c>Cover Letter</label><input type=file id=c name=cover_letter required>""")
    report = await form(page, service, tmp_path).fill_current()
    assert not report["ok"]
    assert any("Required document missing: Cover Letter" in p for p in report["problems"])


def test_intents_never_answer_other_countries_current_salary_or_prose(service, config):
    p = Profile(facts={"work_authorized_us": True, "requires_sponsorship": False, "willing_onsite": True},
                salary_expectation="$150,000")
    r = Resolver(p, service.db, config)

    def ask(label, options=("Yes", "No"), kind="select"):
        return r.local({"id": "x", "label": label, "type": kind, "required": True, "options": list(options), "group": ""})

    assert ask("Are you legally authorized to work in Canada?") is None
    assert ask("Will you require visa sponsorship to work in the United Kingdom?") is None
    assert ask("What is your current base salary?", (), "text") is None
    assert ask("Describe a time you collaborated with a hybrid team", (), "textarea") is None
    assert ask("Are you legally authorized to work in the United States?").value == "Yes"


async def test_already_applied_footer_on_a_job_page_is_not_an_application(service, page, tmp_path):
    await page.set_content("<h1>Engineer</h1><p>Already applied? Sign in to check your application status.</p><a href='#'>Apply now</a>")
    adapter = Adapter(engine_for(service, page, tmp_path))
    assert adapter.classify(await adapter.form.scan()) == Step.JOB


async def test_submit_is_clicked_once_even_when_the_page_does_not_change(service, page, tmp_path, monkeypatch):
    await page.set_content(
        "<h1>Review</h1><label>Name<input value='Alex Example' required></label>"
        "<button type=button onclick='window.clicks=(window.clicks||0)+1'>Submit Application</button>"
    )
    monkeypatch.setattr(service, "reserve_submission", lambda run_id: None)
    adapter = Adapter(engine_for(service, page, tmp_path))
    final = adapter.find_final(await adapter.form.scan())
    first = await adapter.commit(final)
    again = await adapter.on_review(await adapter.form.scan())
    assert first["state"] == again["state"] == "submission_unknown"
    assert await page.evaluate("window.clicks") == 1


async def test_resume_rejected_on_an_earlier_page_blocks_the_dry_run(service, page, tmp_path):
    await page.set_content("<h1>Review</h1><button type=button>Submit Application</button>")
    adapter = Adapter(engine_for(service, page, tmp_path, mode="dry_run"))
    adapter.form.resume_seen, adapter.form.upload_verified = True, False
    result = await adapter.on_review(await adapter.form.scan(), {"problems": [], "pending": []})
    assert result["state"] == "needs_review" and "Resume" in result["reason"]


async def test_no_email_is_triggered_without_the_shared_sender_lease(service, page, tmp_path, monkeypatch):
    await page.set_content("<label>Email<input type=email></label><button type=button onclick='window.sent=1'>Continue</button>")
    engine = engine_for(service, page, tmp_path)
    adapter = Adapter(engine)
    monkeypatch.setattr(type(service.inbox), "configured", property(lambda self: True))
    monkeypatch.setattr(service.inbox, "arm", AsyncMock(side_effect=ValueError("Another application is verifying with this sender")))
    result = await adapter.on_email_entry(await adapter.form.scan())
    assert result["state"] == "timed_out"
    assert not await page.evaluate("window.sent")


def test_employer_domains_match_exactly_not_by_prefix():
    tokens = tenant_tokens("https://careers-healthedge.icims.com/jobs/1/x/job", "HealthEdge")
    assert sender_allowed("healthedge.com", [], tokens, True)
    assert not sender_allowed("healthedge-careers.com", [], tokens, True)
    assert trusted_sender("zip.co", {"company": "Zip Co", "url": ""})
    assert not trusted_sender("ziprecruiter.com", {"company": "Zip Co", "url": ""})
    assert not trusted_sender("stripe-careers-notify.com", {"company": "Stripe", "url": ""})
    assert not trusted_sender("app.com", {"company": "Applied Intuition", "url": ""})
    assert trusted_sender("myfundedfutures.com", {"company": "My Funded Futures", "url": ""})


async def test_untrusted_acknowledgement_does_not_hide_the_trusted_one(service, prepared):
    from jobpilot.receipts import check_receipts

    gmail = FakeGmail(EMAIL).connect(service)
    job, _ = add_job(service.db, JobInput(url="https://jobs.example.test/ramp", company="Ramp", title="AI Engineer"))
    with service.db.exclusive() as s:
        run = Run(job_id=job["id"], mode="submit", state="submission_unknown", packet={"profile": {"email": EMAIL}})
        s.add(run)
        s.flush()
        run_id = run.id
    body = "Thank you for applying to Ramp for the AI Engineer position. We received your application."
    gmail.deliver(EMAIL, "no-reply@ramp.com", "Thank you for applying to Ramp", body, received=time.time() + 1)
    gmail.deliver(EMAIL, "alerts@jobboard-mailer.test", "Thank you for applying to Ramp", body, received=time.time() + 2)
    await check_receipts(service.mail, run_id)
    with service.db.session() as s:
        row = s.get(Run, run_id)
        assert row.state == "confirmed" and row.receipt["email"]["from"] == "no-reply@ramp.com"
        assert row.receipt["email_untrusted"]["from"] == "alerts@jobboard-mailer.test"


async def test_an_expired_request_window_is_never_reused(service, prepared):
    from jobpilot.db import MailChallenge

    FakeGmail(EMAIL).connect(service)
    job, _ = add_job(service.db, JobInput(url="https://careers-acme.icims.com/jobs/1/x/job", company="Acme"))
    run = await service.queue(job["id"], resume_id=prepared["id"])
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "running"
    target = {"url": job["url"], "ats": "icims", "company": "Acme"}
    old = await service.inbox.arm(run["id"], target, EMAIL, "auto")
    with service.db.exclusive() as s:
        s.get(MailChallenge, old["id"]).expires = time.time() - 1  # Deadline passed, still pending.
    new = await service.inbox.arm(run["id"], target, EMAIL, "password_reset")
    assert new["id"] != old["id"] and new["kind"] == "password_reset"
    with service.db.session() as s:
        assert s.get(MailChallenge, old["id"]).state == "expired"


async def test_adapter_solves_a_challenge_that_opens_after_the_email_step(service, page, tmp_path):
    await page.set_content(
        "<label>Email<input type=email></label><button type=button onclick=\"document.body.insertAdjacentHTML('beforeend',"
        "'<iframe title=\\'hCaptcha challenge\\' srcdoc=\\'<p>Please click each image</p>\\' style=\\'width:400px;height:500px\\'></iframe>')\">Continue</button>"
    )
    engine = engine_for(service, page, tmp_path)
    engine.solve_captcha = AsyncMock(return_value={"state": "waiting_browser", "reason": "check"})
    adapter = Adapter(engine)
    result = await adapter.on_email_entry(await adapter.form.scan())
    assert result == {"state": "waiting_browser", "reason": "check"}
    engine.solve_captcha.assert_awaited()


async def test_visible_hcaptcha_checkbox_is_opened(page, config):
    from jobpilot.capsolver import CaptchaSolver

    await page.route("https://newassets.hcaptcha.com/**", lambda r: r.fulfill(content_type="text/html", body=(
        "<div id=checkbox role=checkbox onclick=\"parent.postMessage('passed','*')\">I am human</div>")))
    await page.set_content(
        '<iframe src="https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=checkbox" style="width:303px;height:78px"></iframe>'
        "<textarea name=h-captcha-response></textarea>"
        "<script>addEventListener('message',e=>{if(e.data==='passed')document.querySelector('textarea').value='P1_x'})</script>"
    )
    await page.frames[-1].wait_for_load_state()
    solver = CaptchaSolver(config, lambda *a: None)
    assert await solver.solve(page)
    assert await page.locator("textarea").input_value() == "P1_x"


def workday_engine(service, page, tmp_path, url):
    profile = Profile(email=EMAIL, allow_account_creation=True)
    f = FormSession(page, Resolver(profile, service.db, service.config), tmp_path / "r.pdf", lambda *a: None)
    return SimpleNamespace(
        job={"url": url}, page=page, form=f, db=service.db, service=service, config=service.config, run_id="r",
        email_verification=SimpleNamespace(rule={"link_origins": [url.rsplit("/job", 1)[0]]}, challenge_id=None,
                                           prepare_submission=AsyncMock(), state=lambda o: ("link", []),
                                           handle=AsyncMock(side_effect=AssertionError("handled the sign-in form")),
                                           close=lambda: None),
        emit=lambda *a: None,
    )


SIGN_IN = """<h2>Sign In</h2><div data-automation-id=errorMessage role=alert hidden></div>
<input data-automation-id=email><input type=password data-automation-id=password>
<button data-automation-id=signInSubmitButton onclick="window.clicks=(window.clicks||0)+1;
 const a=document.querySelector('[role=alert]');a.hidden=false;a.textContent=window.MESSAGE">Sign In</button>"""


async def test_workday_unverified_sign_in_reverifies_instead_of_mixed_error(service, page, tmp_path, monkeypatch):
    from jobpilot.accounts import AccountStore
    from jobpilot.workday import WorkdayAuth

    url = "https://acme.wd5.myworkdayjobs.com/External/job/X_R1"
    await page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=SIGN_IN))
    await page.goto(url)
    await page.evaluate("window.MESSAGE='Your account is not verified. Check your email for the verification link.'")
    store = AccountStore(service, EMAIL)
    store.save(url, store.shared(), "verification_pending")  # Left unverified by an earlier run.
    auth = WorkdayAuth(workday_engine(service, page, tmp_path, url))
    monkeypatch.setattr(auth, "recover", AsyncMock(side_effect=ValueError("recovery requested")))
    try:
        await auth.run()
    except ValueError as exc:
        assert "recovery requested" in str(exc)
    auth.recover.assert_awaited_once()
    assert await page.evaluate("window.clicks") == 1


async def test_workday_unrecognised_error_is_not_resubmitted(service, page, tmp_path):
    from jobpilot.accounts import AccountStore
    from jobpilot.workday import WorkdayAuth

    url = "https://acme.wd5.myworkdayjobs.com/External/job/X_R2"
    await page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=SIGN_IN))
    await page.goto(url)
    await page.evaluate("window.MESSAGE='Something unexpected happened. Error code 1234.'")
    store = AccountStore(service, EMAIL)
    store.save(url, store.shared(), "authenticated")
    auth = WorkdayAuth(workday_engine(service, page, tmp_path, url))
    try:
        await auth.run()
        raise AssertionError("expected a stop")
    except ValueError as exc:
        assert "Workday rejected the sign in" in str(exc)
    assert await page.evaluate("window.clicks") == 1


async def test_forgotten_or_corrected_answers_stay_gone(service, prepared):
    from jobpilot.answers import answer_key
    from jobpilot.db import Review
    from jobpilot.knowledge import forget, save

    service.db.set_setting("control", {"auto_submit": True, "paused": False})
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/kb-forget", company="Acme"))
    run = await service.queue(job["id"], "dry_run", prepared["id"])
    field = {"label": "Do you hold a security clearance?", "section": "", "options": ["Yes", "No"], "employer": "Acme"}
    service.complete(run["id"], {"state": "needs_review", "reviews": [
        {"question": field["label"], "options": field["options"], "key": answer_key(field)}]}, 1)
    with service.db.session() as s:
        review_id = s.query(Review).filter_by(run_id=run["id"]).one().id
    service.answer_review(review_id, "Yes")
    forget(service.db, review_id)
    profile = service.db.get_setting("profile")
    assert answer_key(field) not in profile["approved_answers"]
    with service.db.exclusive() as s:
        from jobpilot.db import Setting

        if flag := s.get(Setting, "migrated:reviewed_answers"):
            s.delete(flag)  # Even a forced re-migration must not bring it back.
    service.migrate_reviewed_answers()
    assert not [a for a in service.db.get_setting("profile")["reviewed_answers"] if a["id"] == review_id]
    # A correction replaces it everywhere too.
    save(service.db, field["label"], "No", field["options"], "Acme")
    assert [a["answer"] for a in service.db.get_setting("profile")["reviewed_answers"]] == ["No"]


async def test_answered_but_still_missing_question_is_asked_again(service, prepared):
    from jobpilot.db import Review

    job, _ = add_job(service.db, JobInput(url="https://example.test/job/re-ask", company="Acme"))
    run = await service.queue(job["id"], "dry_run", prepared["id"])
    with service.db.exclusive() as s:
        s.add(Review(run_id=run["id"], question="Notice period?", options=[], key="k1", answer="2 weeks"))
    service.complete(run["id"], {"state": "needs_review", "reviews": [{"question": "Notice period?", "options": [], "key": "k1"}]}, 1)
    with service.db.session() as s:
        rows = s.query(Review).filter_by(run_id=run["id"]).all()
        assert sorted(r.answer is None for r in rows) == [False, True]  # Re-asked, not stuck.


def test_changed_application_password_is_used_for_new_accounts(service):
    from jobpilot.accounts import AccountStore

    store = AccountStore(service, EMAIL)
    generated = store.shared()
    store.save("https://old.example.test", generated, "authenticated")
    service.config.application_password = "Chosen!Pass9x"
    assert store.shared()["password"] == "Chosen!Pass9x"
    assert store.get("https://old.example.test")["password"] == generated["password"]  # Kept until reset.


def test_address_facts_and_structured_address_stay_in_sync():
    from jobpilot.api import sync_address

    stored = {"facts": {"street_address": "1 Old St", "postal_code": "02139"}, "address": {"line1": "1 Old St", "postal_code": "02139"}}
    edited_facts = {"facts": {"street_address": "9 New Ave", "postal_code": "02139"}, "address": dict(stored["address"])}
    sync_address(stored, edited_facts)
    assert edited_facts["address"]["line1"] == "9 New Ave"
    edited_address = {"facts": dict(stored["facts"]), "address": {"line1": "5 Other Rd", "postal_code": "02139"}}
    sync_address(stored, edited_address)
    assert edited_address["facts"]["street_address"] == "5 Other Rd"
