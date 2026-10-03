"""Unit coverage for the autonomous engine's building blocks on the merged services."""

import base64
import json
import time
from unittest.mock import AsyncMock

import pytest

from jobpilot.accounts import Accounts
from jobpilot.adapters.icims import ICIMS
from jobpilot.adapters.workday import Workday
from jobpilot.answers import Resolver
from jobpilot.ats import detect, detect_embedded
from jobpilot.config import password_problems
from jobpilot.knowledge import KnowledgeBase, kb_norm, save
from jobpilot.mail import extract_message, tenant_tokens
from jobpilot.schemas import Profile
from jobpilot.widgets import best_option, parse_date, same_value
from mock_ats.gmail import FakeGmail
from test_browser import page as page  # noqa: F401  (shared Chromium fixture)

EMAIL = "alex@example.test"
WORKDAY = "https://acme.wd5.myworkdayjobs.com/External/job/X_R9"


def window(kind="auto", tokens=(), employer_senders=False):
    now = time.time()
    return {
        "recipient": EMAIL,
        "since": now - 30,
        "expires": now + 60,
        "kind": kind,
        "tenant_tokens": list(tokens),
        "employer_senders": employer_senders,
    }


def message(text, sender="acme@myworkday.com", link=None, link_text="Verify Account", to=EMAIL, auth=True):
    return FakeGmail(to).deliver(to, sender, "Verify your candidate account", text, link, link_text, auth)


WORKDAY_RULE = {"sender_domains": ["myworkday.com", "otp.workday.com"], "link_origins": ["https://acme.wd5.myworkdayjobs.com"]}


def test_link_extraction_ignores_untrusted_links():
    msg = message(
        "Thanks for creating an account.",
        link="https://acme.wd5.myworkdayjobs.com/External/activate/tok123",
    )
    assert extract_message(msg, WORKDAY_RULE, window("link", ["acme"])) == {
        "kind": "link",
        "value": "https://acme.wd5.myworkdayjobs.com/External/activate/tok123",
    }
    tracking = message("Thanks for creating an account.", link="https://track.example.net/activate/x")
    assert extract_message(tracking, WORKDAY_RULE, window("link", ["acme"])) is None


@pytest.mark.parametrize(
    "text,code",
    [
        ("Your verification code is 739104.", "739104"),
        ("482915 is your Acme security code", "482915"),
        ("Enter this code: A7K29Q to continue", "A7K29Q"),
        ("Use this verification code to continue your application with Acme: 551203. Posted 2024.", "551203"),
    ],
)
def test_code_extraction(text, code):
    rule = {"sender_domains": ["oraclecloud.com"], "link_origins": []}
    msg = message(text, sender="no-reply@us2.fa.oraclecloud.com")
    assert extract_message(msg, rule, window("code", ["ecsa", "acme"])) == {"kind": "code", "value": code}


def test_ambiguous_or_unauthenticated_codes_are_never_used():
    rule = {"sender_domains": ["oraclecloud.com"], "link_origins": []}
    assert extract_message(message("code 111111 or code 222222", sender="x@oraclecloud.com"), rule, window("code")) is None
    spoofed = message("Your verification code is 739104.", sender="x@oraclecloud.com", auth=False)
    assert extract_message(spoofed, rule, window("code")) is None


def test_recipient_must_match_exactly():
    msg = message("Your verification code is 739104.", to="alex+jobs@example.test")
    assert extract_message(msg, WORKDAY_RULE, window("code", ["acme"])) is None


def test_tenant_identity_on_shared_ats_senders():
    tokens = tenant_tokens(WORKDAY, "Acme")
    assert tokens == ["acme"]
    other = message("Your verification code is 739104.", sender="globex@myworkday.com")
    assert extract_message(other, WORKDAY_RULE, window("code", tokens)) is None
    ours = message("Your verification code is 739104.", sender="acme@myworkday.com")
    assert extract_message(ours, WORKDAY_RULE, window("code", tokens))["value"] == "739104"
    # Workday's shared OTP sender names no tenant; the shared-sender lease attributes it.
    shared = message("Your verification code is 739104.", sender="noreply@otp.workday.com")
    assert extract_message(shared, WORKDAY_RULE, window("code", tokens))["value"] == "739104"
    # Another Oracle tenant's pod is rejected; region labels are not tenants.
    rule = {"sender_domains": ["oraclecloud.com"], "link_origins": []}
    tokens = tenant_tokens("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/job/1", "JPMorgan Chase")
    assert extract_message(message("Your verification code is 739104.", sender="x@globex.fa.oraclecloud.com"), rule, window("code", tokens)) is None
    assert extract_message(message("Your verification code is 739104.", sender="x@us2.fa.oraclecloud.com"), rule, window("code", tokens))


def test_employer_owned_sender_needs_family_permission():
    rule = {"sender_domains": ["icims.com"], "link_origins": []}
    msg = message("Your verification code is 739104.", sender="careers@healthedge.com")
    tokens = tenant_tokens("https://careers-healthedge.icims.com/jobs/8601/machine-learning-engineer/job", "HealthEdge")
    assert extract_message(msg, rule, window("code", tokens)) is None
    assert extract_message(msg, rule, window("code", tokens, employer_senders=True))["value"] == "739104"
    lookalike = message("Your verification code is 739104.", sender="careers@evil-healthedge.com")
    assert extract_message(lookalike, rule, window("code", tokens, employer_senders=True)) is None


async def test_inbox_lease_serializes_shared_senders_and_consumes_once(service, prepared):
    from jobpilot.db import Job, Run
    from jobpilot.discovery import add_job
    from jobpilot.schemas import JobInput

    gmail = FakeGmail(EMAIL).connect(service)
    runs = []
    for host in ("acme.wd5", "globex.wd1"):
        job, _ = add_job(service.db, JobInput(url=f"https://{host}.myworkdayjobs.com/External/job/X_R9", company=host.split(".")[0].title()))
        run = await service.queue(job["id"], resume_id=prepared["id"])
        with service.db.session() as s:
            s.get(Run, run["id"]).state = "running"
            runs.append((run["id"], {"url": job["url"], "ats": "workday", "company": s.get(Job, job["id"]).company}))
    first = await service.inbox.arm(runs[0][0], runs[0][1], EMAIL, "link", since=time.time() - 5)
    assert first and first["rule"]["sender_domains"] == ["myworkday.com", "otp.workday.com"]
    with pytest.raises(ValueError, match="Another application is verifying"):
        # A second Workday employer waits for the lease instead of reading the same sender.
        await service.inbox.arm(runs[1][0], runs[1][1], EMAIL, "link", patience=0)
    gmail.deliver(EMAIL, "acme@myworkday.com", "Verify", "Verify your account.", "https://acme.wd5.myworkdayjobs.com/External/activate/y", "Verify Account")
    token = await service.inbox.wait(first)
    assert token["value"].endswith("/activate/y")
    service.inbox.finish(first, "verified", "Website accepted")
    service.config.mail_wait_seconds = 5
    second = await service.inbox.arm(runs[1][0], runs[1][1], EMAIL, "link", since=time.time() - 60)
    # The consumed message can never satisfy another challenge (and is Acme's, not Globex's).
    assert await service.inbox.wait(second) is None


def test_knowledge_scope_negation_and_options(service):
    save(service.db, "Do you have production experience with Kubernetes?", "Yes", ["Yes", "No"], "Globex")
    kb = KnowledgeBase(service.db)
    assert kb_norm("Have you previously worked for Acme Inc?", "Acme Inc") == "have you previously worked for employer"
    hit = kb.lookup("Do you have production experience with Kubernetes? *", ["Yes", "No"], "Acme", "radio")
    assert hit and hit[1] == "Yes"  # A personal fact transfers across employers.
    assert kb.lookup("Do you not have production experience with Kubernetes?", ["Yes", "No"], "Acme", "radio") is None
    assert kb.lookup("Do you have production experience with Kubernetes?", ["Never", "Once"], "Acme", "radio") is None
    # Relationships and motivations stay with their employer.
    save(service.db, "Have you previously worked for Globex?", "No", ["Yes", "No"], "Globex")
    save(service.db, "Why do you want to work at Globex?", "Because Globex builds X " * 5, [], "Globex")
    kb.refresh()
    assert kb.lookup("Have you previously worked for Acme?", ["Yes", "No"], "Acme", "radio") is None
    assert kb.lookup("Have you previously worked for Globex?", ["Yes", "No"], "Globex", "radio")
    assert kb.lookup("Why do you want to work at Initech?", [], "Initech", "textarea") is None
    assert kb.lookup("Why do you want to work at Globex?", [], "Globex", "textarea")


async def test_live_question_preempts_a_stale_one_and_answer_is_saved(service, prepared, monkeypatch):
    from jobpilot.db import Review, Run
    from jobpilot.discovery import add_job
    from jobpilot.knowledge import ask
    from jobpilot.schemas import JobInput
    from jobpilot.telegram import handle_update

    service.config.telegram_bot_token, service.config.telegram_user_id = "bot", "777"
    sent = []

    async def fake(c, method, payload):
        sent.append(payload)
        return {"message_id": len(sent)}

    monkeypatch.setattr("jobpilot.telegram.api", fake)
    old_job, _ = add_job(service.db, JobInput(url="https://example.test/job/old", company="Old"))
    old = await service.queue(old_job["id"], resume_id=prepared["id"])
    service.complete(old["id"], {"state": "needs_review", "reviews": [{"question": "Old question?", "options": [], "key": "old"}]}, 1)
    from jobpilot.telegram import ask_next_question

    await ask_next_question(service)
    assert "Old question?" in sent[-1]["text"]
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/live", company="Acme"))
    live = await service.queue(job["id"], resume_id=prepared["id"])
    with service.db.session() as s:
        s.get(Run, live["id"]).state = "running"
    service.config.mimo_api_key = ""  # Free text answers without interpretation.

    async def reply():
        import asyncio

        while not any("Years of Kubernetes experience?" in p["text"] for p in sent):
            await asyncio.sleep(0.05)
        await handle_update(service, {"message": {"from": {"id": 777}, "text": "3 years", "reply_to_message": {"message_id": len(sent)}}})

    import asyncio

    task = asyncio.create_task(reply())
    answered = await ask(service, live["id"], [{"question": "Years of Kubernetes experience?", "options": [], "key": "k8s-years"}], 5)
    await task
    assert answered == 1
    with service.db.session() as s:
        review = s.query(Review).filter_by(run_id=live["id"]).one()
        assert review.answer == "3 years"
    learned = [a for a in service.db.get_setting("profile")["reviewed_answers"] if a["question"] == "Years of Kubernetes experience?"]
    assert learned and learned[0]["answer"] == "3 years"


def test_option_matching_and_dates():
    assert best_option(["United States of America", "United Kingdom"], "USA") == "United States of America"
    assert best_option(["Alabama", "New York"], "NY") == "New York"
    assert best_option(["Bachelor's Degree", "Master's Degree"], "Master of Science") == "Master's Degree"
    assert best_option(["Canada (+1)", "United States (+1)"], "+1") is None  # ambiguous, never guessed
    assert best_option(["Mobile", "Home"], "Cell") == "Mobile"
    assert parse_date("2021-06") == ("06", "", "2021") and parse_date("06/2021") == ("06", "", "2021")
    assert same_value({"type": "tel", "label": "Phone"}, "(202) 555-0100", "2025550100")
    assert same_value({"type": "dropdown", "label": "Country"}, "United States of America", "USA")


def test_password_policy_origin_allowlist_and_shared_login(service):
    assert password_problems("Str0ng!Pass#1") == []
    assert "add a digit" in password_problems("NoDigits!here")
    accounts = Accounts(service, Profile(email="Alex@Example.test"))
    job = "https://acme.wd5.myworkdayjobs.com/External/job/X"
    assert accounts.password_allowed("workday", "https://acme.wd5.myworkdayjobs.com/External/login", job)
    assert accounts.password_allowed("workday", "https://wd5.myworkday.com/acme/login.htmld", job)
    assert not accounts.password_allowed("workday", "https://evil.example/login", job)
    # One generated login, reused for every employer, when APPLICATION_PASSWORD is unset.
    service.config.application_password = ""
    first = accounts.credentials("https://one.example.test")
    assert accounts.credentials("https://two.example.test") == first and first["email"] == "alex@example.test"
    assert not password_problems(first["password"])


def test_detection_and_entry_urls():
    assert detect("https://acme.eightfold.ai/careers?pid=1").id == "eightfold"
    assert detect("https://acme.taleo.net/careersection/2/jobdetail.ftl?job=1").id == "taleo"
    assert detect_embedded([("frame", "https://careers-acme.icims.com/jobs/1/x/job?in_iframe=1")])[0] == "icims"
    assert Workday.prepare_url("https://a.wd5.myworkdayjobs.com/en-US/X/job/NY/Eng_R1/apply/applyManually") == (
        "https://a.wd5.myworkdayjobs.com/en-US/X/job/NY/Eng_R1"
    )
    assert "in_iframe=1" in ICIMS.prepare_url("https://careers-acme.icims.com/jobs/1/eng/job")


def test_resolver_screening_intents_negation_and_source(config, service):
    p = Profile(facts={"requires_sponsorship": False, "work_authorized_us": True}, application_source="LinkedIn")
    r = Resolver(p, service.db, config)
    f = lambda label, options=("Yes", "No"), **extra: {  # noqa: E731
        "id": "x",
        "label": label,
        "type": "select",
        "required": True,
        "options": list(options),
        "group": "",
        **extra,
    }
    assert r.local(f("Will you now or in the future require visa sponsorship?")).value == "No"
    assert r.local(f("Are you legally authorized to work in the US?")).value == "Yes"
    # Negated phrasing needs interpretation; it is never mapped from a stored yes/no.
    assert r.local(f("Will you not require sponsorship?")) is None
    assert r.local(f("How did you hear about us?", ["Indeed", "LinkedIn"])).value == "LinkedIn"
    # Search-driven pickers receive the source itself; the widget finds the nested leaf.
    assert r.local(f("How Did You Hear About Us?", ["Job Boards", "Referral"], options_partial=True)).value == "LinkedIn"


def test_address_facts_migrate_into_the_structured_address():
    p = Profile.model_validate({"facts": {"street_address": "1 Main St", "zip_code": "10001", "city": "New York"},
                                "address": {"city": "Brooklyn"}, "unknown_future_key": {"kept": True}})
    assert (p.address.line1, p.address.postal_code, p.address.city) == ("1 Main St", "10001", "Brooklyn")
    assert p.model_dump()["unknown_future_key"] == {"kept": True}


async def test_invisible_recaptcha_and_turnstile_iframe_tasks(page, config, monkeypatch):
    from jobpilot.capsolver import CaptchaSolver

    config.capsolver_api_key = "fixture-key"
    await page.set_content(
        '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=site-key&size=invisible"></iframe>'
        '<textarea name="g-recaptcha-response"></textarea>'
    )
    tasks = []

    async def provider(base, key, task, *args):
        tasks.append(task)
        return None, "ERROR_CAPTCHA_UNSOLVABLE"

    monkeypatch.setattr("jobpilot.capsolver.task_result", provider)
    await CaptchaSolver(config, lambda *a: None).solve(page)
    assert tasks[-1]["type"] == "ReCaptchaV2TaskProxyLess" and tasks[-1]["isInvisible"] is True
    await page.set_content(
        '<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv0/0/0x4AAAAAAAtestkey/light/normal"></iframe>'
        '<input name="cf-turnstile-response" type="hidden">'
    )
    await CaptchaSolver(config, lambda *a: None).solve(page)
    assert tasks[-1]["type"] == "AntiTurnstileTaskProxyLess" and tasks[-1]["websiteKey"] == "0x4AAAAAAAtestkey"


HCAPTCHA = """<style>body{margin:0;font:14px Arial}.challenge-header{height:110px;background:#36c}
.challenge-example{width:90px;height:90px;background:#fc0}.task-grid{width:400px;height:300px;background:#ddd;position:relative}
.interface{height:60px}</style>
<div class=challenge-header><div class=prompt-text>Please click each image containing a bicycle</div><div class=challenge-example></div></div>
<div class=challenge-view><div class=task-grid id=grid></div></div>
<div class=interface><div class="button-submit" role=button>Next</div><div class="refresh button" aria-label="Refresh Challenge.">↻</div></div>
<script>
let round=0;const grid=document.getElementById('grid');
grid.addEventListener('click',e=>{const r=grid.getBoundingClientRect();
  parent.postMessage({x:Math.round(e.clientX-r.left),y:Math.round(e.clientY-r.top)},'*')});
document.querySelector('.button-submit').addEventListener('click',()=>{round++;
  if(round>=2){parent.postMessage('accepted','*')}else{grid.style.background='#aaa';document.querySelector('.button-submit').textContent='Verify'}});
</script>"""


async def test_hcaptcha_puzzle_crop_rounds_and_coordinate_transform(page, config, monkeypatch):
    from jobpilot.capsolver import CaptchaSolver, jpeg_size

    config.twocaptcha_api_key = "fixture-2captcha"
    config.captcha_max_rounds = 4
    await page.route("https://newassets.hcaptcha.example/**", lambda r: r.fulfill(content_type="text/html", body=HCAPTCHA))
    await page.set_content(
        '<p>PRIVATE APPLICATION</p><iframe title="hCaptcha challenge" src="https://newassets.hcaptcha.example/c" '
        'style="position:absolute;left:30px;top:40px;width:420px;height:500px;border:0"></iframe>'
        "<textarea name=h-captcha-response></textarea>"
        "<script>window.clicks=[];addEventListener('message',e=>{if(e.data==='accepted'){"
        "document.querySelector('textarea').value='P1_token';document.querySelector('iframe').remove()}"
        "else window.clicks.push(e.data)})</script>"
    )
    await page.frames[-1].wait_for_load_state()
    tasks = []

    async def provider(base, key, task, *args):
        image = base64.b64decode(task["body"])
        tasks.append({**task, "size": jpeg_size(image)})
        # Image pixel (100, 50) of the puzzle crop.
        return {"coordinates": [{"x": 100, "y": 50}]}, None

    monkeypatch.setattr("jobpilot.capsolver.task_result", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    assert await solver.solve(page), solver.last_reason
    assert len(tasks) == 2 and solver.rounds == 2  # Re-observed after the first round.
    assert tasks[0]["size"] == (400, 300)  # Only the puzzle area, not the header or control bar.
    assert "bicycle" in tasks[0]["comment"] and len(tasks[0]["comment"]) <= 140
    assert "imgInstructions" in tasks[0] and "PRIVATE APPLICATION" not in json.dumps(tasks)
    assert await page.evaluate("window.clicks") == [{"x": 100, "y": 50}, {"x": 100, "y": 50}]


async def test_hcaptcha_stale_and_out_of_bounds_solutions_are_rejected(page, config, monkeypatch):
    from jobpilot.capsolver import CaptchaSolver

    config.twocaptcha_api_key = "fixture-2captcha"
    await page.route("https://newassets.hcaptcha.example/**", lambda r: r.fulfill(content_type="text/html", body=HCAPTCHA))
    await page.set_content(
        '<iframe title="hCaptcha challenge" src="https://newassets.hcaptcha.example/c" style="width:420px;height:500px;border:0"></iframe>'
        "<script>window.clicks=[];addEventListener('message',e=>window.clicks.push(e.data))</script>"
    )
    frame = page.frames[-1]
    await frame.wait_for_load_state()
    monkeypatch.setattr("jobpilot.capsolver.task_result", AsyncMock(return_value=({"coordinates": [{"x": 100, "y": 320}]}, None)))
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page)
    assert "outside the puzzle" in solver.last_reason

    async def changing(*args):
        await frame.evaluate("document.getElementById('grid').style.background='#123'")
        return {"coordinates": [{"x": 10, "y": 10}]}, None

    monkeypatch.setattr("jobpilot.capsolver.task_result", changing)
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page)
    assert "changed while the solver was working" in solver.last_reason
    assert await page.evaluate("window.clicks") == []
    # Bounded: no more paid rounds than CAPTCHA_MAX_ROUNDS in one run.
    config.captcha_max_rounds = 1
    monkeypatch.setattr("jobpilot.capsolver.task_result", AsyncMock(return_value=({"coordinates": [{"x": 10, "y": 10}]}, None)))
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page)
    assert solver.rounds == 1 and "CAPTCHA_MAX_ROUNDS" in solver.last_reason
