"""Unit coverage for the autonomous engine's building blocks."""

import time
from email.message import EmailMessage

import httpx
import pytest

from jobpilot.accounts import Accounts
from jobpilot.adapters.icims import ICIMS
from jobpilot.adapters.workday import Workday
from jobpilot.answers import Resolver
from jobpilot.ats import detect, detect_embedded
from jobpilot.config import password_problems
from jobpilot.inbox import Message, extract, parse_raw, same_mailbox
from jobpilot.knowledge import KnowledgeBase, kb_norm, match_reply, upsert
from jobpilot.schemas import Profile
from jobpilot.widgets import best_option, parse_date, same_value
from test_browser import page as page  # noqa: F401  (shared Chromium fixture)


def raw_email(html, sender="acme@myworkday.com", to="alex@example.test", auth="dmarc=pass"):
    msg = EmailMessage()
    msg["From"] = f"Acme Careers <{sender}>"
    msg["To"] = to
    msg["Subject"] = "Verify your candidate account"
    msg["Authentication-Results"] = f"mx.google.com; dkim=pass header.i=@{sender.split('@')[1]}; {auth}"
    msg.set_content("Open the HTML version of this email.")
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


def test_rfc822_parse_and_link_extraction():
    html = (
        '<p>Thanks for creating an account.</p><a href="https://track.example.net/x">Unsubscribe</a>'
        '<a href="https://acme.wd5.myworkdayjobs.com/External/activate/tok123">Verify Account</a>'
    )
    m = parse_raw(raw_email(html), "imap:1", received=time.time())
    assert m.sender == "acme@myworkday.com" and m.authenticated
    assert extract(m, "auto", ["acme.wd5.myworkdayjobs.com", "myworkday.com"]) == {
        "kind": "link",
        "value": "https://acme.wd5.myworkdayjobs.com/External/activate/tok123",
    }
    # A link to a host outside the employer/ATS is never followed.
    assert extract(m, "link", ["other.example"]) is None


@pytest.mark.parametrize(
    "text,code",
    [
        ("Your verification code is 739104.", "739104"),
        ("482915 is your Acme security code", "482915"),
        ("Enter this code: A7K29Q to continue", "A7K29Q"),
        ("Use this verification code to continue your application: 551203. Posted 2024.", "551203"),
    ],
)
def test_code_extraction(text, code):
    m = Message(key="k", received=0, sender="no-reply@oracle.com", recipients=[], subject="Code", text=text)
    assert extract(m, "code", []) == {"kind": "code", "value": code}


def test_ambiguous_codes_are_not_guessed():
    m = Message(
        key="k", received=0, sender="a@b.c", recipients=[], subject="", text="code 111111 or code 222222"
    )
    assert extract(m, "code", []) is None


def test_gmail_alias_matching():
    assert same_mailbox("a.lex+jobs@gmail.com", "alex@gmail.com")
    assert not same_mailbox("alex@example.test", "alex2@example.test")


async def test_inbox_correlates_employer_and_consumes_once(service):
    service.inbox.fake = [
        Message(
            "m1",
            time.time(),
            "other@myworkday.com",
            ["alex@example.test"],
            "Globex: verify",
            "Verify your Globex account",
            [("https://globex.wd1.myworkdayjobs.com/a/activate/x", "Verify")],
        ),
        Message(
            "m2",
            time.time(),
            "acme@myworkday.com",
            ["alex@example.test"],
            "Acme: verify",
            "Verify your Acme account",
            [("https://acme.wd5.myworkdayjobs.com/a/activate/y", "Verify")],
        ),
    ]
    kwargs = dict(
        run_id="r1",
        since=time.time() - 30,
        ats="workday",
        employer="Acme",
        host="acme.wd5.myworkdayjobs.com",
        kind="link",
        timeout=3,
        recipient="alex@example.test",
    )
    token = await service.inbox.wait(**kwargs)
    assert token["value"].endswith("/activate/y")
    # Consumed: the same message can never satisfy a second challenge.
    assert await service.inbox.wait(**{**kwargs, "timeout": 1}) is None


def test_knowledge_reuse_across_employers(service):
    upsert(service.db, "Have you previously worked for Globex?", "No", ["Yes", "No"], "Globex")
    kb = KnowledgeBase(service.db)
    assert (
        kb_norm("Have you previously worked for Acme Inc?", "Acme Inc")
        == "have you previously worked for employer"
    )
    hit = kb.lookup("Have you previously worked for Acme Inc?", ["Yes", "No"], "Acme Inc", "radio")
    assert hit and hit[1] == "No"
    # Option-compatible only: a free-text answer cannot be forced into an unrelated option set.
    assert (
        kb.lookup("Have you previously worked for Acme Inc?", ["Never", "Once"], "Acme Inc", "radio") is None
    )
    # Employer-specific prose stays with that employer.
    upsert(service.db, "Why do you want to work at Globex?", "Because Globex builds X " * 5, [], "Globex")
    kb.refresh()
    assert kb.lookup("Why do you want to work at Initech?", [], "Initech", "textarea") is None
    assert kb.lookup("Why do you want to work at Globex?", [], "Globex", "textarea")
    assert kb.similar("Did you ever work for Initech before?", "Initech")


def test_reply_matching():
    assert match_reply("2", ["Yes", "No"]) == "No"
    assert match_reply("yes", ["Yes", "No"]) == "Yes"
    assert match_reply("Five years of Python", []) == "Five years of Python"
    with pytest.raises(ValueError):
        match_reply("maybe", ["Yes", "No"])


async def test_telegram_reply_learns_and_requeues(service, prepared, monkeypatch):
    from sqlalchemy import select

    from jobpilot.db import Job, Review, Run, TelegramPrompt
    from jobpilot.discovery import add_job
    from jobpilot.schemas import JobInput
    from jobpilot.telegram import handle_update

    service.config.telegram_bot_token, service.config.telegram_user_id = "bot", "777"
    sent = []

    async def fake(c, method, payload):
        sent.append((method, payload))
        return {"message_id": 99}

    monkeypatch.setattr("jobpilot.telegram.api", fake)
    job, _ = add_job(
        service.db, JobInput(url="https://acme.wd5.myworkdayjobs.com/External/job/X_R9", company="Acme")
    )
    run = await service.queue(job["id"], resume_id=prepared["id"])
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        row.state = "needs_review"
        s.get(Job, job["id"]).status = "needs_review"
        review = Review(
            run_id=run["id"], question="Years of Kubernetes experience?", options=[], key="k8s-years"
        )
        s.add(review)
        s.flush()
        s.add(TelegramPrompt(message_id="99", review_id=review.id))
    queued = []

    async def dispatch(run_id):
        queued.append(run_id)

    service.dispatch = dispatch
    reply = await handle_update(
        service, {"message": {"from": {"id": 777}, "text": "3 years", "reply_to_message": {"message_id": 99}}}
    )
    assert "Saved" in reply
    assert (
        KnowledgeBase(service.db).lookup("Years of Kubernetes experience?", [], "Other", "text")[1]
        == "3 years"
    )
    with service.db.session() as s:
        runs = list(s.scalars(select(Run).where(Run.job_id == job["id"])))
    assert len(runs) == 2 and queued == [max(runs, key=lambda r: r.created_at).id]


def test_option_matching_and_dates():
    assert best_option(["United States of America", "United Kingdom"], "USA") == "United States of America"
    assert best_option(["Alabama", "New York"], "NY") == "New York"
    assert best_option(["Bachelor's Degree", "Master's Degree"], "Master of Science") == "Master's Degree"
    assert best_option(["Canada (+1)", "United States (+1)"], "+1") is None  # ambiguous, never guessed
    assert best_option(["Mobile", "Home"], "Cell") == "Mobile"
    assert parse_date("2021-06") == ("06", "", "2021") and parse_date("06/2021") == ("06", "", "2021")
    assert same_value({"type": "tel", "label": "Phone"}, "(202) 555-0100", "2025550100")
    assert same_value({"type": "dropdown", "label": "Country"}, "United States of America", "USA")


def test_password_policy_and_origin_allowlist(service):
    assert password_problems("Str0ng!Pass#1") == []
    assert "add a digit" in password_problems("NoDigits!here")
    accounts = Accounts(service)
    job = "https://acme.wd5.myworkdayjobs.com/External/job/X"
    assert accounts.password_allowed("workday", "https://acme.wd5.myworkdayjobs.com/External/login", job)
    assert accounts.password_allowed("workday", "https://wd5.myworkday.com/acme/login.htmld", job)
    assert not accounts.password_allowed("workday", "https://evil.example/login", job)
    assert Accounts.realm("successfactors", "https://career4.successfactors.com/career?company=acme") == (
        "successfactors:career4.successfactors.com:acme"
    )


def test_detection_and_entry_urls():
    assert detect("https://acme.eightfold.ai/careers?pid=1").id == "eightfold"
    assert detect("https://acme.taleo.net/careersection/2/jobdetail.ftl?job=1").id == "taleo"
    assert (
        detect_embedded([("frame", "https://careers-acme.icims.com/jobs/1/x/job?in_iframe=1")])[0] == "icims"
    )
    assert Workday.prepare_url(
        "https://a.wd5.myworkdayjobs.com/en-US/X/job/NY/Eng_R1/apply/applyManually"
    ) == ("https://a.wd5.myworkdayjobs.com/en-US/X/job/NY/Eng_R1")
    assert "in_iframe=1" in ICIMS.prepare_url("https://careers-acme.icims.com/jobs/1/eng/job")


def test_resolver_screening_intents_and_negation(config, service):
    p = Profile(facts={"requires_sponsorship": False, "work_authorized_us": True}, referral_source="LinkedIn")
    r = Resolver(p, service.db, config)
    f = lambda label, options=("Yes", "No"): {  # noqa: E731
        "id": "x",
        "label": label,
        "type": "select",
        "required": True,
        "options": list(options),
        "group": "",
    }
    assert r.local(f("Will you now or in the future require visa sponsorship?")).value == "No"
    assert r.local(f("Are you legally authorized to work in the US?")).value == "Yes"
    # Negated phrasing needs interpretation; it is never mapped from a stored yes/no.
    assert r.local(f("Will you not require sponsorship?")) is None
    assert r.local(f("How did you hear about us?", ["Indeed", "LinkedIn"])).value == "LinkedIn"


async def test_captcha_detection_and_token_injection(page, config, monkeypatch):
    from jobpilot import capsolver

    await page.set_content(
        """<form><div class="g-recaptcha" data-sitekey="site-key-123" data-callback="onToken"></div>
        <textarea name="g-recaptcha-response" style="display:none"></textarea></form>
        <script>window.got = null; function onToken(t) { window.got = t; }</script>"""
    )
    items = await capsolver.detect(page)
    assert items[0]["vendor"] == "recaptcha" and items[0]["sitekey"] == "site-key-123"
    assert capsolver.task_for(items[0], "https://x.test")["type"] == "ReCaptchaV2TaskProxyLess"
    config.capsolver_api_key = "test-key"
    calls = []

    async def respond(request):
        import json

        body = json.loads(request.content)
        calls.append((request.url.path, body.get("task", {}).get("type")))
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "t1"})
        return httpx.Response(
            200, json={"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "TOKEN-1"}}
        )

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(capsolver.httpx, "AsyncClient", client)
    result = await capsolver.solve(config, page, lambda *a: None)
    assert result["solved"]
    assert await page.evaluate("document.querySelector('[name=g-recaptcha-response]').value") == "TOKEN-1"
    assert await page.evaluate("window.got") == "TOKEN-1"
    assert calls[0] == ("/createTask", "ReCaptchaV2TaskProxyLess")


async def test_captcha_detected_from_iframe_urls(page):
    from jobpilot import capsolver

    await page.set_content(
        '<iframe src="https://www.google.com/recaptcha/enterprise/anchor?k=ent-key&size=invisible&sa=submit"></iframe>'
        '<div class="cf-turnstile" data-sitekey="0x4AAAAAAAtest"></div>'
    )
    items = {i["vendor"]: i for i in await capsolver.detect(page)}
    assert items["recaptcha"]["enterprise"] and items["recaptcha"]["invisible"]
    assert capsolver.task_for(items["recaptcha"], "u")["type"] == "ReCaptchaV2EnterpriseTaskProxyLess"
    assert capsolver.task_for(items["turnstile"], "u")["websiteKey"] == "0x4AAAAAAAtest"
    assert capsolver.is_blocking(list(items.values())) == [items["turnstile"]]
