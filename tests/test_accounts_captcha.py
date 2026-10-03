from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.accounts import AccountStore, AccountFlow
from jobpilot.answers import Resolver
from jobpilot.browser import GUARD
from jobpilot.capsolver import CaptchaSolver, HOOK
from jobpilot.forms import FormSession
from jobpilot.privacy import deny_location
from jobpilot.schemas import Profile
from test_browser import page as page


def test_shared_credentials_reused_and_encrypted(service):
    store = AccountStore(service, "alex@example.test")
    first = store.shared()
    store.save("https://one.example.test", first, "created_locally")
    assert AccountStore(service, "ALEX@example.test").shared() == first
    assert store.shared() == first
    second = store.shared()
    store.save("https://two.example.test", second, "created_locally")
    assert store.get("https://one.example.test") == store.get("https://two.example.test")
    assert first["password"] not in str(service.db.get_setting(store.key("https://one.example.test")))


async def test_unknown_ats_creates_account_without_secret_ledger(service, page, tmp_path):
    await page.add_init_script(GUARD)
    html = """<form onsubmit="event.preventDefault();document.body.innerHTML='<label>Resume<input type=file></label>'">
    <label>Email<input type=email></label><label>Password<input type=password autocomplete=new-password></label>
    <label>Confirm password<input type=password></label><button>Create Account</button></form>"""
    await page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html))
    await page.goto("https://unfamiliar.example.test/apply")
    profile = Profile(email="alex@example.test", allow_account_creation=True)
    form = FormSession(
        page, Resolver(profile, service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    e = SimpleNamespace(
        service=service,
        form=form,
        page=page,
        result=None,
        emit=lambda *a: None,
        email_verification=SimpleNamespace(prepare_submission=AsyncMock()),
        browser_challenge=AsyncMock(return_value=None),
    )
    auth = AccountFlow(e)
    assert await auth.handle()
    assert e.result is None
    assert await page.locator("input[type=file]").count() == 1
    assert auth.store.get(page.url)["password"] not in str(form.ledger)


async def test_location_denied_in_current_page_and_popup(page):
    await deny_location(page.context, page)
    assert (
        await page.evaluate("navigator.permissions.query({name:'geolocation'}).then(p=>p.state)") == "denied"
    )
    assert (
        await page.evaluate("new Promise(r=>navigator.geolocation.getCurrentPosition(()=>r(0),e=>r(e.code)))")
        == 1
    )
    popup = await page.context.new_page()
    assert (
        await popup.evaluate("navigator.permissions.query({name:'geolocation'}).then(p=>p.state)") == "denied"
    )


async def test_invisible_recaptcha_callback_in_iframe_is_delivered(config, page, monkeypatch):
    config.capsolver_api_key = "private-fixture-key"
    html = """<script>window.grecaptcha={render:()=>1};</script><div id=captcha></div>"""
    await page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html))
    await page.goto("https://example.test")
    await page.set_content('<iframe src="https://frame.example.test"></iframe>')
    frame = page.frames[-1]
    await frame.wait_for_load_state()
    await frame.evaluate(HOOK)
    await frame.evaluate(
        "grecaptcha.render('captcha',{sitekey:'fixture-sitekey',callback:t=>window.accepted=t})"
    )
    events = []

    async def provider(base, key, task, *args):
        assert task["websiteURL"] == "https://frame.example.test/"
        assert task["type"] == "ReCaptchaV2TaskProxyLess"
        return {"gRecaptchaResponse": "private-token"}, None

    monkeypatch.setattr("jobpilot.capsolver.task_result", provider)
    solver = CaptchaSolver(config, lambda *a: events.append(a))
    assert await solver.solve(page)
    assert await frame.evaluate("window.accepted") == "private-token"
    assert "private-token" not in str(events) and "private-fixture-key" not in str(events)
    assert not await solver.solve(page)


async def test_provider_rejection_is_bounded_and_hcaptcha_never_sent_as_token(config, page, monkeypatch):
    config.capsolver_api_key = "fixture-key"
    await page.set_content("<div class=g-recaptcha data-sitekey=fixture></div>")
    provider = AsyncMock(return_value=(None, "ERROR_INVALID_TASK_DATA"))
    monkeypatch.setattr("jobpilot.capsolver.task_result", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    for _ in range(5):
        assert not await solver.solve(page)
    assert provider.call_count == 1
    assert "ERROR_INVALID_TASK_DATA" in solver.last_reason


async def test_stale_solver_response_not_injected(config, page, monkeypatch):
    config.capsolver_api_key = "fixture-key"
    await page.set_content(
        "<div class=g-recaptcha data-sitekey=old></div><textarea name=g-recaptcha-response></textarea>"
    )

    async def provider(*args):
        await page.locator(".g-recaptcha").evaluate("e=>e.dataset.sitekey='replacement'")
        return {"gRecaptchaResponse": "stale-token"}, None

    monkeypatch.setattr("jobpilot.capsolver.task_result", provider)
    assert not await CaptchaSolver(config, lambda *a: None).solve(page)
    assert await page.locator("textarea").input_value() == ""


async def test_image_fallback_clicks_only_challenge_and_verifies_transition(config,page,monkeypatch):
    config.twocaptcha_api_key='private-coordinate-key'
    html='''<style>body{margin:0}#puzzle{height:470px;background:#eee}</style><div id=puzzle onclick="window.picked=true">Select the circle</div>
    <div class=button-submit role=button style="height:50px" onclick="if(window.picked)parent.postMessage('accepted','*')">Verify</div>'''
    await page.route('https://hcaptcha.example.test/**',lambda r:r.fulfill(content_type='text/html',body=html))
    await page.set_content('''<p>PRIVATE APPLICATION CONTENT</p><iframe title="hCaptcha challenge" src="https://hcaptcha.example.test/challenge" style="width:520px;height:570px;border:0"></iframe>
    <script>addEventListener('message',e=>{if(e.data==='accepted')document.querySelector('iframe').remove()})</script>''')
    await page.frames[-1].wait_for_load_state()
    async def provider(base,key,task,*args):
        assert base=='https://api.2captcha.com' and task['type']=='CoordinatesTask'
        assert 'PRIVATE APPLICATION CONTENT' not in str(task)
        assert len(task['body'])>100
        return {'coordinates':[{'x':100,'y':100}]},None
    monkeypatch.setattr('jobpilot.capsolver.task_result',provider)
    solver=CaptchaSolver(config,lambda *a:None)
    assert await solver.solve(page)
    assert await page.locator('p').inner_text()=='PRIVATE APPLICATION CONTENT'


async def test_image_fallback_rejects_navigation_coordinates(config,page,monkeypatch):
    config.twocaptcha_api_key='fixture-key'
    html='<p>Select the circle</p><button onclick="window.badClick=true" style="position:absolute;top:500px">Submit</button>'
    await page.route('https://hcaptcha.example.test/**',lambda r:r.fulfill(content_type='text/html',body=html))
    await page.set_content('<iframe title="hCaptcha challenge" src="https://hcaptcha.example.test/challenge" style="width:520px;height:570px"></iframe>')
    frame=page.frames[-1];await frame.wait_for_load_state()
    monkeypatch.setattr('jobpilot.capsolver.task_result',AsyncMock(return_value=({'coordinates':[{'x':10,'y':520}]},None)))
    solver=CaptchaSolver(config,lambda *a:None)
    assert not await solver.solve(page)
    assert 'outside the puzzle' in solver.last_reason
    assert not await frame.evaluate('window.badClick')
