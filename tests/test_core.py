import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from jobpilot.answers import Resolver, answer_key
from jobpilot.ats import canonicalize, detect
from jobpilot.db import Budget, Job, Review, Run
from jobpilot.discovery import add_job
from jobpilot.models import BudgetExceeded, MeteredTransport
from jobpilot.network import public_url
from jobpilot.schemas import Answer, AnswerBatch, JobInput, Profile


def field(label="Email", **extra):
    return {
        "id": "f1",
        "label": label,
        "type": "text",
        "required": True,
        "options": [],
        "section": "",
        **extra,
    }


@pytest.mark.parametrize(
    "domain,ats",
    [
        ("boards.greenhouse.io", "greenhouse"),
        ("jobs.ashbyhq.com", "ashby"),
        ("x.wd5.myworkdayjobs.com", "workday"),
        ("careers.icims.com", "icims"),
        ("x.oraclecloud.com", "oracle"),
        ("jobs.smartrecruiters.com", "smartrecruiters"),
        ("greenhouse.io.evil.test", "custom"),
    ],
)
def test_ats_boundaries(domain, ats):
    assert detect("https://" + domain + "/role").id == ats


def test_identity_preserves_functional_routes():
    a = canonicalize("https://jobs.lever.co/acme/123/apply?utm_source=x")
    b = canonicalize("https://jobs.lever.co/acme/123")
    assert a[1] == b[1]
    assert "jobId=123" in canonicalize("https://example.test/careers?jobId=123&utm_medium=x#/apply")[0]
    assert (
        canonicalize("https://x.oraclecloud.com/#/job/1")[1]
        != canonicalize("https://x.oraclecloud.com/#/job/2")[1]
    )
    with pytest.raises(ValueError):
        canonicalize("https://api.smartrecruiters.com/v1/companies/acme/postings/123")
    assert (
        canonicalize("https://boards.greenhouse.io/acme/jobs/123")[1]
        == canonicalize("https://job-boards.greenhouse.io/acme/jobs/123")[1]
    )
    assert (
        canonicalize("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/NY/Engineer_R123")[1]
        == canonicalize("https://acme.wd5.myworkdayjobs.com/Careers/job/NY/Engineer_R123/apply")[1]
    )


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "file:///etc/passwd"],
)
async def test_private_destinations_rejected(url):
    with pytest.raises(ValueError):
        await public_url(url)


async def test_evidence_and_polarity(config, service):
    p = Profile(name="Alex Example", email="alex@example.test")
    r = Resolver(p, service.db, config)
    questions = [
        field(),
        field("Have you ever been convicted of an offense?", id="legal", options=["Yes", "No"]),
        field("Do you agree to follow our anti-corruption policy?", id="commit", options=["Yes", "No"]),
        field("Gender", id="dem", options=["Woman", "Man", "Prefer not to identify"]),
    ]
    answers = {a.field_id: a for a in await r.resolve(questions)}
    assert answers["f1"].value == "alex@example.test"
    assert answers["legal"].disposition == answers["commit"].disposition == "review"
    assert answers["dem"].value == "Prefer not to identify"
    p.approved_answers[answer_key(questions[1])] = "No"
    assert r.local(questions[1]).value == "No"
    assert r.local({**questions[1], "label": "Have you never been convicted of an offense?"}) is None
    assert Resolver(p, service.db, config, employer="Other company").local(questions[1]) is None


async def test_unbacked_llm_answer_is_rejected(config, service, monkeypatch):
    config.mimo_api_key = "test"

    async def fake(*args, **kwargs):
        return AnswerBatch(
            answers=[Answer(field_id="f1", disposition="answer", value="No", evidence_ids=["imaginary"])]
        )

    monkeypatch.setattr("jobpilot.answers.structured", fake)
    result = await Resolver(Profile(), service.db, config).resolve(
        [field("Need sponsorship?", options=["Yes", "No"])]
    )
    assert result[0].disposition == "review"


def test_api_auth_and_origin(app, client):
    from fastapi.testclient import TestClient

    outsider = TestClient(app)
    assert outsider.get("/api/snapshot").status_code == 401
    assert outsider.post("/api/login", json={"token": "wrong"}).status_code == 401
    assert (
        outsider.post(
            "/api/login", json={"token": "test-access-token"}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    response = outsider.post("/api/login", json={"token": "test-access-token"})
    assert response.status_code == 200
    assert (
        "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    )
    assert outsider.get("/api/snapshot").status_code == 200
    assert (
        client.put(
            "/api/control",
            json={"paused": True, "auto_submit": True},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert "test-access-token" not in client.get("/api/snapshot").text


def test_pdf_validation_and_dedup(client, resume_pdf, prepared):
    assert (
        client.post(
            "/api/resumes", data={"name": "Bad"}, files={"file": ("bad.pdf", b"not a pdf")}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/resumes", data={"name": "Duplicate"}, files={"file": ("other.pdf", resume_pdf)}
        ).status_code
        == 400
    )
    response = client.get("/api/resumes/" + prepared["id"] + "/download")
    assert response.content == resume_pdf


async def test_job_and_run_idempotency(service, prepared):
    job, created = add_job(service.db, JobInput(url="https://jobs.lever.co/example/123"))
    dup, other = add_job(service.db, JobInput(url="https://jobs.lever.co/example/123/apply?utm_source=mail"))
    assert created and not other and job["id"] == dup["id"]
    results = await asyncio.gather(
        service.queue(job["id"], resume_id=prepared["id"]),
        service.queue(job["id"], resume_id=prepared["id"]),
        return_exceptions=True,
    )
    assert sum(isinstance(x, ValueError) for x in results) == 1
    run = next(x for x in results if isinstance(x, dict))
    service.db.set_setting("profile", {"name": "Changed"})
    assert run["packet"]["profile"]["name"] == "Alex Example"


async def test_duplicate_delivery_does_not_interrupt_active_run(service, prepared):
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/123"))
    run = await service.queue(job["id"], resume_id=prepared["id"])
    with service.db.session() as s:
        s.get(Run, run["id"]).state = "running"
    await service.execute(run["id"])
    with service.db.session() as s:
        assert s.get(Run, run["id"]).state == "running"


async def test_commit_reservation_is_atomic_and_unknown_blocks_retry(service, prepared):
    service.config.daily_application_limit = 1
    service.db.set_setting("control", {"auto_submit": True, "paused": False})
    ids = []
    for i in range(2):
        job, _ = add_job(service.db, JobInput(url=f"https://example.test/job/{i}"))
        with service.db.session() as s:
            s.get(Job, job["id"]).status = "ready"
        run = await service.queue(job["id"], "submit", prepared["id"])
        with service.db.session() as s:
            s.get(Run, run["id"]).state = "running"
        ids.append(run["id"])

    def reserve(i):
        try:
            service.reserve_submission(i)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(reserve, ids))
    assert sorted(results) == [False, True]
    winner = ids[results.index(True)]
    service.complete(winner, {"state": "failed", "reason": "Disconnected after click"}, 2)
    with service.db.session() as s:
        r = s.get(Run, winner)
        assert r.state == "submission_unknown"
        job_id = r.job_id
    with pytest.raises(ValueError, match="uncertain"):
        await service.queue(job_id, resume_id=prepared["id"])
    service.reconcile(winner, False, "Employer portal shows no application")
    assert (await service.queue(job_id, resume_id=prepared["id"]))["state"] == "queued"


async def test_pause_prevents_commit(service, prepared):
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/1"))
    run = await service.queue(job["id"], resume_id=prepared["id"])
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        row.state = "running"
        row.mode = "submit"
    service.db.set_setting("control", {"paused": True, "auto_submit": True})
    with pytest.raises(ValueError, match="paused"):
        service.reserve_submission(run["id"])


async def test_shared_model_meter_and_mimo_json_mode(service, config):
    seen = []

    async def respond(request):
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    config.daily_budget_usd = 0.01
    transport = MeteredTransport(service.db, config, inner=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(
            "https://provider.test/v1/chat/completions",
            json={
                "max_tokens": 40,
                "response_format": {"type": "json_schema", "json_schema": {}},
                "reasoning_effort": "high",
                "messages": [],
            },
        )
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["max_completion_tokens"] == 40 and "max_tokens" not in seen[0]
    assert "reasoning_effort" not in seen[0]
    day = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    with service.db.session() as s:
        assert s.get(Budget, day).spent == pytest.approx(
            (100 * config.mimo_input_price + 20 * config.mimo_output_price) / 1_000_000
        )
    config.daily_budget_usd = 0.00000001
    async with httpx.AsyncClient(
        transport=MeteredTransport(service.db, config, inner=httpx.MockTransport(respond))
    ) as client:
        with pytest.raises(BudgetExceeded):
            await client.post("https://provider.test/v1/chat/completions", json={"messages": []})


async def test_telegram_allowlist(service, config, monkeypatch):
    from jobpilot.telegram import handle_update

    config.telegram_user_id = "12345"
    sent = []

    async def fake(c, m, p):
        sent.append(p)

    monkeypatch.setattr("jobpilot.telegram.api", fake)
    await handle_update(service, {"message": {"from": {"id": 999}, "text": "/pause"}})
    assert not service.db.get_setting("control")["paused"] and not sent
    await handle_update(service, {"message": {"from": {"id": 12345}, "text": "/pause"}})
    assert service.db.get_setting("control")["paused"] and len(sent) == 1


def test_review_scope_and_options(service):
    f = field("Referral?", options=["Yes", "No"], section="Acme")
    with service.db.session() as s:
        r = Review(run_id="fixture", question=f["label"], options=f["options"], key=answer_key(f))
        s.add(r)
        s.flush()
        rid = r.id
    with pytest.raises(ValueError):
        service.answer_review(rid, "maybe")
    service.answer_review(rid, "No")
    assert service.db.get_setting("profile")["approved_answers"][answer_key(f)] == "No"
    with pytest.raises(ValueError):
        service.answer_review(rid, "Yes")


async def test_pydanticai_mimo_structured_output(service, config, monkeypatch):
    import json
    from jobpilot.models import structured
    from jobpilot.schemas import FitResult

    config.mimo_api_key = "unit-test-key"
    seen = []

    async def provider(request):
        seen.append(json.loads(request.content))
        answer = {
            "score": 85,
            "eligible": True,
            "resume_id": "existing-id",
            "reason": "Relevant evidence",
            "uncertainties": ["Sponsorship unknown"],
        }
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "created": 0,
                "object": "chat.completion",
                "model": config.mimo_model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(answer)},
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 35, "total_tokens": 115},
            },
        )

    def client(db, settings, run_id=None):
        return httpx.AsyncClient(
            transport=MeteredTransport(db, settings, run_id, httpx.MockTransport(provider))
        )

    monkeypatch.setattr("jobpilot.models.http_client", client)
    result = await structured(config, service.db, FitResult, "Return a JSON classification.", "Fixture role")
    assert result.score == 85 and result.uncertainties == ["Sponsorship unknown"]
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["max_completion_tokens"] == 2400


async def test_startup_recovery_never_replays_a_commit(service, prepared):
    from jobpilot.workers import recover_interrupted

    ids = []
    for index, state in enumerate(["running", "submitting", "queued"]):
        job, _ = add_job(service.db, JobInput(url=f"https://example.test/recovery/{index}"))
        run = await service.queue(job["id"], resume_id=prepared["id"])
        with service.db.session() as s:
            s.get(Run, run["id"]).state = state
        ids.append(run["id"])
    recover_interrupted(service)
    with service.db.session() as s:
        assert [s.get(Run, i).state for i in ids] == ["needs_review", "submission_unknown", "queued"]
