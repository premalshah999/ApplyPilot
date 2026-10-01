"""Browser controls are referenced from observations, never model-generated selectors."""

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
    const aria = el.getAttribute('aria-labelledby');
    if (aria) return aria.split(' ').map(x => document.getElementById(x)?.innerText || '').join(' ').trim();
    return el.getAttribute('aria-label') || [...(el.labels || [])].map(labelText).join(' ').trim()
      || el.closest('[role=group],fieldset')?.querySelector('legend')?.innerText
      || el.getAttribute('placeholder') || el.getAttribute('name') || el.id || 'Unlabelled field';
  };
  const section = el => el.closest('fieldset')?.querySelector('legend')?.innerText
    || el.closest('section')?.querySelector('h2,h3,h4')?.innerText || '';
  const seen = new Set(), fields = [];
  const controls = document.querySelectorAll('input,select,textarea,[role=combobox],[role=radiogroup]');
  for (const el of controls) {
    let type = el.getAttribute('role') || el.type || el.tagName.toLowerCase();
    if (el.disabled || el.readOnly || ['hidden','submit','button','reset'].includes(type)) continue;
    if (!visible(el) && type !== 'file') continue;
    if (el.closest('[role=combobox]') !== el && el.closest('[role=combobox]')) continue;
    let options = [], value = el.value || '', question = label(el), group = null;
    if (type === 'radio') {
      const key = el.name || id(el);
      if (seen.has(key)) continue;
      seen.add(key);
      group = [...document.querySelectorAll('input[type=radio]')].filter(x => x.name === el.name);
      const parent = el.closest('fieldset,[role=radiogroup]');
      question = parent ? (parent.querySelector('legend')?.innerText || label(parent)) : (el.name || question);
      options = group.map(label); value = group.find(x=>x.checked); value = value ? label(value) : '';
    } else if (type === 'radiogroup') {
      group = [...el.querySelectorAll('[role=radio]')];
      options = group.map(x=>x.innerText || label(x));
      value = group.find(x=>x.getAttribute('aria-checked')==='true')?.innerText || '';
    } else if (el.tagName === 'SELECT') {
      type = 'select'; options = [...el.options].filter(x=>!x.disabled && x.value !== '').map(x=>x.text);
      value = el.selectedOptions[0]?.text || '';
    } else if (type === 'checkbox') value = el.checked ? 'true' : 'false';
    else if (type === 'file') value = [...(el.files || [])].map(x=>x.name).join(', ');
    const req = !!el.required || el.getAttribute('aria-required')==='true' || /\*/.test(question)
      || (group && group.some(x=>x.required));
    fields.push({id:id(el),label:question.replace(/\s+/g,' ').replace(/\s*\*\s*$/,'').trim(),type,
      required:!!req, options, value, section:section(el), maxlength:el.maxLength || -1,
      valid: el.validity ? el.validity.valid : true});
  }
  const buttons = [...document.querySelectorAll('button,a,[role=button],input[type=submit]')]
    .filter(el=>visible(el) && !el.disabled).map(el=>({id:id(el),label:(el.innerText || el.value || label(el)).trim(),
      href:el.getAttribute('href') || '', type:el.form ? (el.type || '') : ''})).filter(x=>x.label);
  return {fields, buttons, document_id:window.__jpDocId, text:document.body.innerText.slice(0,20000), title:document.title};
}"""

FINAL = re.compile(
    r"\bsubmit\b|send (?:my )?application|complete (?:my )?application|finish application", re.IGNORECASE
)
AUTH = re.compile(r"password|verification code|one.time|authentication code|security code", re.IGNORECASE)


class FormSession:
    def __init__(self, page, resolver, resume_path, emit):
        self.page, self.resolver, self.resume_path, self.emit = page, resolver, resume_path, emit
        self.fields, self.buttons, self.frames = {}, {}, {}
        self.expected = {}
        self.ledger = {}
        self.pending = []
        self.uploaded = False
        self.upload_verified = False

    async def scan(self):
        self.fields, self.buttons, self.frames = {}, {}, {}
        texts = []
        for n, frame in enumerate(self.page.frames):
            try:
                data = await frame.evaluate(SCAN)
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

    async def fill(self, field, value):
        el = self.locator(field["id"])
        kind = field["type"]
        if kind == "checkbox":
            await el.set_checked(value.lower() == "true", timeout=4000)
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
                        await radio.check(timeout=4000)
                        found = True
                        break
            if not found:
                raise ValueError("Radio option is no longer present")
        elif kind == "radiogroup":
            await el.get_by_role("radio", name=value, exact=True).click(timeout=4000)
        elif kind == "combobox":
            await el.click(timeout=4000)
            if await el.evaluate("e=>['INPUT','TEXTAREA'].includes(e.tagName)"):
                await el.fill(value, timeout=4000)
            await self.frames[field["id"]].get_by_role("option", name=value, exact=True).click(timeout=5000)
        else:
            await el.fill(value, timeout=4000)
            await el.blur()
        self.expected[field["id"]] = value

    async def fill_current(self):
        await self.scan()
        self.pending = []
        # Upload first: parsing can overwrite fields and change the page.
        for field in list(self.fields.values()):
            if field["type"] == "file" and re.search(r"resume|cv\b", field["label"], re.IGNORECASE):
                if not field["value"]:
                    await self.locator(field["id"]).set_input_files(str(self.resume_path), timeout=15000)
                    await self.page.wait_for_timeout(350)
                self.uploaded = True
        await self.scan()
        # Observe custom option labels before asking the resolver to choose one.
        for field in list(self.fields.values()):
            if field["type"] == "combobox":
                try:
                    el = self.locator(field["id"])
                    await el.click(timeout=2000)
                    options = self.frames[field["id"]].get_by_role("option")
                    field["options"] = [x.strip() for x in await options.all_text_contents() if x.strip()][
                        :250
                    ]
                    await el.press("Escape")
                except Exception:
                    pass
        fields = [x for x in self.fields.values() if x["type"] not in ("file", "password")]
        answers = await self.resolver.resolve(fields)
        for answer in answers:
            field = self.fields[answer.field_id]
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
                await self.fill(field, answer.value)
                self.ledger[answer.field_id] = {
                    "label": field["label"],
                    "answer": answer.value,
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
        for f in self.fields.values():
            expected = self.expected.get(f["id"])
            if expected is not None:
                # Punctuation and case in IDs, URLs, and answers are significant.
                same = " ".join(f["value"].split()) == " ".join(expected.split())
                # Custom comboboxes must close after committing a choice.
                if f["type"] == "combobox":
                    same = same and await self.locator(f["id"]).get_attribute("aria-expanded") != "true"
                if f["id"] in self.ledger:
                    self.ledger[f["id"]]["verified"] = same
                if not same:
                    problems.append(f"Value not accepted: {f['label']}")
            if f["type"] == "file" and re.search(r"resume|cv\b", f["label"], re.IGNORECASE):
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

    async def click(self, control_id):
        await self.scan()
        button = self.buttons.get(control_id)
        if not button:
            raise ValueError("Control is stale; inspect again")
        if FINAL.search(button["label"]):
            raise ValueError("Use submit_application for final submission")
        # Never let a generic navigation action activate a bare HTML submit control.
        if button["type"] == "submit" and not re.search(
            r"next|continue|save|review|sign in|log in", button["label"], re.IGNORECASE
        ):
            raise ValueError("This may submit the application; use submit_application")
        pages = set(self.page.context.pages)
        await self.locator(control_id).click(timeout=8000)
        await self.page.wait_for_timeout(250)
        opened = [p for p in self.page.context.pages if p not in pages]
        if opened:
            self.page = opened[-1]
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        return await self.scan()

    async def proof(self):
        text = await self.page.locator("body").inner_text(timeout=5000)
        pattern = (
            r"thank you for applying|application (?:has been |was )?(?:successfully )?submitted|"
            r"we (?:have )?received your application|successfully applied"
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
