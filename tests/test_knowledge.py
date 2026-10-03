import json

from jobpilot.answers import Resolver, answer_key
from jobpilot.db import Job, Review, Run
from jobpilot.discovery import add_job
from jobpilot.knowledge import learned_answer
from jobpilot.schemas import Answer, AnswerBatch, JobInput, Profile


def field(label, **values):
    return dict(id="f1", label=label, type="text", required=True, options=values.pop("options", []), **values)


async def test_personal_reviews_transfer_but_relationships_do_not(service, config):
    personal = learned_answer("1", "Have you been convicted?", "No", ["Yes", "No"], "Acme")
    relationship = learned_answer("2", "Are you related to an employee?", "No", ["Yes", "No"], "Acme")
    p = Profile(reviewed_answers=[personal, relationship])
    resolver = Resolver(p, service.db, config, employer="Other")
    assert resolver.local(field("Have you been convicted?", options=["No", "Yes"])).value == "No"
    assert resolver.local(field("Have you never been convicted?", options=["No", "Yes"])) is None
    assert resolver.local(field("Are you related to an employee?", options=["No", "Yes"])) is None


async def test_source_preference_and_consent_polarity(service, config):
    r = Resolver(
        Profile(application_source="LinkedIn", facts={"anti_corruption_violations": "No"}), service.db, config
    )
    assert (
        r.local(field("How did you hear about us?", options=["Referral", "LinkedIn", "Indeed"])).value
        == "LinkedIn"
    )
    assert r.local(field("Source")).value == "LinkedIn"
    assert r.local(field("Do you agree to our anti-corruption policy?", options=["Yes", "No"])) is None


async def test_narrative_has_job_context_and_unrelated_fact_cannot_prove_eligibility(
    service, config, monkeypatch
):
    config.mimo_api_key = "test"
    seen = []

    async def model(*args):
        seen.append(json.loads(args[4]))
        return AnswerBatch(
            answers=[
                Answer(
                    field_id="f1", value="No", disposition="answer", evidence_ids=["fact:conviction_history"]
                )
            ]
        )

    monkeypatch.setattr("jobpilot.answers.structured", model)
    r = Resolver(
        Profile(facts={"conviction_history": "No"}),
        service.db,
        config,
        employer="Acme",
        job={"title": "AI Engineer", "description": "Build search tools"},
    )
    answer = (await r.resolve([field("Will you require sponsorship?", options=["Yes", "No"])]))[0]
    assert answer.disposition == "review"
    assert seen[0]["job_context"]["description"] == "Build search tools"
    assert "confirmed_facts" in seen[0] and "experience_stories" in seen[0]


async def test_final_review_resumes_with_updated_profile_and_preserves_approval(service, prepared):
    service.db.set_setting("control", {"auto_submit": True, "paused": False})
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/knowledge", company="Acme"))
    with service.db.session() as s:
        s.get(Job, job["id"]).status = "ready"
    run = await service.queue(job["id"], "submit", prepared["id"])
    question = field("Have you been convicted?", options=["Yes", "No"], employer="Acme")
    service.complete(
        run["id"],
        {
            "state": "needs_review",
            "reviews": [
                {"question": question["label"], "options": question["options"], "key": answer_key(question)}
            ],
        },
        1,
    )
    with service.db.session() as s:
        review = s.query(Review).filter_by(run_id=run["id"]).one()
        review_id = review.id
    result = await service.resolve_review(review_id, "No")
    with service.db.session() as s:
        resumed = s.get(Run, result["run_id"])
        assert resumed.mode == "submit" and resumed.packet["match_approved"]
        assert resumed.packet["profile"]["reviewed_answers"][0]["answer"] == "No"
    service.migrate_reviewed_answers()
    assert len(service.db.get_setting("profile")["reviewed_answers"]) == 1


async def test_successful_dry_run_keeps_job_ready(service, prepared):
    job, _ = add_job(service.db, JobInput(url="https://example.test/job/dry-approved"))
    with service.db.session() as s:
        s.get(Job, job["id"]).status = "ready"
    run = await service.queue(job["id"], "dry_run", prepared["id"])
    service.complete(run["id"], {"state": "dry_run_passed"}, 1)
    with service.db.session() as s:
        assert s.get(Job, job["id"]).status == "ready"
