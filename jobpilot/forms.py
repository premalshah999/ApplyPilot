"""Browser controls are referenced from observations, never model-generated selectors."""

import asyncio
import re

from .answers import answer_key, normalize

SCAN = r"""() => {
  window.__jpCounter = window.__jpCounter || 0;
  window.__jpDocId = window.__jpDocId || Math.random().toString(36).slice(2,10);
  const id = el => el.dataset.jpId || (el.dataset.jpId = 'jp' + (++window.__jpCounter));
  const visible = el => !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
  const labelText = el => {
    const clone = el.cloneNode(true);
    clone.querySelectorAll('input,select,textarea,button,[role=option]').forEach(x=>x.remove());
    return clone.textContent.trim();
  };
  const label = el => {
    const linked = el.id && [...document.querySelectorAll('label')].find(x => x.htmlFor === el.id);
    if (linked) return labelText(linked);
    const aria = el.getAttribute('aria-labelledby');
    if (aria) return aria.split(' ').map(x => document.getElementById(x)?.innerText || '').join(' ').trim();
    const applicationLabel = el.closest('.application-question')?.querySelector('.application-label');
    if (applicationLabel && !['checkbox','radio'].includes(el.type)) return labelText(applicationLabel);
    return el.getAttribute('aria-label') || [...(el.labels || [])].map(labelText).join(' ').trim()
      || el.closest('[role=group],fieldset')?.querySelector('legend')?.innerText
      || el.getAttribute('placeholder') || el.getAttribute('name') || el.id || 'Unlabelled field';
  };
  const section = el => el.closest('fieldset')?.querySelector('legend')?.innerText
    || el.closest('section')?.querySelector('h2,h3,h4')?.innerText || '';
  const seen = new Set(), fields = [];
  const controls = document.querySelectorAll('input,select,textarea,[role=combobox],[role=radiogroup],button[aria-haspopup=listbox],.ashby-application-form-input-yesno');
  for (const el of controls) {
    if (el.closest('[inert],[aria-hidden="true"]') || el.getAttribute('data-automation-id') === 'beecatcher') continue;
    if (/honey.?pot|beecatcher|bot.?trap/i.test(`${el.id} ${el.name || ''}`)) continue;
    if (el.matches('input') && el.closest('.ashby-application-form-input-yesno')) continue;
    if (el.matches('button[aria-haspopup=listbox]')
        && !el.closest('[data-automation-id^="formField-"]')) continue;
    let type = el.getAttribute('role') || el.type || el.tagName.toLowerCase();
    if (el.matches('.ashby-application-form-input-yesno')) type = 'buttonchoice';
    if (el.getAttribute('data-uxi-widget-type') === 'selectinput'
        || el.matches('button[aria-haspopup=listbox]')) type = 'combobox';
    if (el.disabled || el.readOnly || el.getAttribute('aria-hidden') === 'true'
        || ['hidden','submit','button','reset'].includes(type)) continue;
    const choiceLabel = ['checkbox','radio'].includes(type) &&
      (el.labels?.[0] || document.getElementById((el.getAttribute('aria-labelledby') || '').split(' ')[0]));
    if (!visible(el) && type !== 'file' && !(choiceLabel && visible(choiceLabel))) continue;
    if (el.closest('[role=combobox]') !== el && el.closest('[role=combobox]')) continue;
    let options = [], value = el.value || '', question = label(el), group = null;
    const ashbyLabel = el.closest('.ashby-application-form-field-entry')?.querySelector('.ashby-application-form-question-title');
    if (type === 'buttonchoice') {
      question = ashbyLabel?.innerText || question;
      group = [...el.querySelectorAll('button[aria-pressed]')];
      options = group.map(x=>x.innerText.trim());
      value = group.find(x=>x.getAttribute('aria-pressed')==='true')?.innerText.trim() || '';
    }
    if (type === 'combobox' && el.matches('button[aria-haspopup=listbox]'))
      value = el.innerText.trim() === 'Select One' ? '' : el.innerText.trim();
    if (type === 'combobox' && el.getAttribute('data-uxi-widget-type') === 'selectinput') {
      const hint = document.getElementById(el.getAttribute('aria-describedby') || '')?.textContent || '';
      const selected = hint.match(/\d+ items? selected,?\s*(.*)/i);
      value = selected?.[1]?.trim() || '';
    }
    if (type === 'file') {
      const identifier = `${el.id || ''} ${el.getAttribute('name') || ''}`;
      if (/resume|\bcv\b/i.test(identifier) && !/resume|\bcv\b/i.test(question)) question = 'Resume/CV';
      else if (/cover.?letter/i.test(identifier) && !/cover.?letter/i.test(question)) question = 'Cover Letter';
      else if (el.getAttribute('data-automation-id') === 'file-upload-input-ref'
        && /resume\/CV/i.test(document.body.innerText)) question = 'Resume/CV';
    }
    if (type === 'checkbox' && el.name && [...document.querySelectorAll('input[type=checkbox]')].filter(x=>x.name===el.name).length > 1) {
      const key = 'checkbox:' + el.name;
      if (seen.has(key)) continue;
      seen.add(key);
      group = [...document.querySelectorAll('input[type=checkbox]')].filter(x=>x.name===el.name);
      const parent = el.closest('.application-question,fieldset,[role=group]');
      question = parent?.querySelector('.application-label,legend')?.innerText || el.name;
      type = 'checkboxgroup'; options = group.map(label);
      value = group.filter(x=>x.checked).map(label).join('\n');
    } else if (type === 'radio') {
      const key = el.name || id(el);
      if (seen.has(key)) continue;
      seen.add(key);
      group = [...document.querySelectorAll('input[type=radio]')].filter(x => x.name === el.name);
      const parent = el.closest('fieldset,[role=radiogroup],.application-question');
      question = parent ? (parent.querySelector('legend,.application-label')?.innerText || label(parent)) : (el.name || question);
      options = group.map(label); value = group.find(x=>x.checked); value = value ? label(value) : '';
    } else if (type === 'radiogroup') {
      group = [...el.querySelectorAll('[role=radio]')];
      options = group.map(x=>x.innerText || label(x));
      value = group.find(x=>x.getAttribute('aria-checked')==='true')?.innerText || '';
    } else if (el.tagName === 'SELECT') {
      type = 'select'; options = [...el.options].filter(x=>!x.disabled && x.value !== '').map(x=>x.text);
      value = el.selectedOptions[0]?.text || '';
    } else if (type === 'combobox' && !value) {
      const selected = el.closest('.select__control')?.querySelectorAll(
        '.select__single-value,.select__multi-value__label'
      ) || [];
      value = [...selected].map(x=>x.textContent.trim()).filter(Boolean).join(', ');
    } else if (type === 'checkbox') value = el.checked ? 'true' : 'false';
    else if (type === 'file') value = [...(el.files || [])].map(x=>x.name).join(', ');
    const req = !!el.required || el.getAttribute('aria-required')==='true' || /[*✱]/.test(question)
      || (ashbyLabel && /required/i.test(ashbyLabel.className))
      || (group && group.some(x=>x.required));
    fields.push({id:id(el),label:question.replace(/\s+/g,' ').replace(/\s*[*✱]\s*$/,'').trim(),type,
      required:!!req, options, value, section:section(el), maxlength:el.maxLength || -1,
      autocomplete:el.autocomplete || '',
      valid: type === 'checkboxgroup' ? (!req || !!value) : (el.validity ? el.validity.valid : true)});
  }
  const buttons = [...document.querySelectorAll('button,a,[role=button],input[type=submit]')]
    .filter(el=>visible(el) && !el.disabled).map(el=>({id:id(el),label:(el.innerText || el.value || label(el)).trim(),
      href:el.getAttribute('href') || '', type:el.form ? (el.type || '') : ''})).filter(x=>x.label);
  return {fields, buttons, document_id:window.__jpDocId, text:document.body.innerText.slice(-22000), title:document.title};
}"""

FINAL = re.compile(
    r"\bsubmit\b|send (?:my )?application|complete (?:my )?application|finish application", re.IGNORECASE
)
ENTRY = re.compile(
    r"^(?:apply(?: now| for (?:this|the) (?:job|position))?|start (?:your )?application|"
    r"continue application|i.m interested|apply manually|autofill with resume)$", re.I
)
AUTH = re.compile(r"password|verification code|one.time|authentication code|security code", re.IGNORECASE)


class FormSession:
    def __init__(self, page, resolver, resume_path, emit):
        self.page, self.resolver, self.resume_path, self.emit = page, resolver, resume_path, emit
        self.fields, self.buttons, self.frames = {}, {}, {}
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        self.ledger = {}
        self.pending = []
        self.uploaded = False
        self.upload_verified = False

    async def scan(self):
        self.fields, self.buttons, self.frames = {}, {}, {}
        texts = []
        for n, frame in enumerate(self.page.frames):
            try:
                if frame != self.page.main_frame:
                    owner = await frame.frame_element()
                    identity = await owner.evaluate("e=>[e.title,e.id,e.src?.slice(0,200)].join(' ')")
                    if re.search(r"hcaptcha|recaptcha|challenge", identity, re.I):
                        continue
                data = await asyncio.wait_for(frame.evaluate(SCAN), timeout=5)
            except Exception:
                continue
            texts.append(data["text"])
            for kind in ("fields", "buttons"):
                for item in data[kind]:
                    item["employer"] = self.resolver.employer
                    item["dom_id"] = item["id"]
                    item["id"] = f"f{n}-{data['document_id']}-{item['id']}"
                    self.frames[item["id"]] = frame
                    getattr(self, kind)[item["id"]] = item
        slots = {}
        for field in self.fields.values():
            key = (normalize(field["label"]), field["type"])
            field["slot"] = slots.get(key, 0)
            slots[key] = field["slot"] + 1
        return {
            "fields": list(self.fields.values()),
            "controls": list(self.buttons.values()),
            "text": "\n".join(texts)[:22000],
            "url": self.page.url,
        }

    def locator(self, field_id):
        item = self.fields.get(field_id) or self.buttons.get(field_id)
        if not item:
            raise ValueError("Unknown or stale field ID; inspect again")
        return self.frames[field_id].locator(f'[data-jp-id="{item["dom_id"]}"]')

    async def set_checked(self, el, checked):
        if await el.is_checked() == checked:
            return
        if await el.is_visible():
            await el.set_checked(checked, timeout=4000)
        else:
            label = await el.evaluate_handle(
                "e=>e.labels?.[0] || document.getElementById((e.getAttribute('aria-labelledby')||'').split(' ')[0])"
            )
            target = label.as_element()
            if not target or not await target.is_visible():
                raise ValueError("Checkbox or radio label is not visible")
            await target.click(timeout=4000)
            if not await el.is_checked() and checked and self.resolver.profile.accept_all_application_terms:
                handle = await el.element_handle()
                frame = await handle.owner_frame()
                agree = frame.get_by_role("button", name=re.compile(r"^(?:I )?Agree$", re.I))
                if await agree.count() == 1 and await agree.is_visible():
                    agreed = await agree.element_handle()
                    await agreed.evaluate("e=>e.dataset.jpAuthControl='true'")
                    try:
                        await agreed.click(timeout=4000)
                    finally:
                        await agreed.evaluate("e=>delete e.dataset.jpAuthControl")
                    await self.page.wait_for_timeout(200)
        if await el.is_checked() != checked:
            raise ValueError("Checkbox or radio selection was not accepted")

    async def fill(self, field, value):
        el = self.locator(field["id"])
        kind = field["type"]
        if kind == "checkbox":
            await self.set_checked(el, value.lower() == "true")
        elif kind == "checkboxgroup":
            name = await el.get_attribute("name")
            matched = False
            for checkbox in await self.frames[field["id"]].locator('input[type="checkbox"]').all():
                if await checkbox.get_attribute("name") != name:
                    continue
                label = await checkbox.evaluate("e=>[...(e.labels||[])].map(x=>x.innerText.trim()).join(' ')")
                selected = normalize(label) == normalize(value)
                await self.set_checked(checkbox, selected)
                matched = matched or selected
            if not matched:
                raise ValueError("Checkbox answer is no longer present")
        elif kind == "select":
            await el.select_option(label=value, timeout=4000)
        elif kind == "radio":
            name = await el.get_attribute("name")
            frame = self.frames[field["id"]]
            radios = frame.locator('input[type="radio"]')
            found = False
            for i in range(await radios.count()):
                radio = radios.nth(i)
                if await radio.get_attribute("name") == name:
                    labels = await radio.evaluate("e => [...e.labels].map(x=>x.innerText).join(' ').trim()")
                    if normalize(labels) == normalize(value):
                        await self.set_checked(radio, True)
                        found = True
                        break
            if not found:
                raise ValueError("Radio option is no longer present")
        elif kind == "radiogroup":
            await el.get_by_role("radio", name=value, exact=True).click(timeout=4000)
        elif kind == "buttonchoice":
            await el.get_by_role("button", name=value, exact=True).click(timeout=4000)
        elif kind == "combobox":
            workday_source = (
                "myworkdayjobs.com" in self.page.url
                and field["label"] == "How Did You Hear About Us?"
                and self.resolver.profile.application_source.casefold() == "linkedin"
                and value == "Job Boards"
            )
            await el.click(timeout=4000)
            if await el.evaluate("e=>['INPUT','TEXTAREA'].includes(e.tagName)") and not workday_source:
                await el.fill(value, timeout=4000)
            options = self.frames[field["id"]].locator(
                '[role="option"]:not([data-automation-id="selectedItem"])'
                if "myworkdayjobs.com" in self.page.url
                else '[role="option"]'
            )
            if workday_source:
                root = options.filter(has_text=re.compile(r"^Job Boards$"))
                await root.first.click(timeout=5000)
                leaf = self.frames[field["id"]].get_by_role("option", name="LinkedIn")
                await leaf.click(timeout=5000)
                value = "LinkedIn"
                exact = None
            else:
                exact = options.filter(has_text=re.compile("^" + re.escape(value) + "$"))
            if re.search(r"location|city|currently based", field["label"], re.I):
                await options.first.wait_for(timeout=5000)
                labels = await options.all_text_contents()
                region = self.resolver.profile.location.split(",")[1].strip().casefold() if "," in self.resolver.profile.location else ""
                region = {"md": "maryland"}.get(region, region)
                candidates = [label for label in labels if value.casefold() in label.casefold()
                              and region and region in label.casefold()]
                if candidates:
                    value = candidates[0]
                    exact = self.frames[field["id"]].get_by_role("option", name=value, exact=True)
            if exact is not None:
                await exact.first.click(timeout=5000)
        else:
            await el.fill(value, timeout=4000)
            await el.blur()
        expected = value
        if kind == "combobox" and field["label"] == "Country":
            # Greenhouse's phone-country picker shows only the dial code after
            # an exact option was clicked (for example, "United States +1" -> "+1").
            displayed = await el.evaluate(
                "e=>e.closest('.select__control')?.querySelector('.select__single-value')?.textContent.trim() || ''"
            )
            if re.fullmatch(r"\+\d+", displayed) and value.endswith(displayed):
                expected = displayed
        self.expected[field["id"]] = expected
        self.expected_labels[(normalize(field["label"]), kind)] = expected
        self.expected_slots[(normalize(field["label"]), kind, field["slot"])] = expected

    async def fill_current(self):
        await self.scan()
        self.pending = []
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        if "myworkdayjobs.com" in self.page.url:
            body = await self.page.locator("body").inner_text(timeout=5000)
            if self.resume_path.name in body and "Successfully Uploaded!" in body:
                self.uploaded = self.upload_verified = True
        # Upload first: parsing can overwrite fields and change the page.
        for field in list(self.fields.values()):
            if field["type"] == "file" and re.search(r"resume|cv\b", field["label"], re.IGNORECASE):
                if "myworkdayjobs.com" in self.page.url and self.uploaded and self.upload_verified:
                    continue
                if not field["value"]:
                    await self.locator(field["id"]).set_input_files(str(self.resume_path), timeout=15000)
                    if await self.page.locator('.file-upload[aria-labelledby="upload-label-resume"]').count():
                        await self.page.locator('.file-upload[aria-labelledby="upload-label-resume"] .file-upload__filename').wait_for(timeout=15000)
                    elif "myworkdayjobs.com" in self.page.url:
                        await self.page.get_by_text("Successfully Uploaded!", exact=True).wait_for(timeout=20000)
                    else:
                        await self.page.wait_for_timeout(350)
                self.uploaded = True
        await self.scan()
        experience = "myworkdayjobs.com" in self.page.url and bool(
            await self.page.get_by_role("heading", name="My Experience", exact=True).count()
        )
        fields = []
        for field in self.fields.values():
            if field["type"] in ("file", "password"):
                continue
            prefilled_experience = experience and bool(field["value"])
            preselected_contact = "myworkdayjobs.com" in self.page.url and field["value"] and (
                (field["label"] == "Country" and field["value"] == "United States of America")
                or (field["label"] == "Phone Device Type" and field["value"] == "Mobile")
                or (
                    field["label"] == "Country Phone Code"
                    and field["value"] == "United States of America (+1)"
                )
            )
            if prefilled_experience or preselected_contact:
                self.expected[field["id"]] = field["value"]
                self.expected_labels[(normalize(field["label"]), field["type"])] = field["value"]
                self.expected_slots[(normalize(field["label"]), field["type"], field["slot"])] = field["value"]
                self.ledger[field["id"]] = {
                    "label": field["label"],
                    "type": field["type"],
                    "slot": field["slot"],
                    "answer": field["value"],
                    "evidence_ids": ["resume:parsed" if prefilled_experience else "employer:preselected"],
                    "verified": False,
                }
                continue
            if experience and not field["required"]:
                continue
            if experience and normalize(field["label"]) in {"degree", "field of study", "school or university"}:
                field["entry_context"] = {
                    f["label"]: f["value"] for f in self.fields.values()
                    if f["slot"] == field["slot"] and f["value"]
                    and normalize(f["label"]) in {"school or university", "degree", "field of study", "overall result gpa"}
                }
            fields.append(field)
        # Observe only unanswered questions. Workday resume parsing can create
        # dozens of filled controls; opening those menus can change the form.
        for field in fields:
            if field["type"] == "combobox":
                try:
                    el = self.locator(field["id"])
                    await el.click(timeout=2000)
                    options = self.frames[field["id"]].locator(
                        '[role="option"]:not([data-automation-id="selectedItem"])'
                        if "myworkdayjobs.com" in self.page.url
                        else '[role="option"]'
                    )
                    await options.first.wait_for(timeout=3000)
                    field["options"] = [x.strip() for x in await options.all_text_contents() if x.strip()][
                        :250
                    ]
                    await el.press("Escape")
                except Exception:
                    pass
        original_fields = {field["id"]: field for field in fields}
        answers = await self.resolver.resolve(fields)
        for answer in answers:
            field = original_fields[answer.field_id]
            if answer.disposition != "answer":
                if field["required"]:
                    self.pending.append(
                        {
                            "question": field["label"],
                            "options": field["options"],
                            "key": answer_key(field),
                            "reason": answer.reason,
                        }
                    )
                continue
            try:
                # Workday replaces controls after each selection. Resolve the same
                # visible question again before committing the next answer.
                if "myworkdayjobs.com" in self.page.url:
                    await self.scan()
                    matches = [
                        item
                        for item in self.fields.values()
                        if item["label"] == field["label"]
                        and item["type"] == field["type"]
                        and item["slot"] == field["slot"]
                    ]
                    if len(matches) != 1:
                        raise ValueError("Question changed while filling")
                    field = matches[0]
                await self.fill(field, answer.value)
                self.ledger[field["id"]] = {
                    "label": field["label"],
                    "type": field["type"],
                    "slot": field["slot"],
                    "answer": self.expected[field["id"]],
                    "evidence_ids": answer.evidence_ids,
                    "verified": False,
                }
            except Exception as exc:
                self.emit(
                    "field_error", f"Could not commit {field['label']}", {"error_type": type(exc).__name__}
                )
        for field in self.fields.values():
            if field["type"] == "password" or AUTH.search(field["label"]):
                self.pending.append(
                    {
                        "question": "Sign in to this employer and import the browser session",
                        "options": [],
                        "key": "session",
                        "reason": "Authentication required",
                    }
                )
        report = await self.verify()
        self.emit("verification", "Read back the form after filling", report)
        return report

    async def verify(self):
        await self.scan()
        problems = []
        greenhouse_resume = self.page.locator('.file-upload[aria-labelledby="upload-label-resume"]')
        if await greenhouse_resume.count():
            filenames = await greenhouse_resume.locator('.file-upload__filename').all_text_contents()
            self.upload_verified = any(self.resume_path.name in name for name in filenames)
            if not self.upload_verified:
                problems.append("Resume upload has not been accepted by the website")
        elif self.uploaded and "myworkdayjobs.com" in self.page.url:
            body = await self.page.locator("body").inner_text(timeout=5000)
            self.upload_verified = (
                "Successfully Uploaded!" in body and self.resume_path.name in body
            ) or self.upload_verified
        for f in self.fields.values():
            expected = self.expected.get(f["id"])
            if expected is None:
                expected = self.expected_slots.get((normalize(f["label"]), f["type"], f["slot"]))
            if expected is not None:
                # Punctuation and case in IDs, URLs, and answers are significant.
                same = " ".join(f["value"].split()) == " ".join(expected.split())
                # Custom comboboxes must close after committing a choice.
                if f["type"] == "combobox" and "myworkdayjobs.com" not in self.page.url:
                    same = same and await self.locator(f["id"]).get_attribute("aria-expanded") != "true"
                ledger = self.ledger.get(f["id"])
                if ledger is None:
                    ledger = next(
                        (
                            item
                            for item in self.ledger.values()
                            if item["label"] == f["label"]
                            and item.get("type") == f["type"]
                            and item.get("slot") == f["slot"]
                        ),
                        None,
                    )
                if ledger is not None:
                    ledger["verified"] = same
                if not same:
                    problems.append(f"Value not accepted: {f['label']}")
            if f["type"] == "file" and re.search(r"resume|cv\b", f["label"], re.IGNORECASE):
                if "myworkdayjobs.com" in self.page.url and self.uploaded and self.upload_verified:
                    continue
                self.upload_verified = self.resume_path.name in f["value"]
                if not self.upload_verified:
                    problems.append("Resume attachment was not accepted")
            if f["required"] and (
                not f["valid"] or not f["value"] or (f["type"] == "checkbox" and f["value"] != "true")
            ):
                problems.append(f"Required field incomplete: {f['label']}")
            if f["required"] and f["type"] != "file" and expected is None:
                problems.append(f"Required field has no verified answer: {f['label']}")
        for q in self.pending:
            problems.append(f"Needs an approved answer: {q['question']}")
        return {
            "ok": not problems,
            "problems": list(dict.fromkeys(problems)),
            "verified_fields": sum(x["verified"] for x in self.ledger.values()),
            "resume_attached": self.uploaded and self.upload_verified,
            "pending": self.pending,
        }

    async def click(self, control_id, auth_control=False):
        await self.scan()
        button = self.buttons.get(control_id)
        if not button:
            raise ValueError("Control is stale; inspect again")
        if FINAL.search(button["label"]) and not auth_control:
            raise ValueError("Use submit_application for final submission")
        entry = bool(ENTRY.fullmatch(button["label"])) and not any(
            f["type"] not in {"search", "hidden"} and not re.search(r"search|keyword|location", f["label"], re.I)
            for f in self.fields.values()
        )
        # Never let a generic navigation action activate a bare HTML submit control.
        if (
            not auth_control
            and not entry
            and button["type"] == "submit"
            and not re.search(r"next|continue|save|review|sign in|log in", button["label"], re.IGNORECASE)
        ):
            raise ValueError("This may submit the application; use submit_application")
        opener = self.page
        opened = []
        def on_popup(page):
            opened.append(page)
        opener.on("popup", on_popup)
        el = self.locator(control_id)
        permitted = auth_control or entry or bool(re.fullmatch(
            r"next|continue|save (?:and|&) continue|review(?: application)?", button["label"], re.I
        ))
        if permitted:
            el = await el.element_handle()
            await el.evaluate("el => {el.dataset.jpAuthControl='true';const f=el.closest('form');if(f)f.dataset.jpAuthControl='true'}")
        try:
            await el.click(timeout=8000)
            await opener.wait_for_timeout(250)
        finally:
            opener.remove_listener("popup", on_popup)
            if permitted:
                try:
                    await el.evaluate("el => {delete el.dataset.jpAuthControl;const f=el.closest('form');if(f)delete f.dataset.jpAuthControl}")
                except Exception:
                    pass
        if opened:
            self.page = opened[-1]
            if callback := getattr(self, "on_popup", None):
                await callback(self.page)
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        return await self.scan()

    async def proof(self):
        text = await self.page.locator("body").inner_text(timeout=5000)
        pattern = (
            r"thank you for (?:applying|your application)|application (?:has been |was )?(?:successfully )?submitted|"
            r"we (?:have )?received your application|your application (?:has been |was )?received|"
            r"application (?:has been |was )?received|successfully applied"
        )
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return {
            "url": self.page.url,
            "confirmation": text[max(0, match.start() - 60) : match.end() + 240],
            "title": await self.page.title(),
            "type": "explicit_confirmation_page",
        }

    async def rejection(self):
        text = await self.page.locator("body").inner_text(timeout=5000)
        match = re.search(
            r"(?:we )?(?:couldn.t|could not|unable to) submit your application|"
            r"application submission was (?:flagged|rejected)|"
            r"your form needs corrections|missing entry for required field|"
            r"your application (?:could not|couldn.t) be submitted", text, re.I
        )
        if match:
            return {
                "type": "explicit_rejection_page", "url": self.page.url,
                "evidence": text[max(0, match.start() - 20):match.end() + 400],
            }
        return None
