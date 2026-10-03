from sqlalchemy import select

from jobpilot.db import Job, Review, Run
from jobpilot.telegram import ask_next_question, handle_update, notify_run
import pytest


async def waiting(service, prepared, title="Engineer"):
    profile = service.db.get_setting("profile")
    profile["autonomous"] = True
    service.db.set_setting("profile", profile)
    control = service.db.get_setting("control")
    control["auto_submit"] = True
    service.db.set_setting("control", control)
    with service.db.exclusive() as s:
        job = Job(
            identity=title,
            url="https://example.test/" + title,
            title=title,
            company="Example",
            status="ready",
            resume_id=prepared["id"],
        )
        s.add(job)
        s.flush()
        jid = job.id
    run = await service.queue(jid, "submit")
    service.complete(
        run["id"],
        {
            "state": "needs_review",
            "reviews": [
                {
                    "question": "Do you have an employment restriction?",
                    "options": ["Yes", "No"],
                    "key": "restriction-" + title,
                }
            ],
        },
        1,
    )
    return run["id"]


async def test_plain_telegram_reply_is_learned_and_application_resumes(
    service, config, prepared, monkeypatch
):
    config.telegram_user_id, config.telegram_bot_token = "123", "fixture-token"
    sent = []

    async def api(c, method, payload):
        sent.append(payload)
        return {"message_id": len(sent)}

    monkeypatch.setattr("jobpilot.telegram.api", api)
    rid = await waiting(service, prepared)
    await notify_run(service, rid)
    assert service.db.get_setting("telegram_question")["review_id"]
    with service.db.session() as s:
        assert s.get(Run, rid).state == "waiting_answer"
    result = await handle_update(service, {"message": {"from": {"id": 123}, "text": "No"}})
    assert "resumed automatically" in result
    assert service.db.get_setting("profile")["reviewed_answers"][-1]["answer"] == "No"
    with service.db.session() as s:
        assert len(list(s.scalars(select(Run).where(Run.state == "queued")))) == 1
    assert service.db.get_setting("telegram_question") == {}


async def test_only_one_question_is_active_and_stale_reply_cannot_answer_next(
    service, config, prepared, monkeypatch
):
    config.telegram_user_id, config.telegram_bot_token = "123", "fixture-token"
    sent = []

    async def api(c, method, payload):
        sent.append(payload)
        return {"message_id": len(sent)}

    monkeypatch.setattr("jobpilot.telegram.api", api)
    await waiting(service, prepared, "First")
    await waiting(service, prepared, "Second")
    await ask_next_question(service)
    await ask_next_question(service)
    assert len(sent) == 1
    first_message = service.db.get_setting("telegram_question")["message_id"]
    await handle_update(service, {"message": {"from": {"id": 123}, "text": "No"}})
    second = service.db.get_setting("telegram_question")["review_id"]
    result = await handle_update(
        service,
        {"message": {"from": {"id": 123}, "text": "Yes", "reply_to_message": {"message_id": first_message}}},
    )
    assert "already saved" in result
    with service.db.session() as s:
        assert s.get(Review, second).answer is None


def test_explicit_rejection_is_failed_even_after_commit(service):
    with service.db.exclusive() as s:
        job = Job(identity="rejected", url="https://example.test", status="submitting")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state="submitting")
        s.add(run)
        s.flush()
        rid = run.id
    service.complete(
        rid,
        {
            "state": "failed",
            "receipt": {"type": "explicit_rejection_page", "evidence": "Could not submit your application"},
        },
        1,
    )
    with service.db.session() as s:
        assert s.get(Run, rid).state == "failed"


async def test_spam_rejection_pauses_employer_and_prevents_repeated_attempt(service, prepared):
    with service.db.exclusive() as s:
        job = Job(
            identity="spam",
            url="https://jobs.ashbyhq.com/example/123",
            ats="ashby",
            status="ready",
            resume_id=prepared["id"],
        )
        s.add(job)
        s.flush()
        jid = job.id
        run = Run(job_id=jid, state="submitting")
        s.add(run)
        s.flush()
        rid = run.id
    service.complete(
        rid,
        {
            "state": "failed",
            "receipt": {"type": "explicit_rejection_page", "evidence": "Flagged as possible spam"},
        },
        1,
    )
    with pytest.raises(ValueError, match="paused for six hours"):
        await service.queue(jid)


async def test_browser_questions_queue_and_old_done_does_not_resume_another_job(
    service, config, prepared, monkeypatch
):
    config.telegram_user_id, config.telegram_bot_token = "123", "fixture-token"
    sent = []

    async def api(c, method, payload):
        sent.append(payload)
        return {"message_id": len(sent)}

    monkeypatch.setattr("jobpilot.telegram.api", api)
    ids = []
    for title in ["First browser", "Second browser"]:
        with service.db.exclusive() as s:
            job = Job(
                identity=title,
                url="https://example.test/" + title,
                title=title,
                company=title,
                resume_id=prepared["id"],
            )
            s.add(job)
            s.flush()
            run = Run(job_id=job.id, state="waiting_browser", resume_id=prepared["id"])
            s.add(run)
            s.flush()
            ids.append(run.id)
        await notify_run(service, ids[-1])
    first = service.db.get_setting("telegram_browser_question")
    assert first["run_id"] == ids[0]
    result = await handle_update(
        service,
        {
            "message": {
                "from": {"id": 123},
                "text": "done",
                "reply_to_message": {"message_id": first["message_id"]},
            }
        },
    )
    assert "continuing" in result
    assert service.db.get_setting("telegram_browser_question")["run_id"] == ids[1]
    result = await handle_update(
        service,
        {
            "message": {
                "from": {"id": 123},
                "text": "done",
                "reply_to_message": {"message_id": first["message_id"]},
            }
        },
    )
    assert "no longer waiting" in result
    with service.db.session() as s:
        assert len(list(s.scalars(select(Run).where(Run.state == "queued")))) == 1


async def test_conversation_does_not_become_a_free_text_profile_fact(service, config, prepared, monkeypatch):
    from jobpilot.telegram import ChatReply, ReplyInterpretation

    config.telegram_user_id, config.telegram_bot_token = "123", "fixture-token"

    async def api(*args):
        return {"message_id": 42}

    monkeypatch.setattr("jobpilot.telegram.api", api)
    rid = await waiting(service, prepared)
    with service.db.exclusive() as s:
        review = s.scalar(select(Review).where(Review.run_id == rid))
        review.options = []
        review_id = review.id

    async def structured(config, db, schema, *args):
        if schema is ReplyInterpretation:
            return ReplyInterpretation(kind="unrelated")
        return ChatReply(reply="The application is waiting for your answer.")

    monkeypatch.setattr("jobpilot.models.structured", structured)
    await ask_next_question(service)
    reply = await handle_update(
        service, {"message": {"from": {"id": 123}, "text": "Tell me how the applications are going"}}
    )
    assert reply == "The application is waiting for your answer."
    with service.db.session() as s:
        assert s.get(Review, review_id).answer is None
