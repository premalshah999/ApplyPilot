"""CapSolver client. Detects the challenge from markup and iframe URLs, solves it, and hands the
token to the page the way the widget would (response fields in every frame + the site callback)."""

import asyncio
import base64
import time
from urllib.parse import parse_qs, urlsplit

import httpx

DETECT = r"""() => {
  const out = [];
  const add = x => out.push(x);
  for (const el of document.querySelectorAll('.g-recaptcha[data-sitekey],[data-sitekey].g-recaptcha')) {
    add({vendor: 'recaptcha', sitekey: el.dataset.sitekey, invisible: el.dataset.size === 'invisible',
         action: el.dataset.action || '', callback: el.dataset.callback || '', visible: !!el.getClientRects().length});
  }
  for (const f of document.querySelectorAll('iframe[src*="/recaptcha/"]')) {
    if (!/anchor/.test(f.src)) continue;
    const u = new URL(f.src);
    add({vendor: 'recaptcha', sitekey: u.searchParams.get('k'), invisible: u.searchParams.get('size') === 'invisible',
         enterprise: /enterprise/.test(u.pathname), action: u.searchParams.get('sa') || '',
         s: u.searchParams.get('s') || '', visible: !!f.getClientRects().length});
  }
  for (const s of document.querySelectorAll('script[src*="recaptcha"]')) {
    const u = new URL(s.src, location.href); const key = u.searchParams.get('render');
    if (key && key !== 'explicit') add({vendor: 'recaptcha', sitekey: key, v3: true, enterprise: /enterprise/.test(u.pathname)});
  }
  for (const el of document.querySelectorAll('.cf-turnstile[data-sitekey],[data-sitekey][class*=turnstile]')) {
    add({vendor: 'turnstile', sitekey: el.dataset.sitekey, action: el.dataset.action || '', cdata: el.dataset.cdata || '',
         callback: el.dataset.callback || '', visible: !!el.getClientRects().length});
  }
  for (const f of document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]')) {
    const m = f.src.match(/\/(0x[0-9A-Za-z_-]{10,})\//); if (m) add({vendor: 'turnstile', sitekey: m[1], visible: true});
  }
  for (const f of document.querySelectorAll('iframe[src*="hcaptcha.com"]')) add({vendor: 'hcaptcha', visible: !!f.getClientRects().length});
  for (const img of document.querySelectorAll('img[src*="captcha" i],img[id*="captcha" i],img[alt*="captcha" i]')) {
    if (!img.getClientRects().length) continue;
    const box = img.closest('form,div,td') || document.body;
    const input = box.querySelector('input[name*="captcha" i],input[id*="captcha" i],input[type=text]');
    if (input) { input.dataset.jpCaptcha = input.dataset.jpCaptcha || ('c' + Math.random().toString(36).slice(2, 9));
      img.dataset.jpCaptchaImg = input.dataset.jpCaptcha;
      add({vendor: 'image', input: input.dataset.jpCaptcha, visible: true}); }
  }
  return out;
}"""

INJECT_RECAPTCHA = r"""(token) => {
  let n = 0;
  for (const el of document.querySelectorAll('textarea[name="g-recaptcha-response"],[id^="g-recaptcha-response"]')) {
    el.value = token; el.innerHTML = token; n++;
    el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  const called = [];
  const visit = (obj, depth) => {
    if (!obj || typeof obj !== 'object' || depth > 4) return;
    for (const key of Object.keys(obj)) {
      const v = obj[key];
      if (key === 'callback' && typeof v === 'function' && !called.includes(v)) { called.push(v); try { v(token); } catch (_) {} }
      else if (key === 'callback' && typeof v === 'string' && typeof window[v] === 'function') { try { window[v](token); } catch (_) {} }
      else if (v && typeof v === 'object') visit(v, depth + 1);
    }
  };
  try { if (window.___grecaptcha_cfg) visit(window.___grecaptcha_cfg.clients, 0); } catch (_) {}
  for (const el of document.querySelectorAll('[data-callback]')) {
    const name = el.dataset.callback; if (typeof window[name] === 'function') { try { window[name](token); } catch (_) {} }
  }
  return n;
}"""

HOOK_V3 = r"""(token) => {
  const wrap = g => { if (!g || g.__jpHooked) return; const orig = g.execute;
    g.execute = function() { return Promise.resolve(token); }; g.__jpHooked = true; g.__jpOrig = orig; };
  if (window.grecaptcha) { wrap(window.grecaptcha); if (window.grecaptcha.enterprise) wrap(window.grecaptcha.enterprise); }
  return !!window.grecaptcha;
}"""

INJECT_TURNSTILE = r"""({token, callback}) => {
  let n = 0;
  for (const el of document.querySelectorAll('[name="cf-turnstile-response"],[name="g-recaptcha-response"]')) {
    el.value = token; n++; el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  const names = [callback, ...[...document.querySelectorAll('.cf-turnstile[data-callback]')].map(e => e.dataset.callback)];
  for (const name of names) if (name && typeof window[name] === 'function') { try { window[name](token); } catch (_) {} }
  return n;
}"""


async def detect(page):
    found = []
    for frame in page.frames:
        try:
            for item in await frame.evaluate(DETECT):
                item["frame"] = frame
                found.append(item)
        except Exception:
            continue
    # Prefer explicit widgets, then iframe-derived ones; drop duplicates by vendor+sitekey.
    unique, seen = [], set()
    for item in found:
        key = (item["vendor"], item.get("sitekey"), item.get("input"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def task_for(item, url):
    vendor = item["vendor"]
    if vendor == "recaptcha":
        enterprise = item.get("enterprise")
        if item.get("v3"):
            task = {
                "type": "ReCaptchaV3EnterpriseTaskProxyLess" if enterprise else "ReCaptchaV3TaskProxyLess",
                "pageAction": item.get("action") or "submit",
            }
        else:
            task = {
                "type": "ReCaptchaV2EnterpriseTaskProxyLess" if enterprise else "ReCaptchaV2TaskProxyLess"
            }
            if item.get("invisible"):
                task["isInvisible"] = True
            if item.get("action"):
                task["pageAction"] = item["action"]
            if enterprise and item.get("s"):
                task["enterprisePayload"] = {"s": item["s"]}
        task.update({"websiteURL": url, "websiteKey": item["sitekey"]})
        return task
    if vendor == "turnstile":
        task = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": url, "websiteKey": item["sitekey"]}
        meta = {k: item[k] for k in ("action", "cdata") if item.get(k)}
        if meta:
            task["metadata"] = meta
        return task
    return None


async def _run_task(client, key, task, max_seconds, emit):
    started = time.monotonic()
    r = await client.post("/createTask", json={"clientKey": key, "task": task})
    r.raise_for_status()
    result = r.json()
    if result.get("errorId"):
        return None, result.get("errorCode", "CapSolver rejected task")
    emit("captcha", "Challenge sent to CapSolver", {"type": task["type"]})
    task_id = result.get("taskId")
    while time.monotonic() - started < max_seconds:
        if result.get("status") == "ready":
            return result.get("solution", {}), ""
        await asyncio.sleep(1.5)
        r = await client.post("/getTaskResult", json={"clientKey": key, "taskId": task_id})
        r.raise_for_status()
        result = r.json()
        if result.get("errorId"):
            return None, result.get("errorCode", "Solver failed")
    return None, "Challenge exceeded solver budget"


async def solve(config, page, emit, max_seconds=60, items=None):
    if not config.capsolver_api_key:
        return {"solved": False, "reason": "CapSolver is not configured"}
    items = items if items is not None else await detect(page)
    supported = [
        i
        for i in items
        if i["vendor"] in {"recaptcha", "turnstile", "image"} and (i.get("sitekey") or i.get("input"))
    ]
    if not supported:
        if any(i["vendor"] == "hcaptcha" for i in items):
            return {"solved": False, "reason": "hCaptcha is not supported by CapSolver"}
        return {"solved": False, "reason": "No supported captcha detected"}
    # Visible checkbox widgets first; score-based v3 last.
    item = sorted(supported, key=lambda i: (i.get("v3", False), not i.get("visible", False)))[0]
    frame = item["frame"]
    async with httpx.AsyncClient(base_url="https://api.capsolver.com", timeout=20) as client:
        if item["vendor"] == "image":
            img = frame.locator(f'img[data-jp-captcha-img="{item["input"]}"]').first
            body = await img.screenshot(timeout=5000, type="png")
            task = {"type": "ImageToTextTask", "body": base64.b64encode(body).decode()}
            solution, error = await _run_task(client, config.capsolver_api_key, task, max_seconds, emit)
            if not solution:
                return {"solved": False, "reason": error}
            await frame.locator(f'[data-jp-captcha="{item["input"]}"]').fill(solution.get("text", ""))
            return {"solved": True, "reason": "Image text entered"}
        task = task_for(item, page.url)
        solution, error = await _run_task(client, config.capsolver_api_key, task, max_seconds, emit)
        if not solution:
            return {"solved": False, "reason": error}
        token = solution.get("gRecaptchaResponse") or solution.get("token")
        if not token:
            return {"solved": False, "reason": "Solver returned no token"}
        applied = 0
        for f in page.frames:
            try:
                if item["vendor"] == "turnstile":
                    applied += await f.evaluate(
                        INJECT_TURNSTILE, {"token": token, "callback": item.get("callback")}
                    )
                else:
                    if item.get("v3"):
                        await f.evaluate(HOOK_V3, token)
                    applied += await f.evaluate(INJECT_RECAPTCHA, token)
            except Exception:
                continue
        return {"solved": bool(applied) or bool(item.get("v3")), "reason": "Token delivered to the page"}


def sitekey_from_url(url):
    return parse_qs(urlsplit(url).query).get("k", [""])[0]


def is_blocking(items):
    """Visible checkbox/image challenges block progress; invisible and v3 ones run at submit time."""
    return [i for i in items if i.get("visible") and not i.get("invisible") and not i.get("v3")]


def describe(items):
    return ", ".join(sorted({i["vendor"] + (" v3" if i.get("v3") else "") for i in items})) or "none"


__all__ = ["detect", "solve", "is_blocking", "describe", "task_for", "sitekey_from_url"]
