"""Drivers for the controls real ATS forms use. Each commits an observed option and reads it back."""

import asyncio
import re
import time
from datetime import date

OPTION_SELECTOR = (
    "[role=option],[role=menuitem],[role=menuitemradio],[role=treeitem],.select2-results__option,"
    ".chosen-results li,.ui-menu-item,.pac-item,.oj-listbox-result,[data-automation-id=promptOption],"
    "[data-automation-id=menuItem]"
)

VISIBLE_OPTIONS = r"""(args) => {
  const root = (args.scope && document.getElementById(args.scope)) || document;
  const out = [], seen = new Set();
  for (const el of root.querySelectorAll(args.selector)) {
    if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') continue;
    // Nested matches (option inside menuitem) would duplicate the same choice.
    if ([...seen].some(p => p.contains(el))) continue;
    seen.add(el);
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    el.dataset.jpOpt = el.dataset.jpOpt || ('o' + Math.random().toString(36).slice(2, 10));
    out.push({id: el.dataset.jpOpt, text, disabled: el.getAttribute('aria-disabled') === 'true',
      leaf: !el.querySelector('[data-automation-id=promptIcon],[aria-haspopup],[data-uxi-multiselectlistitem-hasChildren=true]')
        && el.getAttribute('aria-haspopup') !== 'true' && el.dataset.hasChildren !== 'true'});
  }
  return out;
}"""

US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "PR": "Puerto Rico",
}
ALIASES = [
    {"united states", "united states of america", "usa", "us", "u s", "u s a", "america"},
    {"united kingdom", "uk", "u k", "great britain", "england"},
    {"yes", "y", "true"},
    {"no", "n", "false"},
    {"mobile", "cell", "cell phone", "mobile phone", "cellular"},
    {
        "bachelor s degree",
        "bachelors",
        "bachelor",
        "bachelor s",
        "ba",
        "bs",
        "b s",
        "bsc",
        "b sc",
        "btech",
        "b tech",
        "be",
        "b e",
        "bachelor of science",
        "bachelor of arts",
        "bachelor of engineering",
        "bachelor of technology",
        "bachelors degree",
        "undergraduate degree",
    },
    {
        "master s degree",
        "masters",
        "master",
        "master s",
        "ms",
        "m s",
        "ma",
        "msc",
        "m sc",
        "mba",
        "meng",
        "m eng",
        "mtech",
        "m tech",
        "master of science",
        "master of arts",
        "master of engineering",
        "master of business administration",
        "master of technology",
        "masters degree",
        "graduate degree",
    },
    {"doctorate", "phd", "ph d", "doctor of philosophy", "doctoral degree", "doctorate degree"},
    {"associate s degree", "associates", "associate", "associate s"},
    {"high school diploma", "high school", "ged", "high school ged"},
]
for _code, _name in US_STATES.items():
    ALIASES.append({_code.lower(), _name.lower()})


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s+]", " ", str(text).lower())).strip()


def _alias_group(text):
    n = norm(text)
    for group in ALIASES:
        if n in group:
            return group
    return {n}


def best_option(options, value):
    """Choose one observed option for a desired value. Ambiguity returns None, never a guess."""
    if not options:
        return None
    labels = [o["text"] if isinstance(o, dict) else o for o in options]
    want = norm(value)
    if not want:
        return None
    exact = [i for i, x in enumerate(labels) if norm(x) == want]
    if len(exact) >= 1:
        return options[exact[0]]
    group = _alias_group(value)
    alias = [i for i, x in enumerate(labels) if norm(x) in group]
    if len(alias) == 1:
        return options[alias[0]]
    # "United States of America (+1)" for "+1"; "Mobile" for "Mobile Phone".
    contained = [
        i
        for i, x in enumerate(labels)
        if re.search(r"(^|\s)" + re.escape(want) + r"($|\s)", norm(x))
        or (norm(x) and re.search(r"(^|\s)" + re.escape(norm(x)) + r"($|\s)", want) and len(norm(x)) > 2)
    ]
    if len(contained) == 1:
        return options[contained[0]]
    if contained:
        starts = [i for i in contained if norm(labels[i]).startswith(want)]
        if len(starts) == 1:
            return options[starts[0]]
        shortest = sorted(contained, key=lambda i: len(labels[i]))
        if len(norm(labels[shortest[0]])) < len(norm(labels[shortest[1]])) and norm(
            labels[shortest[0]]
        ).startswith(want):
            return options[shortest[0]]
    for alt in group - {want}:
        hits = [i for i, x in enumerate(labels) if re.search(r"(^|\s)" + re.escape(alt) + r"($|\s)", norm(x))]
        if len(hits) == 1:
            return options[hits[0]]
    return None


def same_value(field, actual, expected):
    a, e = " ".join(str(actual).split()), " ".join(str(expected).split())
    if a == e:
        return True
    kind, label = field.get("type", ""), field.get("label", "")
    if kind in {"dropdown", "pills", "combobox", "select", "radio", "radiogroup"}:
        return norm(a) == norm(e) or (bool(norm(e)) and norm(a) in _alias_group(e))
    if kind == "prompt":
        return bool(norm(e)) and norm(e) in norm(a)
    if kind in {"tel", "date_parts", "date"} or re.search(r"phone|mobile|telephone|date", label, re.I):
        digits_a, digits_e = re.sub(r"\D", "", a), re.sub(r"\D", "", e)
        return bool(digits_e) and (digits_a == digits_e or digits_a.endswith(digits_e))
    if kind == "url" or re.search(r"linkedin|website|url|github|portfolio", label, re.I):
        return a.rstrip("/").lower().removeprefix("https://").removeprefix("http://").removeprefix(
            "www."
        ) == e.rstrip("/").lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return False


def parse_date(value):
    """Return (month, day, year) strings; missing precision stays empty."""
    v = str(value).strip().lower()
    if v in {"today", "now"}:
        t = date.today()
        return f"{t.month:02d}", f"{t.day:02d}", f"{t.year:04d}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", v)
    if m:
        return f"{int(m[2]):02d}", f"{int(m[3]):02d}" if m[3] else "", m[1]
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return f"{int(m[1]):02d}", f"{int(m[2]):02d}", m[3]
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", v)
    if m:
        return f"{int(m[1]):02d}", "", m[2]
    m = re.fullmatch(r"(\d{4})", v)
    if m:
        return "", "", m[1]
    raise ValueError("Unsupported date format")


def date_text(field, value):
    month, day, year = parse_date(value)
    parts = {"month": month, "day": day, "year": year}
    kinds = [p["kind"] for p in field.get("parts", [])] or ["month", "day", "year"]
    if any(not parts[k] for k in kinds):
        raise ValueError("The applicant date does not have the precision this field requires")
    return "/".join(parts[k] for k in kinds)


async def visible_options(frame, scope=None, selector=OPTION_SELECTOR):
    try:
        return await frame.evaluate(VISIBLE_OPTIONS, {"scope": scope, "selector": selector})
    except Exception:
        return []


async def wait_options(frame, scope=None, timeout=3.0, selector=OPTION_SELECTOR, settle=0.15):
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        opts = await visible_options(frame, scope, selector)
        if opts and [o["text"] for o in opts] == [o["text"] for o in last]:
            return opts
        last = opts
        await asyncio.sleep(settle)
    return last


async def _popup_scope(el):
    try:
        return await el.evaluate(
            "e => e.getAttribute('aria-controls') || e.getAttribute('aria-owns') || "
            "(e.querySelector('[aria-controls]')?.getAttribute('aria-controls')) || null"
        )
    except Exception:
        return None


async def click_option(frame, option):
    await frame.locator(f'[data-jp-opt="{option["id"]}"]').first.click(timeout=4000)


async def dropdown(frame, el, value):
    """Button with aria-haspopup=listbox (Workday, MUI). Returns the committed option text."""
    await el.click(timeout=4000)
    scope = await _popup_scope(el)
    opts = await wait_options(frame, scope, timeout=3)
    if not opts and scope:
        opts = await wait_options(frame, None, timeout=1)
        scope = None
    choice = best_option([o for o in opts if not o["disabled"]], value)
    # Long virtualized lists render a window of options; type-ahead moves it ("NY" -> "new york").
    for typed in [str(value)] + sorted(_alias_group(value) - {norm(value)}, key=len, reverse=True)[:3]:
        if choice:
            break
        await asyncio.sleep(0.9)  # let the widget's type-ahead buffer reset
        await frame.page.keyboard.type(typed[:24], delay=12)
        await asyncio.sleep(0.35)
        opts = await wait_options(frame, scope, timeout=1.5)
        choice = best_option([o for o in opts if not o["disabled"]], value)
    if not choice:
        await frame.page.keyboard.press("Escape")
        raise ValueError("The desired option is not offered")
    await click_option(frame, choice)
    for _ in range(20):
        text = norm(await el.inner_text(timeout=2000))
        if text and (text == norm(choice["text"]) or norm(choice["text"]) in text):
            return choice["text"]
        await asyncio.sleep(0.1)
    return choice["text"]


async def combobox(frame, el, value, options_hint=None):
    """ARIA combobox: input or div. Types to search when editable, then commits a real option."""
    await el.click(timeout=4000)
    editable = await el.evaluate(
        "e => ['INPUT','TEXTAREA'].includes(e.tagName) && !e.readOnly && !e.disabled"
    )
    scope = await _popup_scope(el)
    opts = []
    if editable:
        current = await el.input_value()
        listed = await wait_options(frame, scope, timeout=0.4)
        if not best_option(listed, value):
            if current:
                await el.fill("")
            await el.fill(str(value), timeout=4000)
        opts = await wait_options(frame, scope, timeout=3)
    else:
        opts = await wait_options(frame, scope, timeout=3)
    if not opts and scope:
        opts = await wait_options(frame, None, timeout=1)
        scope = None
    choice = best_option([o for o in opts if not o["disabled"]], value)
    if not choice and editable and opts:
        # Search may expand abbreviations ("NYC" -> "New York, NY"): accept one remaining candidate.
        real = [o for o in opts if not re.search(r"no (results|matches|options)|not found", o["text"], re.I)]
        if len(real) == 1:
            choice = real[0]
    if not choice and not editable:
        await frame.page.keyboard.type(str(value)[:24], delay=12)
        opts = await wait_options(frame, scope, timeout=1.5)
        choice = best_option(opts, value)
    if not choice:
        await el.press("Escape")
        raise ValueError("No matching option is offered")
    await click_option(frame, choice)
    await asyncio.sleep(0.1)
    return choice["text"]


async def prompt(frame, container, value):
    """Workday multiselect prompt: search, then commit a leaf; categories are opened once."""
    inp = container.locator("input").first
    await inp.click(timeout=4000)
    await inp.fill(str(value), timeout=4000)
    await inp.press("Enter")
    opts = await wait_options(
        frame, None, timeout=3, selector="[data-automation-id=promptOption],[role=option]"
    )
    real = [o for o in opts if not re.search(r"^no items|no results|no matches", o["text"], re.I)]
    choice = best_option(real, value) or (real[0] if len(real) == 1 else None)
    if not choice:
        # Browse from the top: the value can be a leaf under a category ("Job Board" > "LinkedIn").
        await inp.fill("")
        await inp.press("Enter")
        opts = await wait_options(
            frame, None, timeout=2, selector="[data-automation-id=promptOption],[role=option]"
        )
        for category in [o for o in opts if not o["leaf"]][:12]:
            await click_option(frame, category)
            inner = await wait_options(
                frame, None, timeout=2, selector="[data-automation-id=promptOption],[role=option]"
            )
            choice = best_option([o for o in inner if o["leaf"]], value)
            if choice:
                break
            back = frame.locator("[data-automation-id=backButton],[aria-label=Back]").first
            if await back.count():
                await back.click(timeout=2000)
            else:
                break
    if not choice:
        await inp.press("Escape")
        raise ValueError("No matching option is offered")
    await click_option(frame, choice)
    if not choice["leaf"]:
        inner = await wait_options(
            frame, None, timeout=2, selector="[data-automation-id=promptOption],[role=option]"
        )
        leaf = best_option([o for o in inner if o["leaf"]], value)
        if not leaf:
            raise ValueError("The selected category needs a specific choice")
        await click_option(frame, leaf)
        choice = leaf
    await asyncio.sleep(0.25)
    try:
        await frame.page.keyboard.press("Escape")
    except Exception:
        pass
    return choice["text"]


async def date_parts(frame, field, value):
    text = date_text(field, value)
    values = dict(zip([p["kind"] for p in field["parts"]], text.split("/"), strict=True))
    for part in field["parts"]:
        loc = frame.locator(f'[data-jp-id="{part["dom_id"]}"]')
        await loc.click(timeout=4000)
        await loc.press("Control+A")
        await loc.press_sequentially(values[part["kind"]], delay=25)
    await asyncio.sleep(0.1)
    return text


async def pills(el, value):
    buttons = el.locator("button[aria-pressed],[role=button][aria-pressed]")
    texts = [x.strip() for x in await buttons.all_inner_texts()]
    choice = best_option(texts, value)
    if choice is None:
        raise ValueError("No matching choice is offered")
    target = buttons.nth(texts.index(choice))
    if await target.get_attribute("aria-pressed") != "true":
        await target.click(timeout=4000)
    return choice


async def select(el, value, widget=""):
    options = await el.evaluate(
        "e => [...e.options].filter(o => !o.disabled && o.value !== '').map(o => o.text.trim())"
    )
    choice = best_option(options, value)
    if choice is None:
        raise ValueError("No matching option is offered")
    if widget == "proxy":
        # select2/chosen hide the native select; set it and notify the plugin.
        await el.evaluate(
            """(e, label) => {
              const o = [...e.options].find(x => x.text.trim() === label);
              e.value = o.value; o.selected = true;
              e.dispatchEvent(new Event('input', {bubbles: true}));
              e.dispatchEvent(new Event('change', {bubbles: true}));
              if (window.jQuery) { try { window.jQuery(e).trigger('change').trigger('chosen:updated'); } catch (_) {} }
            }""",
            choice,
        )
    else:
        await el.select_option(label=choice, timeout=4000)
    return choice


async def checkbox(el, value, widget=""):
    want = str(value).strip().lower() in {"true", "yes", "checked", "1", "on"}
    is_input = await el.evaluate("e => e.tagName === 'INPUT'")
    if is_input:
        try:
            await el.set_checked(want, timeout=3000, force=widget == "label")
        except Exception:
            if await el.is_checked() != want:
                await el.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e).click()")
    elif (await el.get_attribute("aria-checked") == "true") != want:
        await el.click(timeout=4000)
    return "true" if want else "false"
