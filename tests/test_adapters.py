"""End-to-end adapter runs against high-fidelity mock ATS sites. No employer is ever contacted."""

import json
import os

import pytest
from sqlalchemy import select

from jobpilot.accounts import AccountStore
from jobpilot.db import Event, Job, Run
from jobpilot.discovery import add_job
from jobpilot.knowledge import save
from jobpilot.schemas import JobInput
from mock_ats import MockICIMS, MockOracle, MockWorkday, Router
from mock_ats.gmail import FakeGmail
from mock_ats.portals import MockEmbeddedAccount, MockSuccessFactors, MockTaleo

PASSWORD = "Str0ng!Pass#1"
EMAIL = "alex@example.test"
PROFILE = {
    "name": "Alex Example",
    "email": EMAIL,
    "phone": "2025550100",
    "location": "New York, NY",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "first_name": "Alex",
    "last_name": "Example",
    "address": {
        "line1": "1 Main St",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "United States of America",
    },
    "work": [
        {
            "company": "Globex",
            "title": "Backend Engineer",
            "location": "New York, NY",
            "start": "2021-06",
            "current": True,
            "description": "Built Python services on Kubernetes.",
        }
    ],
    "education": [
        {
            "school": "State University",
            "degree": "Master of Science",
            "field_of_study": "Computer Science",
            "start": "2019",
            "end": "2021",
        }
    ],
    "allow_account_creation": True,
    "accept_all_application_terms": True,
    "application_source": "LinkedIn",
    "facts": {
        "work_authorized_us": True,
        "requires_sponsorship": False,
        "over_18": True,
        "previously_employed_here": False,
        "has_non_compete": False,
    },
}


@pytest.fixture
async def harness(service, config, prepared, monkeypatch):
    import asyncio

    calls = []

    async def no_model(*args, **kwargs):
        """A cautious model: it never invents an answer, so unknown questions come back as review."""
        from jobpilot.schemas import Answer, AnswerBatch

        questions = json.loads(args[4])["questions"]
        calls.append(questions)
        if os.getenv("ADAPTER_DEBUG"):
            print("MODEL", questions)
        return AnswerBatch(
            answers=[Answer(field_id=q["id"], disposition="review", reason="No evidence") for q in questions]
        )

    monkeypatch.setattr("jobpilot.answers.structured", no_model)
    if os.getenv("ADAPTER_DEBUG"):
        import traceback

        from jobpilot.browser import BrowserEngine

        original = BrowserEngine.run

        async def traced(self, *a):
            try:
                return await original(self, *a)
            except Exception:
                traceback.print_exc()
                raise

        monkeypatch.setattr(BrowserEngine, "run", traced)
    inbox = FakeGmail(EMAIL).connect(service)
    config.application_password = PASSWORD
    config.account_email = EMAIL
    config.mail_wait_seconds = 45  # Windows open before the triggering click; allow for load.
    config.mail_poll_seconds = 0.2
    config.multipage_timeout = 240
    service.db.set_setting("profile", PROFILE)
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    # Learned on Telegram while applying to another employer; reused here without asking again.
    save(service.db, "Do you have production experience with Kubernetes?", "Yes", ["Yes", "No"], "Globex")
    # The background mailbox poller, as the app runs it.
    poller = asyncio.create_task(service.mail.poll())

    async def run(site, url, mode="dry_run"):
        service.fixture_router = Router(site)
        job, _ = add_job(service.db, JobInput(url=url, title="Software Engineer", company="Acme"))
        with service.db.session() as s:
            s.get(Job, job["id"]).status = "ready"
        queued = await service.queue(job["id"], mode, prepared["id"])
        await service.execute(queued["id"])
        with service.db.session() as s:
            row = s.get(Run, queued["id"])
            events = [(e.kind, e.message) for e in s.scalars(select(Event).where(Event.run_id == row.id))]
            result = {
                "state": row.state,
                "reason": row.reason,
                "receipt": row.receipt,
                "elapsed": row.elapsed,
                "events": events,
                "id": row.id,
            }
        if os.getenv("ADAPTER_DEBUG"):
            from jobpilot.db import Review

            with service.db.session() as s:
                for r in s.scalars(select(Review).where(Review.run_id == row.id)):
                    print("REVIEW", r.question, r.options[:6], r.reason)
            with service.db.session() as s:
                for e in s.scalars(select(Event).where(Event.run_id == row.id)):
                    print(
                        "EVENT",
                        e.kind,
                        e.message,
                        json.dumps(e.data)[:600] if e.kind in {"verification", "field_error"} else "",
                    )
            for step in sorted((service.config.data_dir / "runs" / row.id / "steps").glob("*.json")):
                data = json.loads(step.read_text())
                print("STEP", step.name, data["headings"][:3], data["errors"][:3])
                for f in data["fields"]:
                    print("   F", f)
        return result

    run.inbox = inbox
    run.calls = calls
    yield run
    poller.cancel()
    await asyncio.gather(poller, return_exceptions=True)


def account_state(service, url):
    record = service.db.get_setting(AccountStore(service, EMAIL).key(url), {})
    return record.get("state")


async def test_workday_new_account_email_verification_and_submit(harness, service):
    site = MockWorkday(harness.inbox)
    result = await harness(site, MockWorkday.job_url, mode="submit")
    assert result["state"] == "confirmed", result
    assert harness.calls == []
    assert site.accounts[EMAIL]["verified"] and site.accounts[EMAIL]["password"] == PASSWORD
    assert account_state(service, MockWorkday.job_url) == "authenticated"
    [submission] = site.submissions
    v = submission["values"]
    expected = {
        "firstName": "Alex",
        "lastName": "Example",
        "country": "United States of America",
        "state": "New York",
        "city": "New York",
        "postal": "10001",
        "phoneType": "Mobile",
        "phoneCode": "United States of America (+1)",
        "phone": "2025550100",
        "source": "LinkedIn",
        "previousWorker": "No",
        "authorized": "Yes",
        "sponsorship": "No",
        "adult": "Yes",
        "k8s": "Yes",
        "gender": "I do not wish to answer",
        "ethnicity": "I do not wish to answer",
        "veteran": "I do not wish to self-identify",
        "terms": "true",
        "sigName": "Alex Example",
        "disability": "I do not want to answer",
        "w1_title": "Backend Engineer",
        "w1_company": "Globex",
        "w1_location": "New York, NY",
        "w1_from": "06/2021",
        "w1_current": "true",
        "e1_school": "State University",
        "e1_degree": "Master's Degree",
        "e1_to": "2021",
        "linkedin": "https://www.linkedin.com/in/alex-example",
    }
    assert {k: v.get(k) for k in expected} == expected
    assert submission["resume"] == "Alex_Example_Resume.pdf"
    assert result["receipt"]["type"] == "explicit_confirmation_page"
    assert result["elapsed"] < 150, result["elapsed"]


async def test_workday_existing_account_signs_in_and_dry_run_never_submits(harness, service):
    site = MockWorkday(harness.inbox)
    site.add_account(EMAIL, PASSWORD)
    result = await harness(site, MockWorkday.job_url + "/apply")
    assert result["state"] == "dry_run_passed", result
    assert site.submissions == [] and harness.calls == []
    assert len(site.saves) == 5  # every page before Review was saved, nothing submitted


async def test_workday_old_password_is_reset_through_the_mailbox(harness, service):
    site = MockWorkday(harness.inbox)
    site.add_account(EMAIL, "OldPassw0rd!")
    result = await harness(site, MockWorkday.job_url)
    if os.getenv("ADAPTER_DEBUG"):
        print("ACCOUNTS", site.accounts, site.requests)
    assert result["state"] == "dry_run_passed", result
    assert site.accounts[EMAIL]["password"] == PASSWORD
    assert account_state(service, MockWorkday.job_url) in {"authenticated", "password_reset"}
    assert any("password reset" in m.lower() for _, m in result["events"])


MOTIVATION = "What interests you most about this role?"


async def test_oracle_new_candidate_full_submit(harness, service):
    # A narrative confirmed for this employer earlier.
    save(service.db, MOTIVATION, "Building reliable platforms that other engineers depend on.", [], "Acme")
    site = MockOracle(harness.inbox)
    result = await harness(site, MockOracle.job_url, mode="submit")
    assert result["state"] == "confirmed", result
    assert harness.calls == []
    [submission] = site.submissions
    v = submission["values"]
    expected = {
        "email": EMAIL,
        "firstName": "Alex",
        "lastName": "Example",
        "phoneCountry": "United States (+1)",
        "phone": "2025550100",
        "country": "United States",
        "address1": "1 Main St",
        "city": "New York",
        "state": "New York",
        "zip": "10001",
        "authorized": "Yes",
        "sponsorship": "No",
        "noncompete": "No",
        "motivation": "Building reliable platforms that other engineers depend on.",
        "signature": "Alex Example",
        "esign": "true",
    }
    assert {k: v.get(k) for k in expected} == expected
    assert submission["resume"] == "Alex_Example_Resume.pdf"


async def test_oracle_returning_candidate_pin_and_live_telegram_answer(harness, service, config, monkeypatch):
    import asyncio

    from jobpilot.telegram import handle_update

    config.telegram_bot_token, config.telegram_user_id = "fixture-bot", "12345"
    config.telegram_wait_seconds = 30
    sent, tasks = [], []

    async def reply_later(message_id):
        await asyncio.sleep(0.5)
        await handle_update(
            service,
            {
                "message": {
                    "from": {"id": 12345},
                    "text": "Owning reliability for systems people rely on.",
                    "reply_to_message": {"message_id": message_id},
                }
            },
        )

    async def fake_api(cfg, method, payload):
        sent.append((method, payload))
        if method == "sendMessage" and MOTIVATION in payload["text"]:
            tasks.append(asyncio.create_task(reply_later(len(sent))))
        return {"message_id": len(sent)}

    monkeypatch.setattr("jobpilot.telegram.api", fake_api)

    async def interpret(config, db, schema, instructions, prompt, *args, **kwargs):
        # The reply-interpretation model: the applicant's own words, quoted exactly.
        from jobpilot.telegram import ReplyInterpretation

        assert schema is ReplyInterpretation
        reply = json.loads(prompt)["applicant_reply"]
        return ReplyInterpretation(kind="answer", answer=reply, quote=reply)

    monkeypatch.setattr("jobpilot.models.structured", interpret)
    site = MockOracle(harness.inbox)
    site.add_account(EMAIL, PASSWORD)
    result = await harness(site, MockOracle.job_url)
    assert result["state"] == "dry_run_passed", result
    # The model declined (no evidence); the question went to Telegram instead of being guessed.
    assert [q["label"] for batch in harness.calls for q in batch] == [MOTIVATION]
    assert site.submissions == []
    assert any(kind == "email_verified" for kind, _ in result["events"])
    assert any(kind == "answered" for kind, _ in result["events"])
    from jobpilot.knowledge import KnowledgeBase

    kb = KnowledgeBase(service.db)
    hit = kb.lookup(MOTIVATION, [], "Acme", "text")
    assert hit and hit[1] == "Owning reliability for systems people rely on."
    # A motivation is employer-specific: another employer gets it only as writing context.
    assert kb.lookup(MOTIVATION, [], "Another Corp", "text") is None
    assert kb.similar(MOTIVATION, "Another Corp") == []


async def test_icims_new_account_hidden_selects_and_submit(harness, service):
    site = MockICIMS(harness.inbox)
    result = await harness(site, MockICIMS.job_url, mode="submit")
    assert result["state"] == "confirmed", result
    assert harness.calls == []
    assert site.accounts[EMAIL]["password"] == PASSWORD
    assert site.accounts[EMAIL]["names"] == ("Alex", "Example")
    assert site.uploads[0]["resume"] == "Alex_Example_Resume.pdf"
    [profile] = site.saves
    assert {
        k: profile[k] for k in ("first", "last", "phone", "address", "city", "state", "zip", "education")
    } == {
        "first": "Alex",
        "last": "Example",
        "phone": "2025550100",
        "address": "1 Main St",
        "city": "New York",
        "state": "New York",
        "zip": "10001",
        "education": "Master's",
    }
    [submission] = site.submissions
    assert {k: submission[k] for k in ("authorized", "sponsorship", "source", "k8s")} == {
        "authorized": "Yes",
        "sponsorship": "No",
        "source": "LinkedIn",
        "k8s": "Yes",
    }


async def test_icims_wrong_password_is_reset_from_email(harness, service):
    site = MockICIMS(harness.inbox)
    site.add_account(EMAIL, "Different1!")
    site.accounts[EMAIL]["names"] = ("Alex", "Example")
    result = await harness(site, MockICIMS.job_url)
    assert result["state"] == "dry_run_passed", result
    assert site.accounts[EMAIL]["password"] == PASSWORD
    assert site.submissions == []


async def test_taleo_new_user_privacy_agreement_and_submit(harness, service):
    site = MockTaleo(harness.inbox)
    result = await harness(site, MockTaleo.job_url, mode="submit")
    assert result["state"] == "confirmed", result
    assert harness.calls == []
    assert site.accounts[EMAIL]["password"] == PASSWORD  # the shared email doubles as the user name
    resume, personal, questions = site.saves
    assert resume["resume"] == "Alex_Example_Resume.pdf"
    assert {k: personal[k] for k in ("first", "last", "email", "phone", "country", "city")} == {
        "first": "Alex",
        "last": "Example",
        "email": EMAIL,
        "phone": "2025550100",
        "country": "United States",
        "city": "New York",
    }
    assert questions["authorized"] == "Yes" and questions["sponsorship"] == "No" and questions["k8s"] == "Yes"
    assert len(site.submissions) == 1


async def test_successfactors_account_then_long_form_dry_run(harness, service):
    site = MockSuccessFactors(harness.inbox)
    result = await harness(site, MockSuccessFactors.job_url)
    assert result["state"] == "dry_run_passed", result
    assert harness.calls == [] and site.submissions == []
    assert site.accounts[EMAIL]["password"] == PASSWORD


async def test_application_form_with_embedded_password_never_submits_in_dry_run(harness, service):
    site = MockEmbeddedAccount(harness.inbox)
    result = await harness(site, MockEmbeddedAccount.job_url)
    assert result["state"] == "dry_run_passed", result
    assert site.submissions == []
    assert all(method == "GET" for method, _ in site.requests)


# ----- regressions: untrusted verification email and false confirmations ---------------------
class DecoyOracle(MockOracle):
    """Before the genuine PIN email, the mailbox receives look-alike messages a careless matcher
    would accept: spoofed (DMARC fail), a look-alike domain, another recipient, another tenant."""

    def __init__(self, inbox, genuine=True):
        super().__init__(inbox)
        self.genuine, self.pin_attempts = genuine, []

    def mail(self, to, sender, subject, text, link=None, link_text="Verify"):
        self.inbox.deliver(to, sender, subject, "Your verification code is 111111.", authenticated=False)
        self.inbox.deliver(
            to, "no-reply@oraclecloud.com.evil.test", subject, "Your verification code is 222222."
        )
        self.inbox.deliver("someone@example.test", sender, subject, "Your verification code is 333333.")
        self.inbox.deliver(
            to, "no-reply@globex.fa.oraclecloud.com", subject, "Your verification code is 444444."
        )
        if self.genuine:
            super().mail(to, sender, subject, text, link, link_text)

    def api(self, name, body, query):
        if name == "pin":
            self.pin_attempts.append(body.get("pin"))
        return super().api(name, body, query)


async def test_regression_decoy_codes_are_never_entered(harness, service):
    save(service.db, MOTIVATION, "Building reliable platforms that other engineers depend on.", [], "Acme")
    site = DecoyOracle(harness.inbox)
    site.add_account(EMAIL, PASSWORD)
    result = await harness(site, MockOracle.job_url)
    assert result["state"] == "dry_run_passed", result
    assert len(site.pin_attempts) == 1 and site.pin_attempts[0] not in {
        "111111",
        "222222",
        "333333",
        "444444",
    }
    assert site.submissions == []


async def test_regression_only_untrusted_codes_means_no_code_and_no_guess(harness, service):
    save(service.db, MOTIVATION, "Building reliable platforms that other engineers depend on.", [], "Acme")
    service.config.mail_wait_seconds = 6
    site = DecoyOracle(harness.inbox, genuine=False)
    site.add_account(EMAIL, PASSWORD)
    result = await harness(site, MockOracle.job_url)
    assert result["state"] == "needs_review", result
    assert "did not arrive or was not unique" in result["reason"]
    assert site.pin_attempts == [] and site.submissions == []


class OtherTenantWorkday(MockWorkday):
    """Another Workday tenant's activation email (same shared sender) arrives first."""

    def __init__(self, inbox, genuine=True):
        super().__init__(inbox)
        self.genuine = genuine

    def mail(self, to, sender, subject, text, link=None, link_text="Verify"):
        self.inbox.deliver(
            to,
            "globex@myworkday.com",
            "Globex - Verify your candidate account",
            "Please verify your email to activate your account.",
            "https://globex.wd1.myworkdayjobs.com/en-US/External/activate/foreign",
            "Verify Account",
        )
        if self.genuine:
            super().mail(to, sender, subject, text, link, link_text)


async def test_regression_another_tenants_link_is_never_opened(harness, service):
    site = OtherTenantWorkday(harness.inbox)
    result = await harness(site, MockWorkday.job_url)
    assert result["state"] == "dry_run_passed", result
    assert site.accounts[EMAIL]["verified"]


async def test_regression_only_another_tenants_link_leaves_the_account_unverified(harness, service):
    service.config.mail_wait_seconds = 6
    site = OtherTenantWorkday(harness.inbox, genuine=False)
    result = await harness(site, MockWorkday.job_url)
    assert result["state"] not in {"dry_run_passed", "confirmed"}, result
    assert site.accounts[EMAIL]["verified"] is False  # Created, never activated by the foreign link.
    assert all(token[0] == "activate" for token in site.tokens.values())  # Our link is still unused.


async def test_owned_submit_runs_record_exactly_one_submission_and_one_receipt(harness, service):
    site = MockICIMS(harness.inbox)
    result = await harness(site, MockICIMS.job_url, mode="submit")
    assert result["state"] == "confirmed", result
    assert len(site.submissions) == 1
    receipt = result["receipt"]
    assert receipt["type"] == "explicit_confirmation_page" and receipt["website_confirmed"] is True
    assert receipt["email_confirmed"] is False  # No acknowledgement email in this fixture.
    with service.db.session() as s:
        assert s.scalar(select(Run).where(Run.id == result["id"])).state == "confirmed"
