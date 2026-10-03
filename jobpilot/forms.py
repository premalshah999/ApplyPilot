"""Browser controls are referenced from observations, never model-generated selectors."""

import asyncio
import re
import time
from pathlib import Path

from . import widgets
from .answers import answer_key, normalize

SCAN = (Path(__file__).parent / "js" / "scan.js").read_text()

FINAL = re.compile(
    r"\bsubmit\b|send (?:my )?application|complete (?:my )?application|finish application", re.IGNORECASE
)
ENTRY = re.compile(
    r"^(?:apply(?: now| for (?:this|the) (?:job|position))?|start (?:your )?application|"
    r"continue application|i.m interested|apply manually|autofill with resume)$", re.I
)
AUTH = re.compile(r"password|verification code|one.time|authentication code|security code", re.IGNORECASE)
RESUME = re.compile(r"resume|cv\b|curriculum", re.IGNORECASE)
NOT_RESUME = re.compile(
    r"cover letter|transcript|portfolio|writing sample|certificat|reference", re.IGNORECASE
)
# Optional fields worth one model call; other optional unknowns are left blank.
USEFUL_OPTIONAL = re.compile(
    r"linkedin|github|portfolio|website|salary|compensation|start date|notice|hear about|how did you|referr",
    re.IGNORECASE,
)
# Single-page ATSs whose react-select comboboxes keep their proven live behavior.
WORKDAY_HOST = re.compile(r"myworkdayjobs\.com|myworkdaysite\.com", re.I)
SINGLE_PAGE_HOSTS = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com", re.I)
# Frames that never hold applicant fields: CAPTCHA/bot-check providers, analytics, media, chat.
PROVIDER_FRAME = re.compile(
    r"hcaptcha\.com|newassets\.hcaptcha|google\.com/recaptcha|recaptcha\.net|gstatic\.com/recaptcha|"
    r"challenges\.cloudflare\.com|turnstile|arkoselabs|funcaptcha|geetest|datadome|perimeterx|px-cdn|"
    r"captcha-delivery|googletagmanager|doubleclick|youtube\.com/embed|player\.vimeo|intercom|drift\.com|"
    r"zendesk|livechat",
    re.I,
)
MAX_FRAMES = 12
DEPENDENT_TYPES = {"radio", "radiogroup", "select", "dropdown", "pills", "checkbox", "combobox", "prompt"}


COOKIE_JS = r"""() => {
  const sels = ['#onetrust-accept-btn-handler','#truste-consent-button','#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    '.cc-allow','.cookie-accept','button[data-automation-id="legalNoticeAcceptButton"]','[data-testid="cookie-accept"]',
    'button[id*="accept" i][id*="cookie" i]','button[aria-label*="accept" i][aria-label*="cookie" i]'];
  for (const s of sels) { const b = document.querySelector(s); if (b && b.getClientRects().length) { b.click(); return true; } }
  for (const box of document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[aria-label*=cookie i]')) {
    if (!box.getClientRects().length) continue;
    for (const b of box.querySelectorAll('button,a,[role=button]')) {
      if (/^(accept( all)?( cookies)?|allow( all)?( cookies)?|i accept|agree|got it|ok)$/i.test((b.innerText||'').trim())) { b.click(); return true; }
    }
  }
  return false;
}"""


def has_value(field):
    """An unticked checkbox reports "false", which is not an answer."""
    if field["type"] == "checkbox":
        return field["value"] == "true"
    return bool(field["value"])


class NetworkMonitor:
    """Counts in-flight XHR/fetch so waits follow the application instead of fixed sleeps."""

    def __init__(self, target):
        # A page (or a private context). Never a shared desktop context: other workers' traffic
        # would keep this run waiting.
        self.inflight = {}
        self.last = time.monotonic()
        self.watch(target)

    def watch(self, target):
        target.on("request", self._start)
        target.on("requestfinished", self._end)
        target.on("requestfailed", self._end)

    def _start(self, request):
        if request.resource_type in {"xhr", "fetch", "document"}:
            self.inflight[request] = time.monotonic()
            self.last = time.monotonic()

    def _end(self, request):
        if self.inflight.pop(request, None) is not None:
            self.last = time.monotonic()

    def quiet(self, period=0.3):
        # Long-polling and beacons never finish; only recent requests mean the page is still working.
        now = time.monotonic()
        recent = [t for t in self.inflight.values() if now - t < 4]
        return not recent and now - self.last >= period


class FormSession:
    def __init__(self, page, resolver, resume_path, emit):
        self.page, self.resolver, self.resume_path, self.emit = page, resolver, resume_path, emit
        self.fields, self.buttons, self.frames = {}, {}, {}
        self.meta = {"headings": [], "errors": [], "automation": [], "busy": False, "title": "", "url": ""}
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        self.ledger = {}
        self.pending = []
        self.uploaded = False
        self.upload_verified = False
        self.network = None
        self.accept_prefilled = False

    async def scan(self):
        self.fields, self.buttons, self.frames = {}, {}, {}
        texts = []
        meta = {
            "headings": [],
            "errors": [],
            "automation": [],
            "dialogs": [],
            "busy": False,
            "title": "",
            "url": self.page.url,
        }
        for n, frame in enumerate(self.page.frames[:MAX_FRAMES]):
            try:
                if frame != self.page.main_frame:
                    if frame.is_detached() or PROVIDER_FRAME.search(frame.url or ""):
                        continue
                    owner = await frame.frame_element()
                    identity = await owner.evaluate("e=>[e.title,e.id,e.src?.slice(0,200)].join(' ')")
                    # CAPTCHA widgets (Skip, Refresh Challenge, accessibility) are never applicant
                    # fields or navigation; the CAPTCHA solver owns them.
                    if PROVIDER_FRAME.search(identity) or re.search(r"hcaptcha|recaptcha|challenge", identity, re.I):
                        continue
                data = await asyncio.wait_for(frame.evaluate(SCAN), timeout=5)
            except Exception:
                continue
            texts.append(data["text"])
            if n == 0:
                meta["title"] = data.get("title", "")
            for key in ("headings", "errors", "automation"):
                meta[key].extend(data.get(key, []))
            for d in data.get("dialogs", []):
                prefix = f"f{n}-{data['document_id']}-"
                meta["dialogs"].append(
                    {
                        "title": d.get("title", ""),
                        "text": d.get("text", ""),
                        "automation": d.get("automation", []),
                        "field_ids": [prefix + x for x in d.get("field_ids", [])],
                        "control_ids": [prefix + x for x in d.get("control_ids", [])],
                    }
                )
            meta["busy"] = meta["busy"] or data.get("busy", False)
            for kind in ("fields", "buttons"):
                for item in data[kind]:
                    if kind == "fields" and AUTH.search(item.get("label", "")) and item.get("value"):
                        # Codes and passwords never leave the page through an observation (model
                        # prompts, agent tools, traces); only that a value is present.
                        item["value"] = "********"
                    item["employer"] = self.resolver.employer
                    item["dom_id"] = item["id"]
                    item["id"] = f"f{n}-{data['document_id']}-{item['id']}"
                    item["frame_url"] = frame.url
                    self.frames[item["id"]] = frame
                    getattr(self, kind)[item["id"]] = item
        self.meta = meta
        # Checkbox lists answer one question; peers give context ("I do not want to answer" -> disability).
        boxes = {}
        for f in self.fields.values():
            if f["type"] == "checkbox":
                boxes.setdefault((f["frame_url"], f["section"], f["group"]), []).append(f)
        for peers in boxes.values():
            for f in peers:
                f["peers"] = " | ".join(x["label"] for x in peers if x is not f)[:600]
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
            **{k: meta[k] for k in ("headings", "errors", "automation", "dialogs", "busy", "title")},
        }

    def locator(self, field_id):
        item = self.fields.get(field_id) or self.buttons.get(field_id)
        if not item:
            raise ValueError("Unknown or stale field ID; inspect again")
        return self.frames[field_id].locator(f'[data-jp-id="{item["dom_id"]}"]')

    async def settle(self, timeout=6.0, quiet=0.3):
        """Wait for the page to stop loading: no busy indicator and no XHR in flight."""
        deadline = time.monotonic() + timeout
        stable = 0
        while time.monotonic() < deadline:
            try:
                busy = await self.page.evaluate(
                    """() => document.readyState !== 'complete' && document.readyState !== 'interactive' ||
                    [...document.querySelectorAll('[aria-busy=true],[data-automation-id="loadingSpinner"],.oj-progress-circle,.spinner,.loading-spinner,.loader')]
                    .some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')"""
                )
            except Exception:
                busy = True
            idle = not busy and (self.network is None or self.network.quiet(quiet))
            stable = stable + 1 if idle else 0
            if stable >= 2:
                return True
            await asyncio.sleep(0.1)
        return False

    def resume_fields(self):
        files = [f for f in self.fields.values() if f["type"] == "file"]
        named = [f for f in files if RESUME.search(f["label"]) and not NOT_RESUME.search(f["label"])]
        if named:
            return named[:1]
        unnamed = [f for f in files if not NOT_RESUME.search(f["label"])]
        return unnamed[:1] if len(files) == 1 else []

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
        frame = self.frames[field["id"]]
        kind = field["type"]
        committed = value
        if kind == "checkbox":
            want = str(value).strip().lower() in {"true", "yes", "checked", "1", "on"}
            if await el.evaluate("e => e.tagName === 'INPUT'"):
                # Label clicks, plus an "I Agree" dialog some portals open from the checkbox (Oracle).
                await self.set_checked(el, want)
                committed = "true" if want else "false"
            else:
                committed = await widgets.checkbox(el, value, field.get("widget", ""))
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
            committed = await widgets.select(el, value, field.get("widget", ""))
        elif kind == "radio":
            name = await el.get_attribute("name")
            radios = frame.locator('input[type="radio"]')
            found = False
            candidates = []
            for i in range(await radios.count()):
                radio = radios.nth(i)
                if name and await radio.get_attribute("name") != name:
                    continue
                labels = await radio.evaluate("e => [...e.labels].map(x=>x.innerText).join(' ').trim()")
                candidates.append((radio, labels))
            exact = [r for r, text in candidates if normalize(text) == normalize(value)]
            chosen = exact[0] if exact else None
            if chosen is None:
                match = widgets.best_option([t for _, t in candidates], value)
                chosen = next((r for r, t in candidates if t == match), None) if match else None
            if chosen is not None:
                try:
                    await self.set_checked(chosen, True)
                except Exception:
                    await chosen.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e).click()")
                found = True
            if not found:
                raise ValueError("Radio option is no longer present")
        elif kind == "radiogroup":
            radios = el.get_by_role("radio")
            texts = [x.strip() for x in await radios.all_inner_texts()]
            match = widgets.best_option(texts, value) if texts else None
            if match is not None:
                await radios.nth(texts.index(match)).click(timeout=4000)
            else:
                await el.get_by_role("radio", name=value, exact=True).click(timeout=4000)
        elif kind == "buttonchoice":
            await el.get_by_role("button", name=value, exact=True).click(timeout=4000)
        elif kind == "combobox" and SINGLE_PAGE_HOSTS.search(self.page.url):
            committed = await self._combobox_single_page(field, el, value)
        elif kind == "combobox":
            committed = await widgets.combobox(frame, el, value)
        elif kind == "dropdown":
            committed = await widgets.dropdown(frame, el, value)
        elif kind == "prompt":
            committed = await widgets.prompt(frame, el, value)
        elif kind == "date_parts":
            committed = await widgets.date_parts(frame, field, value)
        elif kind == "pills":
            committed = await widgets.pills(el, value)
        elif kind == "date":
            month, day, year = widgets.parse_date(value)
            await el.fill(f"{year}-{month}-{day or '01'}", timeout=4000)
        else:
            await el.fill(value, timeout=4000)
            try:
                await el.blur()
            except Exception:
                pass
        if kind == "combobox" and field["label"] == "Country":
            # Greenhouse's phone-country picker shows only the dial code after
            # an exact option was clicked (for example, "United States +1" -> "+1").
            displayed = await el.evaluate(
                "e=>e.closest('.select__control')?.querySelector('.select__single-value')?.textContent.trim() || ''"
            )
            if re.fullmatch(r"\+\d+", displayed) and str(committed).endswith(displayed):
                committed = displayed
        self.expected[field["id"]] = committed
        self.expected_labels[(normalize(field["label"]), kind)] = committed
        self.expected_slots[(normalize(field["label"]), kind, field.get("slot", 0))] = committed
        return committed

    async def _combobox_single_page(self, field, el, value):
        """Greenhouse/Lever/Ashby react-select comboboxes (the behavior proven on live runs)."""
        await el.click(timeout=4000)
        if await el.evaluate("e=>['INPUT','TEXTAREA'].includes(e.tagName)"):
            await el.fill(value, timeout=4000)
        options = self.frames[field["id"]].locator('[role="option"]')
        exact = options.filter(has_text=re.compile("^" + re.escape(value) + "$"))
        if re.search(r"location|city|currently based", field["label"], re.I):
            await options.first.wait_for(timeout=5000)
            labels = await options.all_text_contents()
            location = self.resolver.profile.location
            region = location.split(",")[1].strip() if "," in location else ""
            from .answers import US_STATES

            region = US_STATES.get(region.upper(), region).casefold()
            candidates = [
                label for label in labels if value.casefold() in label.casefold() and region and region in label.casefold()
            ]
            if candidates:
                value = candidates[0]
                exact = self.frames[field["id"]].get_by_role("option", name=value, exact=True)
        if await exact.count():
            await exact.first.click(timeout=5000)
        else:
            match = widgets.best_option([t.strip() for t in await options.all_text_contents()], value)
            if not match:
                raise ValueError("Option is not offered")
            await options.filter(has_text=re.compile("^" + re.escape(match) + "$")).first.click(timeout=5000)
            value = match
        return value

    async def discover_options(self, field):
        """Open custom selects once to read their real choices before the resolver decides."""
        frame = self.frames[field["id"]]
        el = self.locator(field["id"])
        try:
            if field["type"] == "combobox":
                await el.click(timeout=2000)
                scope = await widgets._popup_scope(el)
                opts = await widgets.wait_options(frame, scope, timeout=0.8)
                if not opts:
                    opts = await widgets.wait_options(frame, None, timeout=0.4)
                field["options"] = [o["text"] for o in opts][:250]
                await el.press("Escape")
            elif field["type"] == "dropdown":
                await el.click(timeout=2000)
                scope = await widgets._popup_scope(el)
                opts = await widgets.wait_options(frame, scope, timeout=1.5)
                field["options"] = [o["text"] for o in opts if not o["disabled"]][:400]
                # A long list may be a virtualized window; the driver can type to reach other options.
                field["options_partial"] = len(field["options"]) >= 10
                await frame.page.keyboard.press("Escape")
            elif field["type"] == "prompt":
                inp = el.locator("input").first
                await inp.click(timeout=2000)
                opts = await widgets.wait_options(
                    frame, None, timeout=1.2, selector="[data-automation-id=promptOption],[role=option]"
                )
                field["options"] = [o["text"] for o in opts][:250]
                # Search-driven: the listed options are only the first level.
                field["options_partial"] = True
                await frame.page.keyboard.press("Escape")
        except Exception:
            pass

    def _pending(self, field, reason):
        self.pending.append(
            {
                "question": field["label"],
                "options": field["options"],
                "key": answer_key(field),
                "reason": reason,
                "type": field["type"],
                "required": field["required"],
                "field_id": field["id"],
            }
        )

    @property
    def workday(self):
        return bool(WORKDAY_HOST.search(self.page.url))

    async def wait_upload(self):
        """Wait for the website's own upload acknowledgement, not a fixed delay."""
        greenhouse = self.page.locator('.file-upload[aria-labelledby="upload-label-resume"]')
        try:
            if await greenhouse.count():
                await greenhouse.locator(".file-upload__filename").wait_for(timeout=15000)
            elif self.workday:
                await self.page.get_by_text("Successfully Uploaded!", exact=True).wait_for(timeout=20000)
            else:
                await self.page.wait_for_timeout(150)
                await self.settle(timeout=12)
        except Exception:
            pass  # verify() reads the result back and reports a missing attachment.

    def preselected(self, field, experience):
        """Workday values the employer or its resume parser already set; recorded, not re-entered."""
        parsed = experience and has_value(field)  # An unticked checkbox reports "false".
        contact = bool(field["value"]) and (
            (field["label"] == "Country" and field["value"] == "United States of America")
            or (field["label"] == "Phone Device Type" and field["value"] == "Mobile")
            or (field["label"] == "Country Phone Code" and field["value"] == "United States of America (+1)")
        )
        if not (parsed or contact):
            return False
        key = (normalize(field["label"]), field["type"])
        self.expected[field["id"]] = field["value"]
        self.expected_labels[key] = field["value"]
        self.expected_slots[key + (field.get("slot", 0),)] = field["value"]
        self.ledger[field["id"]] = {
            "label": field["label"],
            "type": field["type"],
            "slot": field.get("slot", 0),
            "answer": field["value"],
            "evidence_ids": ["resume:parsed" if parsed else "employer:preselected"],
            "verified": False,
        }
        return True

    async def rematch(self, field):
        await self.scan()
        matches = [
            item
            for item in self.fields.values()
            if item["label"] == field["label"]
            and item["type"] == field["type"]
            and item.get("slot", 0) == field.get("slot", 0)
        ]
        if len(matches) != 1:
            raise ValueError("Question changed while filling")
        return matches[0]

    async def fill_current(self, accept_prefilled=None):
        if accept_prefilled is not None:
            self.accept_prefilled = accept_prefilled
        await self.scan()
        self.pending = []
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        if self.workday:
            body = await self._page_text()
            if self.resume_path.name in body and "Successfully Uploaded!" in body:
                self.uploaded = self.upload_verified = True
        # Upload first: parsing can overwrite fields and change the page.
        page_text = await self._page_text() if self.uploaded else ""
        for field in self.resume_fields():
            if self.workday and self.uploaded and self.upload_verified:
                continue
            # Some ATSs clear the input after upload and list the file instead.
            if not field["value"] and self.resume_path.name not in page_text:
                await self.locator(field["id"]).set_input_files(str(self.resume_path), timeout=15000)
                await self.wait_upload()
            self.uploaded = True
        await self.scan()
        experience = self.workday and bool(
            await self.page.get_by_role("heading", name="My Experience", exact=True).count()
        )
        handled, rounds = set(), 0
        while rounds < 3:
            rounds += 1
            await self.scan()
            todo = []
            for x in self.fields.values():
                if (
                    x["type"] in ("file", "password")
                    or x["id"] in handled
                    or AUTH.search(x["label"])
                    or (self.accept_prefilled and has_value(x) and x["valid"] and not x["required"])
                ):
                    continue
                if self.workday and self.preselected(x, experience):
                    handled.add(x["id"])
                    continue
                if experience and not x["required"] and not has_value(x) and not self.resolver.knows(x):
                    continue  # Optional Workday experience details without a profile answer stay blank.
                todo.append(x)
            if not todo:
                break
            for field in todo:
                if field["type"] in {"combobox", "dropdown", "prompt"} and not field["options"]:
                    await self.discover_options(field)
            ask = [
                x
                for x in todo
                if x["required"]
                or has_value(x)
                or USEFUL_OPTIONAL.search(x["label"])
                or x["type"] in {"checkbox"}
                or self.resolver.knows(x)
            ]
            handled.update(x["id"] for x in todo)
            answers = await self.resolver.resolve(ask) if ask else []
            changed = False
            for answer in answers:
                field = self.fields.get(answer.field_id)
                if not field:
                    continue
                if answer.disposition != "answer":
                    if field["required"]:
                        if self.accept_prefilled and has_value(field) and field["valid"]:
                            self.ledger[field["id"]] = {
                                "label": field["label"],
                                "type": field["type"],
                                "slot": field.get("slot", 0),
                                "answer": field["value"],
                                "evidence_ids": ["prefilled"],
                                "verified": True,
                            }
                            self.expected[field["id"]] = field["value"]
                        else:
                            self._pending(field, answer.reason)
                    continue
                if has_value(field) and widgets.same_value(field, field["value"], answer.value):
                    self.expected[field["id"]] = field["value"]
                    committed = field["value"]
                else:
                    try:
                        if self.workday:
                            # Workday replaces controls after each selection. Resolve the same
                            # visible question again before committing the next answer.
                            field = await self.rematch(field)
                        committed = await self.fill(field, answer.value)
                        changed = changed or field["type"] in DEPENDENT_TYPES
                    except Exception as exc:
                        self.emit(
                            "field_error",
                            f"Could not commit {field['label']}",
                            {"error_type": type(exc).__name__, "detail": str(exc)[:160]},
                        )
                        if field["required"]:
                            self._pending(field, f"Could not commit the answer: {str(exc)[:120]}")
                        continue
                self.ledger[field["id"]] = {
                    "label": field["label"],
                    "type": field["type"],
                    "slot": field.get("slot", 0),
                    "answer": committed,
                    "evidence_ids": answer.evidence_ids,
                    "verified": False,
                }
            if not changed:
                break
            await self.settle(timeout=3)
        # A later answer can hide a question ("I currently work here" hides "To"); drop those.
        await self.scan()
        self.pending = [p for p in self.pending if p.get("field_id") in self.fields]
        report = await self.verify()
        auth = [f for f in self.fields.values() if f["type"] == "password" or AUTH.search(f["label"])]
        report["auth_required"] = bool(auth)
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
        elif self.uploaded and self.workday:
            body = await self._page_text()
            self.upload_verified = (
                "Successfully Uploaded!" in body and self.resume_path.name in body
            ) or self.upload_verified
        for f in self.fields.values():
            expected = self.expected.get(f["id"])
            if expected is None:
                expected = self.expected_slots.get((normalize(f["label"]), f["type"], f.get("slot", 0)))
            if expected is not None:
                same = widgets.same_value(f, f["value"], expected)
                # Custom comboboxes must close after committing a choice.
                if f["type"] == "combobox" and not self.workday:
                    same = same and await self.locator(f["id"]).get_attribute("aria-expanded") != "true"
                ledger = self.ledger.get(f["id"])
                if ledger is None:
                    ledger = next(
                        (
                            item
                            for item in self.ledger.values()
                            if item["label"] == f["label"]
                            and item.get("type") == f["type"]
                            and item.get("slot") == f.get("slot", 0)
                        ),
                        None,
                    )
                if ledger is not None:
                    ledger["verified"] = same
                if not same:
                    problems.append(f"Value not accepted: {f['label']}")
            if f["type"] == "password" or AUTH.search(f["label"]):
                continue
            if f in self.resume_fields() or (f["type"] == "file" and RESUME.search(f["label"])):
                if self.workday and self.uploaded and self.upload_verified:
                    continue
                text = await self._page_text()
                self.upload_verified = self.resume_path.name in f["value"] or self.resume_path.name in text
                if not self.upload_verified:
                    problems.append("Resume attachment was not accepted")
                continue
            empty = not f["value"] or (f["type"] == "checkbox" and f["value"] != "true")
            if f["required"] and f["type"] != "file" and (not f["valid"] or empty):
                problems.append(f"Required field incomplete: {f['label']}")
            if f["required"] and f["type"] != "file" and expected is None:
                if not (self.accept_prefilled and has_value(f) and f["valid"]):
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

    async def _page_text(self):
        try:
            return await self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            return ""

    async def click(self, control_id, auth_control=False, step_control=False):
        await self.scan()
        button = self.buttons.get(control_id)
        if not button:
            raise ValueError("Control is stale; inspect again")
        allowed = auth_control or step_control
        if FINAL.search(button["label"]) and not allowed:
            raise ValueError("Use submit_application for final submission")
        entry = bool(ENTRY.fullmatch(button["label"])) and not any(
            f["type"] not in {"search", "hidden"} and not re.search(r"search|keyword|location", f["label"], re.I)
            for f in self.fields.values()
        )
        # Never let a generic navigation action activate a bare HTML submit control.
        if (
            not allowed
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
        # Marked controls pass the page's submit guard: auth/step controls, entry links, and
        # ordinary navigation labels.
        permitted = allowed or entry or bool(
            re.fullmatch(r"next|continue|save (?:and|&) continue|review(?: application)?", button["label"], re.I)
        )
        if permitted:
            el = await el.element_handle()
            await el.evaluate(
                "el => {el.dataset.jpAuthControl='true';const f=el.closest('form');if(f)f.dataset.jpAuthControl='true'}"
            )
        try:
            try:
                await el.click(timeout=6000)
            except Exception:
                # Cookie walls and modal backdrops intercept pointer events; clear them and retry.
                for frame in self.page.frames[:3]:
                    try:
                        await frame.evaluate(COOKIE_JS)
                    except Exception:
                        pass
                try:
                    await el.click(timeout=3000)
                except Exception:
                    # Last resort: dispatch the click on this exact element, never at screen coordinates.
                    await el.evaluate("e => e.click()")
            await opener.wait_for_timeout(120)
        finally:
            opener.remove_listener("popup", on_popup)
            if permitted:
                try:
                    await el.evaluate("el => {delete el.dataset.jpAuthControl;const f=el.closest('form');if(f)delete f.dataset.jpAuthControl}")
                except Exception:
                    pass
        # Only popups opened by this page: with a shared desktop browser, other workers' tabs
        # live in the same context and must never be adopted.
        if opened:
            self.page = opened[-1]
            if callback := getattr(self, "on_popup", None):
                await callback(self.page)
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        await self.settle(timeout=8)
        return await self.scan()

    async def proof(self):
        text = await self.page.locator("body").inner_text(timeout=5000)
        pattern = (
            r"thank you for (?:applying|your (?:job )?(?:application|submission))|"
            r"application (?:has been |was )?(?:successfully )?(?:submitted|received)|"
            r"we(?:'ve| have)? received your application|successfully (?:applied|submitted)|"
            r"your application (?:is |has been |was )?(?:complete|submitted|received|on its way)|"
            r"you(?:'ve| have) (?:successfully )?applied"
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
