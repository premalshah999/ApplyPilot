"""Owned application fixtures: entry, page transitions, and submission boundaries."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine, GUARD
from jobpilot.email_browser import EmailBrowser
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from test_browser import page as page


@pytest.mark.parametrize("ats", ["workday", "lever", "ashby", "smartrecruiters", "workable", "bamboohr", "oracle", "icims", "taleo"])
async def test_entry_multipage_resume_and_final_boundary(ats, service, page, tmp_path, resume_pdf):
    contact = '<form onsubmit="event.preventDefault();document.body.innerHTML=experience"><label>Full name<input required></label><button>Continue</button></form>'
    experience = '<form onsubmit="event.preventDefault();document.body.innerHTML=review"><label>Resume<input type=file required></label><button>Save and Continue</button></form>'
    review = '<h2>Review</h2><button onclick="window.didSubmit=true">Submit application</button>'
    await page.context.add_init_script(GUARD)
    await page.set_content('<button onclick="document.body.innerHTML=contact">Apply Now</button>')
    await page.evaluate("x=>Object.assign(window,x)", {"contact": contact, "experience": experience, "review": review})
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(resume_pdf)
    engine = BrowserEngine(service, "owned-" + ats)
    engine.job = {"ats": ats, "url": "https://careers.example.test/job"}
    engine.page = page
    engine.run_record = {"mode": "dry_run"}
    engine.form = FormSession(page, Resolver(Profile(name="Alex Example"), service.db, service.config), resume, engine.emit)
    engine.email_verification = EmailBrowser(engine)
    result = await engine.guided_flow()
    assert result["state"] == "dry_run_passed"
    assert result["report"]["resume_attached"]
    assert not await page.evaluate("!!window.didSubmit")


async def test_application_entry_permission_does_not_allow_final_apply(page, service, tmp_path):
    await page.context.add_init_script(GUARD)
    await page.set_content('<form onsubmit="event.preventDefault();window.didSubmit=true"><label>Full name<input required value="Alex"></label><button>Apply</button></form>')
    form = FormSession(page, Resolver(Profile(name="Alex"), service.db, service.config), tmp_path / 'resume.pdf', lambda *a: None)
    obs = await form.scan()
    with pytest.raises(ValueError, match="submit"):
        await form.click(obs["controls"][0]["id"])
    assert not await page.evaluate("!!window.didSubmit")


async def test_lever_source_checkbox_group_and_unlinked_question_label(page, service, tmp_path):
    await page.set_content('''<li class="application-question"><div class="application-label">How did you hear about us?✱</div>
    <label><input type=checkbox name=source required>Employee Referral</label>
    <label><input type=checkbox name=source required>LinkedIn</label></li>
    <li class="application-question"><div class="application-label">Email✱</div><input type=email name="cards[1]" required></li>''')
    form = FormSession(page, Resolver(Profile(email="alex@example.test", application_source="LinkedIn"), service.db, service.config), tmp_path/'resume.pdf', lambda *a: None)
    result = await form.fill_current()
    assert result["ok"] and not result["pending"]
    assert await page.get_by_label("LinkedIn").is_checked()
    assert not await page.get_by_label("Employee Referral").is_checked()


async def test_hidden_oracle_consent_opens_terms_and_agrees(page, service, tmp_path):
    await page.set_content('''<label>Email<input type=email required></label>
    <input type=text id=honey-pot-1>
    <input type=checkbox id=terms required style="display:none" aria-labelledby="terms-label">
    <label id=terms-label onclick="event.preventDefault();document.querySelector('dialog').showModal()">I agree with the terms and conditions</label>
    <dialog><h2>Terms and Conditions</h2><button onclick="document.getElementById('terms').checked=true;this.closest('dialog').close()">AGREE</button></dialog>''')
    form = FormSession(page, Resolver(Profile(email="alex@example.test", accept_all_application_terms=True), service.db, service.config), tmp_path/'resume.pdf', lambda *a: None)
    result = await form.fill_current()
    assert result['ok'], result
    assert await page.locator('#terms').is_checked()
    assert await page.locator('#honey-pot-1').input_value() == ''
    assert await page.locator('[data-jp-auth-control]').count() == 0


async def test_ashby_boolean_buttons_are_required_questions(page, service, tmp_path):
    await page.set_content('''<div class="ashby-application-form-field-entry"><label class="ashby-application-form-question-title _required_xyz">Are you authorized to work in the United States?</label>
    <div class="ashby-application-form-input-yesno"><button aria-pressed=false onclick="this.setAttribute('aria-pressed','true')">Yes</button><button aria-pressed=false>No</button><input type=checkbox hidden></div></div>''')
    form=FormSession(page, Resolver(Profile(facts={'work_authorization':'Yes'}),service.db,service.config),tmp_path/'resume.pdf',lambda *a:None)
    obs=await form.scan()
    assert len(obs['fields'])==1 and obs['fields'][0]['required']
    assert obs['fields'][0]['options']==['Yes','No']
    await form.fill(obs['fields'][0], 'Yes')
    assert (await form.scan())['fields'][0]['value']=='Yes'
    await page.set_content('<h2>Your form needs corrections</h2><p>Missing entry for required field</p>')
    assert (await form.rejection())['type']=='explicit_rejection_page'


async def test_next_programmatic_submit_and_unrelated_tab_do_not_break_navigation(page, service, tmp_path):
    await page.set_content('<form onsubmit="event.preventDefault();window.advanced=true"><button type=button onclick="this.form.requestSubmit()">Next</button></form>')
    await page.evaluate(GUARD)
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path/'resume.pdf', lambda *a: None)
    obs = await form.scan()
    import asyncio
    async def unrelated_tab():
        await asyncio.sleep(0.1)
        return await page.context.new_page()
    task = asyncio.create_task(unrelated_tab())
    await form.click(obs['controls'][0]['id'])
    other = await task
    assert await page.evaluate('window.advanced')
    assert form.page == page
    assert await page.locator('[data-jp-auth-control]').count() == 0
    await other.close()


async def test_desktop_navigator_uses_only_its_page(page, service, tmp_path, resume_pdf, monkeypatch):
    from jobpilot.browser import NavigationDecision
    await page.set_content('<label>Full name<input required></label><label>Resume<input type=file required></label><button>Submit application</button>')
    other=await page.context.new_page()
    await other.set_content('<h1>Another employer sign-in</h1>')
    resume=tmp_path/'resume.pdf';resume.write_bytes(resume_pdf)
    engine=BrowserEngine(service,'desktop-isolated')
    engine.job={'ats':'custom','url':'https://example.test/job'}
    engine.page=page;engine.run_record={'mode':'dry_run'}
    engine.form=FormSession(page,Resolver(Profile(name='Alex Example'),service.db,service.config),resume,engine.emit)
    engine.email_verification=EmailBrowser(engine)
    decisions=iter(['fill','submit'])
    async def structured(*args,**kwargs):
        assert 'Another employer' not in str(args)
        return NavigationDecision(action=next(decisions))
    monkeypatch.setattr('jobpilot.models.structured',structured)
    await engine.run_desktop_navigator()
    assert engine.result['state']=='dry_run_passed'
    assert not other.is_closed()
    assert await other.locator('h1').inner_text()=='Another employer sign-in'
    await other.close()


async def test_unsupported_email_verification_keeps_desktop_page_waiting(service):
    service.config.browser_cdp_url='http://localhost:9224'
    engine=BrowserEngine(service,'desktop-verification')
    class UnsupportedVerification:
        async def handle(self):
            raise ValueError('Employer verification rule is missing')
    engine.email_verification=UnsupportedVerification()
    assert not await engine.handle_email()
    assert engine.result['state']=='waiting_browser'
    assert 'open Chrome tab' in engine.result['reason']
