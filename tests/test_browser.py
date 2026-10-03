"""Owned fixtures only. These tests never apply to a real employer."""

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.async_api import async_playwright

from jobpilot.answers import Resolver, answer_key
from jobpilot.api import create_app
from jobpilot.browser import GUARD
from jobpilot.config import Settings
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


@pytest.fixture
async def page(config):
    async with async_playwright() as pw:
        path = config.chromium_path or pw.chromium.executable_path
        if not Path(path).exists():
            pytest.skip("Install Chromium: playwright install chromium")
        browser = await pw.chromium.launch(executable_path=path, headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        yield page
        await browser.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = os.getenv("CHROMIUM_PATH") or pw.chromium.executable_path
    if not Path(browser).exists():
        pytest.skip("Install Chromium: playwright install chromium")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    config = Settings(
        _env_file=None,
        gmail_address="",
        gmail_app_password="",
        telegram_bot_token="",
        telegram_user_id="",
        mimo_api_key="",
        google_client_id="",
        google_client_secret="",
        browser_cdp_url="",
        data_dir=tmp_path_factory.mktemp("integration"),
        app_token="synthetic-fixture-access",
        base_url=url,
        chromium_path=browser,
        enable_workers=True,
        application_timeout=60,
    ).prepare()
    app = create_app(config)
    from fastapi import Request
    from fastapi.responses import HTMLResponse
    from jobpilot.demo import FORM

    @app.get("/fixture/live", response_class=HTMLResponse)
    async def fixture_form():
        return FORM

    # The SPA fallback must remain the last GET route.
    app.router.routes.insert(-2, app.router.routes.pop())

    @app.post("/fixture/live", response_class=HTMLResponse)
    async def fixture_submit():
        return "<h1>Thank you for applying</h1>"

    model_calls = []
    app.state.fixture_calls = model_calls

    @app.post("/fake-mimo/v1/chat/completions")
    async def fake_model(request: Request):
        body = await request.json()
        model_calls.append(body)
        actions = ["inspect_application", "fill_application_page", "submit_application"]
        index = min(len(model_calls) - 1, 2)
        import json

        content = json.dumps(
            {
                "evaluation_previous_goal": "Fixture step",
                "memory": "Only use approved tools",
                "next_goal": actions[index],
                "action": [{actions[index]: {}}],
            }
        )
        return {
            "id": "fixture-response",
            "object": "chat.completion",
            "created": 0,
            "model": config.mimo_model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield url, config, app.state.service
    server.should_exit = True
    thread.join(timeout=15)


def test_browser_use_and_mimo_protocol_together(server, resume_pdf, monkeypatch):
    """Exercise actual Browser Use + our tools + HTTP adapter; inference is scripted."""
    url, config, service = server
    from jobpilot.browser import BrowserEngine
    import traceback

    failures = []
    original_run = BrowserEngine.run

    async def diagnosed_run(self, *args):
        try:
            return await original_run(self, *args)
        except Exception:
            failures.append(traceback.format_exc())
            raise

    monkeypatch.setattr(BrowserEngine, "run", diagnosed_run)
    # This test pins the navigation-model protocol: no adapters and no guided flow.
    monkeypatch.setattr(config, "engine", "guided")

    async def use_agent(self):
        return None

    monkeypatch.setattr(BrowserEngine, "guided_flow", use_agent)
    config.mimo_api_key = "synthetic-provider-key"
    config.mimo_base_url = url + "/fake-mimo/v1"
    config.allow_private_urls = True
    try:
        with httpx.Client(
            base_url=url, headers={"Authorization": "Bearer " + config.app_token}, trust_env=False, timeout=10
        ) as c:
            r = c.post(
                "/api/resumes", data={"name": "Protocol fixture"}, files={"file": ("resume.pdf", resume_pdf)}
            )
            assert r.status_code == 200, r.text
            rid = r.json()["id"]
            c.put(
                "/api/profile",
                json={
                    "name": "Alex Example",
                    "email": "alex@example.test",
                    "phone": "2025550100",
                    "location": "New York",
                    "facts": {"first_name": "Alex", "last_name": "Example"},
                },
            )
            job = c.post(
                "/api/jobs",
                json={"url": url + "/fixture/live", "title": "Protocol fixture", "company": "Local test"},
            ).json()["job"]
            run = c.post(f"/api/jobs/{job['id']}/queue", json={"resume_id": rid, "mode": "dry_run"})
            assert run.status_code == 200, run.text
            detail = wait_result(c, run.json()["id"])
            assert detail["run"]["state"] == "dry_run_passed", (detail, failures)
            assert detail["run"]["model_calls"] == 3
            assert detail["run"]["cost"] > 0
            assert len(detail["answers"]) >= 5
            c.delete("/api/resumes/" + rid)
            c.put("/api/profile", json={})
    finally:
        config.mimo_api_key = ""
        config.allow_private_urls = False


def test_generic_adapter_finishes_custom_site_without_model(server, resume_pdf):
    """The deterministic driver handles a plain career-site form: zero navigation-model calls."""
    url, config, service = server
    config.mimo_api_key = "synthetic-provider-key"
    config.mimo_base_url = url + "/fake-mimo/v1"
    config.allow_private_urls = True
    try:
        with httpx.Client(
            base_url=url, headers={"Authorization": "Bearer " + config.app_token}, trust_env=False, timeout=10
        ) as c:
            rid = c.post(
                "/api/resumes", data={"name": "Adapter fixture"}, files={"file": ("resume.pdf", resume_pdf)}
            ).json()["id"]
            c.put(
                "/api/profile",
                json={
                    "name": "Alex Example",
                    "email": "alex@example.test",
                    "phone": "2025550100",
                    "location": "New York",
                    "facts": {"first_name": "Alex", "last_name": "Example"},
                },
            )
            job = c.post(
                "/api/jobs",
                json={"url": url + "/fixture/live?adapter=1", "title": "Adapter fixture", "company": "Local"},
            ).json()["job"]
            run = c.post(f"/api/jobs/{job['id']}/queue", json={"resume_id": rid, "mode": "dry_run"})
            assert run.status_code == 200, run.text
            detail = wait_result(c, run.json()["id"])
            assert detail["run"]["state"] == "dry_run_passed", detail
            assert detail["run"]["model_calls"] == 0
            assert all(a["verified"] for a in detail["answers"].values()) and len(detail["answers"]) >= 5
            kinds = [e["kind"] for e in detail["events"]]
            assert "step" in kinds and "agent_step" not in kinds
            c.delete("/api/resumes/" + rid)
            c.put("/api/profile", json={})
    finally:
        config.mimo_api_key = ""
        config.allow_private_urls = False


async def test_native_and_conditional_fields_are_read_back(page, service, config, tmp_path, resume_pdf):
    await page.set_content("""<form><label>First name<input name="first" required></label>
    <label>Email<input type="email" required></label><label>Resume<input type="file" required></label>
    <label>Gender<select required><option value="">Select</option><option>Woman</option><option>Prefer not to identify</option></select></label>
    <fieldset><legend>Do you need sponsorship?</legend><label><input type="radio" name="sponsor" required value="yes">Yes</label><label><input type="radio" name="sponsor" required value="no">No</label></fieldset>
    <button>Submit application</button></form>""")
    path = tmp_path / "resume.pdf"
    path.write_bytes(resume_pdf)
    profile = Profile(email="alex@example.test", facts={"first_name": "Alex"})
    form = FormSession(page, Resolver(profile, service.db, config), path, lambda *x: None)
    await form.scan()
    q = next(f for f in form.fields.values() if f["type"] == "radio")
    profile.approved_answers[answer_key(q)] = "No"
    report = await form.fill_current()
    assert report["ok"] and report["resume_attached"] and report["verified_fields"] == 4, report
    assert await page.get_by_label("Gender").input_value() == "Prefer not to identify"
    assert await page.get_by_label("No", exact=True).is_checked()
    # A dependent question appearing after fill must prevent submission until resolved.
    await page.evaluate(
        "document.querySelector('form').insertAdjacentHTML('beforeend','<label>Explain relocation plans<input required></label>')"
    )
    assert not (await form.verify())["ok"]
    result = await form.fill_current()
    assert result["pending"][0]["question"] == "Explain relocation plans"
    # An ATS overwriting a contact answer is not accepted as success.
    await page.get_by_label("Email", exact=True).fill("different@example.test")
    assert "Value not accepted: Email" in (await form.verify())["problems"]


async def test_file_input_uses_resume_id_when_visible_label_is_generic(page, service, config, tmp_path):
    await page.set_content("""<h2>Resume/CV</h2><label for="resume">Attach</label>
      <input id="resume" type="file" class="visually-hidden" style="display:none">
      <button>Submit application</button>""")
    form = FormSession(
        page,
        Resolver(Profile(), service.db, config),
        tmp_path / "unused.pdf",
        lambda *x: None,
    )
    await form.scan()
    files = [f for f in form.fields.values() if f["type"] == "file"]
    assert len(files) == 1
    assert files[0]["label"] == "Resume/CV"


async def test_react_select_reads_chosen_value_without_proxy_field(page, service, config, tmp_path):
    await page.set_content("""<label id="question-label" for="question">Related to an employee? *</label>
      <div class="select__control"><div class="select__multi-value__label">No</div>
      <input id="question" role="combobox" aria-labelledby="question-label" aria-required="true"></div>
      <input aria-hidden="true" required tabindex="-1" value="No">""")
    form = FormSession(
        page,
        Resolver(Profile(), service.db, config),
        tmp_path / "unused.pdf",
        lambda *x: None,
    )
    await form.scan()
    assert len(form.fields) == 1
    field = next(iter(form.fields.values()))
    assert field["label"] == "Related to an employee?"
    assert field["value"] == "No"
    assert field["required"]


async def test_country_dial_code_display_is_verified(page, service, config, tmp_path):
    await page.set_content("""<label id="country-label" for="country">Country</label>
      <div class="select__control"><div class="select__single-value"></div>
      <input id="country" role="combobox" aria-labelledby="country-label"></div>
      <div role="option" onclick="document.querySelector('.select__single-value').textContent='+1';document.querySelector('#country').value=''">United States +1</div>""")
    form = FormSession(
        page,
        Resolver(Profile(), service.db, config),
        tmp_path / "unused.pdf",
        lambda *x: None,
    )
    await form.scan()
    field = next(f for f in form.fields.values() if f["label"] == "Country")
    await form.fill(field, "United States +1")
    report = await form.verify()
    assert report["ok"] and report["verified_fields"] == 0, report
    assert form.expected[field["id"]] == "+1"


async def test_custom_combobox_commits_option(page, service, config, tmp_path):
    await page.set_content("""<label for="city">Location</label><input id="city" role="combobox" aria-expanded="false" aria-required="true">
      <div id="options" hidden><div role="option">New York</div><div role="option">Boston</div></div>
      <script>const c=document.getElementById('city'),o=document.getElementById('options');
      c.onclick=()=>{o.hidden=false;c.setAttribute('aria-expanded','true')};
      c.onkeydown=e=>{if(e.key==='Escape'){o.hidden=true;c.setAttribute('aria-expanded','false')}};
      o.onclick=e=>{c.value=e.target.textContent;o.hidden=true;c.setAttribute('aria-expanded','false')};</script>""")
    form = FormSession(
        page,
        Resolver(Profile(location="Boston"), service.db, config),
        tmp_path / "unused.pdf",
        lambda *x: None,
    )
    result = await form.fill_current()
    assert result["ok"] and result["verified_fields"] == 1
    assert await page.get_by_role("combobox").input_value() == "Boston"
    assert await page.get_by_role("combobox").get_attribute("aria-expanded") == "false"


async def test_submit_guard_and_confirmation(page):
    await page.set_content(
        '<form onsubmit="event.preventDefault();window.submitted=true"><button>Submit application</button></form>'
    )
    await page.evaluate(GUARD)
    await page.get_by_role("button").click()
    assert not await page.evaluate("!!window.submitted")
    await page.evaluate("window.__jpArmed=true")
    await page.get_by_role("button").click()
    assert await page.evaluate("!!window.submitted")


def wait_result(client, run_id):
    for _ in range(180):
        detail = client.get("/api/runs/" + run_id).json()
        if detail["run"]["state"] not in {"queued", "running", "submitting"}:
            return detail
        time.sleep(0.25)
    pytest.fail("Application did not finish within fixture time budget")


def test_durable_queue_to_real_browser_receipt(server):
    url, config, service = server
    with httpx.Client(
        base_url=url, headers={"Authorization": "Bearer " + config.app_token}, trust_env=False
    ) as c:
        response = c.post("/api/demo", json={"mode": "submit"})
        assert response.status_code == 200, response.text
        detail = wait_result(c, response.json()["id"])
        assert detail["run"]["state"] == "confirmed", detail
        assert detail["run"]["receipt"]["type"] == "explicit_confirmation_page"
        assert detail["demo_receipt"]["submissions"] == 1
        assert detail["demo_receipt"]["resume_sha256"] == detail["run"]["receipt"]["resume_sha256"]
        assert detail["screenshot"] and all(a["verified"] for a in detail["answers"].values())
        assert detail["run"]["model_calls"] == 0
        assert detail["run"]["elapsed"] < 60
        snapshot = c.get("/api/snapshot").json()
        assert snapshot["stats"]["confirmed_today"] == 0, "Demo must not inflate real application metrics"
        assert snapshot["resumes"] == []
        dry = c.post("/api/demo", json={"mode": "dry_run"})
        assert dry.status_code == 200, dry.text
        detail = wait_result(c, dry.json()["id"])
        assert detail["run"]["state"] == "dry_run_passed", detail
        assert detail["run"]["receipt"] == {}
        assert detail["demo_receipt"]["submissions"] == 0
        assert c.get("/api/runs/" + dry.json()["id"] + "/screenshot").status_code == 200


def test_deployment_verifier_and_evidence_integrity(server, tmp_path):
    from jobpilot.verify import VerificationFailed, verify_deployment, write_report

    url, config, service = server
    report = verify_deployment(config, timeout=90)
    assert report["ok"] and [r["submissions_received"] for r in report["runs"]] == [0, 1]
    manifest = tmp_path / "verification.json"
    write_report(manifest, report)
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert "persisted_evidence_matches" in verify_deployment(config, recheck=manifest)["checks"]
    # A receipt flag alone must not pass when the underlying evidence was lost.
    screenshot = config.data_dir / "runs" / report["runs"][0]["id"] / "final.png"
    original = screenshot.read_bytes()
    try:
        screenshot.write_bytes(b"not a screenshot")
        with pytest.raises(VerificationFailed, match="Screenshot evidence"):
            verify_deployment(config, recheck=manifest)
    finally:
        screenshot.write_bytes(original)

    # Verification must not silently unpause production workers to make a test pass.
    control = service.db.get_setting("control")
    service.db.set_setting("control", {**control, "paused": True})
    try:
        with pytest.raises(VerificationFailed, match="paused"):
            verify_deployment(config)
        assert service.db.get_setting("control")["paused"]
    finally:
        service.db.set_setting("control", control)


async def test_dashboard_navigation_and_mobile(server, page, tmp_path):
    url, config, _ = server
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(url)
    if await page.get_by_text("Build the dashboard:", exact=False).count():
        pytest.skip("Build frontend before UI tests")
    await page.get_by_label("Access token", exact=True).fill(config.app_token)
    await page.get_by_role("button", name="Open workspace", exact=True).click()
    await page.get_by_role("heading", name="A clearer path to your next role.").wait_for()
    target = Path(os.getenv("ARTIFACT_DIR", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    await page.set_viewport_size({"width": 1440, "height": 1200})
    await page.screenshot(path=str(target / "dashboard-desktop.png"), full_page=True)
    for name in ["Applications", "Review inbox", "Resumes", "Knowledge base", "Sources", "Settings"]:
        await page.locator("nav").get_by_role("button", name=name, exact=True).click()
        await page.get_by_role("heading", name=name, exact=True).wait_for()
    await page.get_by_role("heading", name="Email verification", exact=True).wait_for()
    assert await page.get_by_role("button", name="Connect Gmail", exact=True).is_disabled()
    await page.screenshot(path=str(target / "settings-email-desktop.png"), full_page=True)
    await page.set_viewport_size({"width": 390, "height": 844})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Email settings overflow"
    )
    await page.screenshot(path=str(target / "settings-email-mobile.png"), full_page=True)
    await page.locator("nav").get_by_role("button", name="Overview", exact=True).click()
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.screenshot(path=str(target / "dashboard-mobile.png"), full_page=True)
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Mobile layout overflows"
    )
    assert not errors, errors
