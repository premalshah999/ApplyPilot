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

    def __init__(self, context):
        self.inflight = {}
        self.last = time.monotonic()
        context.on("request", self._start)
        context.on("requestfinished", self._end)
        context.on("requestfailed", self._end)

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
            "busy": False,
            "title": "",
            "url": self.page.url,
        }
        for n, frame in enumerate(self.page.frames):
            try:
                data = await frame.evaluate(SCAN)
            except Exception:
                continue
            texts.append(data["text"])
            if n == 0:
                meta["title"] = data.get("title", "")
            for key in ("headings", "errors", "automation"):
                meta[key].extend(data.get(key, []))
            meta["busy"] = meta["busy"] or data.get("busy", False)
            for kind in ("fields", "buttons"):
                for item in data[kind]:
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
        return {
            "fields": list(self.fields.values()),
            "controls": list(self.buttons.values()),
            "text": "\n".join(texts)[:22000],
            "url": self.page.url,
            **{k: meta[k] for k in ("headings", "errors", "automation", "busy", "title")},
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

    async def fill(self, field, value):
        el = self.locator(field["id"])
        frame = self.frames[field["id"]]
        kind = field["type"]
        committed = value
        if kind == "checkbox":
            committed = await widgets.checkbox(el, value, field.get("widget", ""))
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
                    await chosen.check(timeout=4000, force=field.get("widget") == "label")
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
        self.expected[field["id"]] = committed
        return committed

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

    async def fill_current(self, accept_prefilled=None):
        if accept_prefilled is not None:
            self.accept_prefilled = accept_prefilled
        await self.scan()
        self.pending = []
        # Upload first: parsing can overwrite fields and change the page.
        page_text = await self._page_text() if self.uploaded else ""
        for field in self.resume_fields():
            # Some ATSs clear the input after upload and list the file instead.
            if not field["value"] and self.resume_path.name not in page_text:
                await self.locator(field["id"]).set_input_files(str(self.resume_path), timeout=15000)
                await self.page.wait_for_timeout(150)
                await self.settle(timeout=12)
            self.uploaded = True
        handled, rounds = set(), 0
        while rounds < 3:
            rounds += 1
            await self.scan()
            todo = [
                x
                for x in self.fields.values()
                if x["type"] not in ("file", "password")
                and x["id"] not in handled
                and not AUTH.search(x["label"])
                and not (self.accept_prefilled and has_value(x) and x["valid"] and not x["required"])
            ]
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
                self.ledger[answer.field_id] = {
                    "label": field["label"],
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
        for f in self.fields.values():
            expected = self.expected.get(f["id"])
            if expected is not None:
                same = widgets.same_value(f, f["value"], expected)
                # Custom comboboxes must close after committing a choice.
                if f["type"] == "combobox":
                    same = same and await self.locator(f["id"]).get_attribute("aria-expanded") != "true"
                if f["id"] in self.ledger:
                    self.ledger[f["id"]]["verified"] = same
                if not same:
                    problems.append(f"Value not accepted: {f['label']}")
            if f["type"] == "password" or AUTH.search(f["label"]):
                continue
            if f in self.resume_fields() or (f["type"] == "file" and RESUME.search(f["label"])):
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
        # Never let a generic navigation action activate a bare HTML submit control.
        if (
            not allowed
            and button["type"] == "submit"
            and not re.search(r"next|continue|save|review|sign in|log in", button["label"], re.IGNORECASE)
        ):
            raise ValueError("This may submit the application; use submit_application")
        pages = set(self.page.context.pages)
        el = self.locator(control_id)
        if allowed:
            el = await el.element_handle()
            await el.evaluate("el => el.dataset.jpAuthControl='true'")
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
        finally:
            if allowed:
                try:
                    await el.evaluate("el => delete el.dataset.jpAuthControl")
                except Exception:
                    pass
        await self.page.wait_for_timeout(120)
        opened = [p for p in self.page.context.pages if p not in pages]
        if opened:
            self.page = opened[-1]
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        await self.settle(timeout=8)
        return await self.scan()

    async def proof(self):
        text = await self.page.locator("body").inner_text(timeout=5000)
        pattern = (
            r"thank you for (?:applying|your (?:job )?(?:application|submission))|"
            r"application (?:has been |was )?(?:successfully )?(?:submitted|received|complete)|"
            r"we (?:have )?received your application|successfully (?:applied|submitted)|"
            r"your application (?:is|has been) (?:complete|submitted|on its way)|"
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
