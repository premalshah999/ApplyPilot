"""Synthetic Gmail responses only; no real mailbox, account, or message is accessed."""

import base64
import copy
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from jobpilot.db import Mailbox, MailChallenge, MailOAuth, Run, now, record
from jobpilot.mail import API_URL, SCOPE, TOKEN_URL, RuleInput, extract_message


def message(body="Your verification code is 483921", *, html=False, message_id="message-1", received=None):
    return {
        "id": message_id,
        "internalDate": str(int((received or time.time()) * 1000)),
        "payload": {
            "mimeType": "text/html" if html else "text/plain",
            "headers": [
                {"name": "From", "value": "Example Careers <no-reply@notify.example.test>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {
                    "name": "Authentication-Results",
                    "value": "mx.google.com; dkim=pass; dmarc=pass header.from=notify.example.test",
                },
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def seed(service, employer="https://careers.example.test", *, run_id="a" * 32):
    mail = service.mail
    with service.db.exclusive() as s:
        box = Mailbox(
            email="alex@example.test",
            credentials=mail.vault.seal(
                {
                    "access_token": "fake-access-secret",
                    "refresh_token": "fake-refresh-secret",
                    "expires_at": time.time() + 3600,
                }
            ),
        )
        run = Run(
            id=run_id,
            job_id="b" * 32,
            state="running",
            started_at=now(),
            packet={"profile": {"email": "alex@example.test"}},
        )
        s.add_all([box, run])
        s.flush()
        return record(box), record(run)


def test_greenhouse_security_code_email_with_footer_numbers():
    msg = message(
        "Copy and paste this code into the security code field on your application: a1BcDeFG After you enter the code, resubmit your application. © 2026 Greenhouse New York NY 10013"
    )
    token = extract_message(
        msg,
        {"sender_domains": ["notify.example.test"], "link_origins": []},
        {
            "recipient": "alex@example.test",
            "since": time.time() - 30,
            "expires": time.time() + 30,
            "kind": "code",
        },
    )
    assert token == {"kind": "code", "value": "a1BcDeFG"}


def test_password_reset_is_scoped_to_explicit_challenge_and_trusted_origin():
    msg = message(
        '<a href="https://careers.example.test/reset?token=secret">Reset your password</a>', html=True
    )
    rule = {"sender_domains": ["notify.example.test"], "link_origins": ["https://careers.example.test"]}
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": "password_reset",
    }
    assert extract_message(msg, rule, challenge)["kind"] == "link"
    assert extract_message(msg, rule, {**challenge, "kind": "auto"}) is None
    assert extract_message(msg, {**rule, "link_origins": ["https://another.example.test"]}, challenge) is None


async def configured(service, **kwargs):
    box, run = seed(service, **kwargs)
    rule = await service.mail.save_rule(
        RuleInput(
            mailbox_id=box["id"],
            employer_origin=kwargs.get("employer", "https://careers.example.test"),
            sender_domains=["notify.example.test"],
        )
    )
    return box, run, rule


def provider(messages):
    def route(request):
        assert request.headers["authorization"] == "Bearer fake-access-secret"
        if request.url.path.endswith("/messages"):
            assert "after:" in request.url.params["q"] and "to:alex@example.test" in request.url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        return httpx.Response(200, json=next(m for m in messages if request.url.path.endswith("/" + m["id"])))

    return httpx.MockTransport(route)


async def test_oauth_browser_binding_refresh_encryption_and_replay(service):
    mail, cfg = service.mail, service.config
    cfg.google_client_id, cfg.google_client_secret = "fake-client", "fake-client-secret"
    authorization, binding = mail.begin_oauth()
    params = parse_qs(urlsplit(authorization).query)
    assert params["scope"] == [SCOPE] and params["code_challenge_method"] == ["S256"]
    state = params["state"][0]
    with service.db.session() as s:
        pending = s.scalar(select(MailOAuth))
        assert state not in json.dumps(record(pending)) and binding not in json.dumps(record(pending))
    with pytest.raises(ValueError, match="another browser"):
        await mail.finish_oauth(state, "wrong-browser", "fake-auth-code")
    calls = []

    def route(request):
        calls.append(request)
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["client_secret"] == ["fake-client-secret"]
            if form["grant_type"] == ["authorization_code"]:
                assert form["redirect_uri"] == [mail.callback_url] and len(form["code_verifier"][0]) >= 43
                return httpx.Response(
                    200,
                    json={
                        "scope": SCOPE,
                        "access_token": "fake-access-secret",
                        "refresh_token": "fake-refresh-secret",
                        "expires_in": 1,
                    },
                )
            assert form["refresh_token"] == ["fake-refresh-secret"]
            return httpx.Response(200, json={"access_token": "new-access-secret", "expires_in": 3600})
        assert str(request.url) == API_URL + "/profile"
        return httpx.Response(200, json={"emailAddress": "alex@example.test"})

    mail.transport = httpx.MockTransport(route)
    assert await mail.finish_oauth(state, binding, "fake-auth-code") == "alex@example.test"
    with pytest.raises(ValueError, match="expired"):
        await mail.finish_oauth(state, binding, "fake-auth-code")
    box = mail.summary()["mailboxes"][0]
    assert "fake-" not in json.dumps(mail.summary())
    with service.db.session() as s:
        encrypted = s.get(Mailbox, box["id"]).credentials
        assert (
            "fake-access" not in encrypted
            and mail.vault.open(encrypted)["refresh_token"] == "fake-refresh-secret"
        )
    await mail.request(box["id"], "/profile")
    assert calls[-1].headers["authorization"] == "Bearer new-access-secret"
    assert (cfg.data_dir / "mail-key").stat().st_mode & 0o777 == 0o600


async def test_mail_api_auth_callback_and_secret_redaction(app, config):
    mail = app.state.service.mail
    config.google_client_id, config.google_client_secret = "fake", "fake"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=config.base_url) as client:
        assert (await client.get("/api/mail")).status_code == 401
        client.headers["authorization"] = "Bearer " + config.app_token
        assert (
            await client.post("/api/mail/connect", headers={"Origin": "https://attacker.test"}, json={})
        ).status_code == 403
        response = await client.post("/api/mail/connect", json={})
        assert response.status_code == 200 and "HttpOnly" in response.headers["set-cookie"]
        state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
        mail.transport = httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "scope": SCOPE,
                    "access_token": "fake-access-secret",
                    "refresh_token": "fake-refresh-secret",
                    "expires_in": 3600,
                }
                if str(r.url) == TOKEN_URL
                else {"emailAddress": "alex@example.test"},
            )
        )
        callback = await client.get(
            "/oauth/gmail/callback", params={"state": state, "code": "fake-auth-code"}
        )
        assert callback.status_code == 303 and callback.headers["location"].endswith("?gmail=connected")
        status = await client.get("/api/mail")
        assert "fake-access" not in status.text and "fake-refresh" not in status.text
        callback = await client.get(
            "/oauth/gmail/callback", params={"state": state, "code": "fake-auth-code"}
        )
        assert callback.headers["location"].endswith("?gmail=connection_failed")


@pytest.mark.parametrize(
    "mutation",
    [
        "sender",
        "recipient",
        "stale",
        "future",
        "dmarc",
        "spoofed_auth",
        "ambiguous_codes",
        "unrelated",
        "nested_host",
        "duplicate_sender",
    ],
)
def test_message_correlation_rejects_untrusted_inputs(mutation):
    start = time.time()
    rule = {"sender_domains": ["notify.example.test"], "link_origins": ["https://careers.example.test"]}
    challenge = {"recipient": "alex@example.test", "since": start - 10, "expires": start + 60, "kind": "auto"}
    msg = message()
    if mutation == "sender":
        msg["payload"]["headers"][0]["value"] = "attacker@evil.test"
    if mutation == "recipient":
        msg["payload"]["headers"][1]["value"] = "someone@example.test"
    if mutation == "stale":
        msg["internalDate"] = str(int((start - 20) * 1000))
    if mutation == "future":
        msg["internalDate"] = str(int((start + 80) * 1000))
    if mutation == "dmarc":
        msg["payload"]["headers"][2]["value"] = "mx.google.com; dmarc=fail header.from=notify.example.test"
    if mutation == "spoofed_auth":
        msg["payload"]["headers"][2]["value"] = "evil.test; dmarc=pass header.from=notify.example.test"
    if mutation == "duplicate_sender":
        msg["payload"]["headers"].append({"name": "From", "value": "attacker@evil.test"})
    if mutation == "ambiguous_codes":
        msg = message("Code: 483921. Code: 193810.")
    if mutation == "unrelated":
        msg = message("Thanks for applying. Your application number is 483921.")
    if mutation == "nested_host":
        msg = message(
            '<a href="https://careers.example.test.evil.test/verify?token=secret">Verify email</a>', html=True
        )
    assert extract_message(msg, rule, challenge) is None


def test_html_multipart_link_and_code_extraction():
    rule = {"sender_domains": ["notify.example.test"], "link_origins": ["https://careers.example.test"]}
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 10,
        "expires": time.time() + 60,
        "kind": "auto",
    }
    msg = message("<p>Your verification code: <b>483921</b></p>", html=True)
    assert extract_message(msg, rule, challenge) == {"kind": "code", "value": "483921"}
    url = "https://careers.example.test/verify?token=fixture-secret"
    msg = message(f'<a href="{url}">Confirm email</a>', html=True)
    assert extract_message(msg, rule, challenge) == {"kind": "link", "value": url}
    part = copy.deepcopy(msg["payload"])
    msg["payload"].update(mimeType="multipart/alternative", body={}, parts=[part, part])
    assert extract_message(msg, rule, challenge)["value"] == url


async def test_message_claim_is_one_use_and_ambiguous_sender_jobs_are_serialized(service):
    box, run, rule = await configured(service)
    mail = service.mail
    challenge = mail.begin(run["id"], rule, "alex@example.test", since=time.time() - 1)
    with service.db.exclusive() as s:
        s.add(Run(id="c" * 32, job_id="d" * 32, state="running"))
    with pytest.raises(ValueError, match="Another application"):
        mail.begin("c" * 32, rule, "alex@example.test")
    mail.transport = provider([message()])
    await mail.poll_once()
    with service.db.session() as s:
        row = s.get(MailChallenge, challenge["id"])
        assert row.state == "matched" and "483921" not in row.payload
    assert "483921" not in json.dumps(mail.summary())
    assert await mail.wait(challenge["id"]) == {"kind": "code", "value": "483921"}
    assert mail.claim(challenge["id"]) is None
    mail.outcome(challenge["id"], "verified")
    other = mail.begin("c" * 32, rule, "alex@example.test", since=time.time() - 1)
    await mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailChallenge, other["id"]).state == "pending"


async def test_ambiguity_expiry_disconnect_and_invalid_refresh(service):
    box, run, rule = await configured(service)
    mail = service.mail
    c = mail.begin(run["id"], rule, "alex@example.test", since=time.time() - 1)
    mail.transport = provider([message(), message(message_id="message-2")])
    await mail.poll_once()
    assert mail.summary()["challenges"][0]["state"] == "failed"
    c = mail.begin(run["id"], rule, "alex@example.test")
    with service.db.exclusive() as s:
        s.get(MailChallenge, c["id"]).expires = time.time() - 1
    await mail.poll_once()
    with pytest.raises(ValueError):
        await mail.wait(c["id"])
    c = mail.begin(run["id"], rule, "alex@example.test")
    with service.db.exclusive() as s:
        row = s.get(Mailbox, box["id"])
        token = mail.vault.open(row.credentials)
        token["expires_at"] = 0
        row.credentials = mail.vault.seal(token)
    mail.transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    await mail.poll_once()
    assert mail.summary()["mailboxes"][0]["state"] == "reconnect_required"
    result = await mail.disconnect(box["id"])
    assert result == {"disconnected": True, "revoked": False}
    assert mail.summary()["mailboxes"] == [] and mail.summary()["rules"] == []
    with service.db.session() as s:
        assert not s.get(MailChallenge, c["id"]).payload


async def test_rule_exact_host_path_and_no_wildcards(service):
    box, _, rule = await configured(service, employer="https://careers.example.test/company-a")
    mail = service.mail
    assert mail.rule_for("https://careers.example.test/company-a/job/1")["id"] == rule["id"]
    for url in [
        "https://careers.example.test/company-ab/job/1",
        "https://careers.example.test/company-b/job/1",
        "https://careers.example.test.evil.test/company-a",
    ]:
        assert mail.rule_for(url) is None
    with pytest.raises(ValueError, match="exact sender"):
        await mail.save_rule(
            RuleInput(
                mailbox_id=box["id"],
                employer_origin="https://careers.example.test",
                sender_domains=["*.example.test"],
            )
        )


@pytest.mark.parametrize(
    "body",
    [
        "Your one-time passcode is 483921",
        "483921 is your verification code",
        "Your verification code below:\n483921",
    ],
)
def test_common_code_layouts(body):
    rule = {"sender_domains": ["notify.example.test"], "link_origins": []}
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 1,
        "expires": time.time() + 60,
        "kind": "code",
    }
    assert extract_message(message(body), rule, challenge) == {"kind": "code", "value": "483921"}


async def test_mail_rule_and_cancel_endpoints(app, config):
    box, run, rule = await configured(app.state.service)
    mail = app.state.service.mail
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url=config.base_url,
        headers={"Authorization": "Bearer " + config.app_token},
    ) as client:
        c = mail.begin(run["id"], rule, "alex@example.test")
        assert (await client.post("/api/mail-challenges/" + c["id"] + "/cancel", json={})).status_code == 200
        # A delayed browser result cannot overwrite applicant cancellation.
        mail.outcome(c["id"], "verified")
        assert mail.summary()["challenges"][0]["state"] == "cancelled"
        data = {
            "mailbox_id": box["id"],
            "employer_origin": "https://another.example.test",
            "sender_domains": ["notify.example.test"],
            "link_origins": [],
        }
        response = await client.post("/api/mail-rules", json=data)
        assert response.status_code == 200
        assert (await client.delete("/api/mail-rules/" + response.json()["id"])).status_code == 200
        assert len(mail.summary()["rules"]) == 1


def test_lower_authentication_header_cannot_override_receiving_failure():
    msg = message()
    msg["payload"]["headers"].insert(
        0,
        {
            "name": "Authentication-Results",
            "value": "mx.google.com; dmarc=fail header.from=notify.example.test",
        },
    )
    rule = {"sender_domains": ["notify.example.test"], "link_origins": []}
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 1,
        "expires": time.time() + 60,
        "kind": "code",
    }
    assert extract_message(msg, rule, challenge) is None


async def test_unknown_ats_mail_rule_learned_from_authenticated_employer_link(service):
    seed(service)
    msg = message(
        '<p>Your verification code is 483921</p><a href="https://careers.example.test/verify?token=secret">Verify email</a>',
        html=True,
    )
    service.mail.transport = provider([msg])
    rule = await service.mail.discover_rule(
        {"url": "https://careers.example.test/job/123"}, "alex@example.test", time.time() - 30
    )
    assert rule["sender_domains"] == ["notify.example.test"]
    assert rule["link_origins"] == ["https://careers.example.test"]


async def test_unknown_ats_mail_cannot_learn_from_unrelated_or_spoofed_messages(service):
    seed(service)
    job = {"url": "https://careers.example.test/job/123"}
    for msg in [
        message(),
        message('<a href="https://other.example.test/verify?token=secret">Verify</a>', html=True),
    ]:
        service.mail.transport = provider([msg])
        assert await service.mail.discover_rule(job, "alex@example.test", time.time() - 30) is None
    msg = message('<a href="https://careers.example.test/verify?token=secret">Verify email</a>', html=True)
    msg["payload"]["headers"][2]["value"] = "mx.google.com; dmarc=fail header.from=notify.example.test"
    service.mail.transport = provider([msg])
    assert await service.mail.discover_rule(job, "alex@example.test", time.time() - 30) is None
