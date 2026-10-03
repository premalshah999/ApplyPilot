"""Bounded CAPTCHA automation; solver output is never an application receipt.

CapSolver handles documented token tasks. Optional 2Captcha CoordinatesTask sends
only the isolated challenge image, never a screenshot of the application form.
"""

import asyncio
import base64
import hashlib
import re

import httpx
from playwright.async_api import Error as PlaywrightError

# Capture function callbacks and invisible execute() promises before site scripts
# run. Secrets remain inside the page; observations contain parameters only.
HOOK = r"""(() => {
 if(window.__jpCaptcha) return;
 const entries=[]; window.__jpCaptcha={entries};
 const wrap=(api,kind,enterprise=false)=>{
  if(!api || api.__jpWrapped) return;
  try {Object.defineProperty(api,'__jpWrapped',{value:true});} catch {return;}
  if(api.render){const original=api.render;
   api.render=function(container,opts={}){
    const id=original.apply(this,arguments);
    entries.push({kind,enterprise,id,key:opts.sitekey,action:opts.action,
      cdata:opts.cData,callback:opts.callback,s:opts.s}); return id;
   };
  }
  if(api.execute){const original=api.execute;
   api.execute=function(id,opts={}){
    let item=entries.find(e=>e.kind===kind && e.id===id);
    if(!item){item={kind,enterprise,id,key:typeof id==='string'?id:null}; entries.push(item);}
    item.action=opts.action||item.action; item.pending=true;
    const result=original.apply(this,arguments);
    if(result?.then){return Promise.race([result,new Promise(resolve=>{item.resolve=resolve;})]);}
    return result;
   };
  }
 };
 const setup=()=>{wrap(window.grecaptcha,'recaptcha');wrap(window.grecaptcha?.enterprise,'recaptcha',true);wrap(window.turnstile,'turnstile');};
 setup();setInterval(setup,50);
})()"""

DETECT = r"""() => {
 const captured=(window.__jpCaptcha?.entries||[]).filter(e=>e.key && !e.delivered);
 if(captured.length){const e=captured.find(x=>x.pending)||captured[0];
  return {kind:e.kind,key:e.key,id:e.id,enterprise:e.enterprise,v3:!!e.action,
    action:e.action,cdata:e.cdata,s:e.s};}
 for(const [selector,kind] of [['.g-recaptcha','recaptcha'],['.cf-turnstile','turnstile']]){
  const el=document.querySelector(selector);
  if(el?.dataset.sitekey) return {kind,key:el.dataset.sitekey,callback:el.dataset.callback,
    action:el.dataset.action,cdata:el.dataset.cdata,s:el.dataset.s};
 }
 for(const el of document.querySelectorAll('iframe[src]')) {
  try {const u=new URL(el.src);
   if(/(?:google\.com|recaptcha\.net)$/.test(u.hostname) && /recaptcha\/(?:api2|enterprise)\/anchor/.test(u.pathname))
    return {kind:'recaptcha',key:u.searchParams.get('k'),enterprise:u.pathname.includes('enterprise')};
  } catch {}
 }
 return null;
}"""

DELIVER = r"""({token,info}) => {
 let used=false;
 const selector=info.kind==='turnstile'?'[name="cf-turnstile-response"]':'[name="g-recaptcha-response"]';
 for(const el of document.querySelectorAll(selector)){
  el.value=token;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));used=true;
 }
 for(const e of window.__jpCaptcha?.entries||[]){
  if(e.kind!==info.kind || e.key!==info.key || (info.id!==undefined && e.id!==info.id))continue;
  if(e.resolve){e.resolve(token);used=true;}
  if(typeof e.callback==='function'){e.callback(token);used=true;}
  else if(typeof e.callback==='string' && typeof window[e.callback]==='function'){window[e.callback](token);used=true;}
  e.delivered=true;
 }
 if(info.callback){let fn=window;for(const part of info.callback.split('.'))fn=fn?.[part];if(typeof fn==='function'){fn(token);used=true;}}
 // Existing Chrome tabs may predate our init script. reCAPTCHA stores their callbacks here.
 if(info.kind==='recaptcha' && !window.__jpCaptcha?.entries?.length){
  const seen=new Set();const walk=(o,depth)=>{if(!o||typeof o!=='object'||depth>6||seen.has(o))return;seen.add(o);
   for(const k of Object.keys(o)){let v;try{v=o[k];}catch{continue;}
    if(k==='callback' && typeof v==='function'){v(token);used=true;}else if(v && typeof v==='object')walk(v,depth+1);
   }};walk(window.___grecaptcha_cfg?.clients,0);
 }
 return used;
}"""


async def task_result(base, key, task, timeout, emit):
    """Keep credentials, task bodies and provider error descriptions out of logs."""
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(base_url=base, timeout=15) as client:
                response = await client.post("/createTask", json={"clientKey": key, "task": task})
                response.raise_for_status()
                result = response.json()
                task_id = result.get("taskId")
                emit(
                    "captcha",
                    "CAPTCHA solver requested",
                    {"provider": base.split("//")[1], "type": task["type"]},
                )
                while True:
                    if result.get("errorId"):
                        code = str(result.get("errorCode", "PROVIDER_ERROR"))
                        return None, code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else "PROVIDER_ERROR"
                    if result.get("status") == "ready":
                        return result.get("solution", {}), None
                    if not task_id:
                        return None, "MISSING_TASK_ID"
                    await asyncio.sleep(3)
                    response = await client.post("/getTaskResult", json={"clientKey": key, "taskId": task_id})
                    response.raise_for_status()
                    result = response.json()
    except TimeoutError:
        return None, "SOLVER_TIMEOUT"
    except (httpx.HTTPError, ValueError, TypeError):
        return None, "SOLVER_CONNECTION_ERROR"


async def challenge_frame(page):
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            owner = await frame.frame_element()
            identity = await owner.evaluate("e=>[e.title,e.src?.slice(0,250)].join(' ')")
            box = await owner.bounding_box()
            if not box or box["width"] < 250 or box["height"] < 150 or not await owner.is_visible():
                continue
            if re.search(r"hcaptcha|recaptcha|challenge", identity, re.I):
                return frame, owner, identity
        except PlaywrightError:
            continue
    return None


class CaptchaSolver:
    def __init__(self, config, emit):
        self.config, self.emit = config, emit
        self.attempts = {}
        self.last_reason = ""
        self.disabled = set()

    async def install(self, page):
        await page.add_init_script(HOOK)
        for frame in page.frames:
            try:
                await frame.evaluate(HOOK)
            except PlaywrightError:
                pass

    async def solve(self, page):
        challenge = await challenge_frame(page)
        if challenge and "hcaptcha" in challenge[2].lower():
            return await self.visual(page, *challenge[:2])
        for frame in page.frames:
            try:
                info = await frame.evaluate(DETECT)
            except PlaywrightError:
                continue
            if not info or not info.get("key"):
                continue
            signature = (frame.url.split("?")[0], info["kind"], info["key"], info.get("action"))
            if not self.config.capsolver_api_key:
                self.last_reason = "CapSolver is not configured"
                return False
            if (
                "capsolver" in self.disabled
                or self.attempts.get(signature, 0) >= self.config.captcha_max_attempts
            ):
                return False
            # Deliver once per widget/page. A fresh execution has new captured state.
            if self.attempts.get(signature, 0) and not challenge:
                continue
            self.attempts[signature] = self.attempts.get(signature, 0) + 1
            kind = info["kind"]
            typ = (
                "AntiTurnstileTaskProxyLess"
                if kind == "turnstile"
                else "ReCaptcha"
                + ("V3" if info.get("v3") else "V2")
                + ("Enterprise" if info.get("enterprise") else "")
                + "TaskProxyLess"
            )
            task = {"type": typ, "websiteURL": frame.url, "websiteKey": info["key"]}
            if kind == "turnstile":
                task["metadata"] = {
                    k: v for k, v in {"action": info.get("action"), "cdata": info.get("cdata")}.items() if v
                }
            elif info.get("action"):
                task["pageAction"] = info["action"]
            if info.get("s"):
                task["enterprisePayload"] = {"s": info["s"]}
            before = frame.url
            solution, error = await task_result(
                "https://api.capsolver.com",
                self.config.capsolver_api_key,
                task,
                self.config.captcha_timeout,
                self.emit,
            )
            if error:
                self.last_reason = "CapSolver: " + error
                if error in {
                    "ERROR_INVALID_TASK_DATA",
                    "ERROR_KEY_DENIED_ACCESS",
                    "ERROR_ZERO_BALANCE",
                    "ERROR_KEY_DOES_NOT_EXIST",
                }:
                    self.disabled.add("capsolver")
                return False
            token = (solution or {}).get("gRecaptchaResponse") or (solution or {}).get("token")
            if not token or frame.is_detached() or frame.url != before:
                self.last_reason = "CAPTCHA changed while the solver was working"
                return False
            current = await frame.evaluate(DETECT)
            if not current or current.get("key") != info["key"]:
                return False
            applied = await frame.evaluate(DELIVER, {"token": token, "info": info})
            self.last_reason = (
                "Token delivered; waiting for the website"
                if applied
                else "Website has no supported token callback"
            )
            self.emit("captcha", self.last_reason)
            if applied:
                await page.wait_for_timeout(1200)
            return bool(applied)
        if challenge:
            self.last_reason = "This challenge has no supported solver parameters"
        return False

    async def visual(self, page, frame, owner):
        """Click/drag puzzle fallback using the documented CoordinatesTask API."""
        if not self.config.twocaptcha_api_key:
            self.last_reason = "hCaptcha image fallback needs TWOCAPTCHA_API_KEY"
            return False
        signature = ("image", page.url.split("?")[0])
        if "2captcha" in self.disabled or self.attempts.get(signature, 0) >= self.config.captcha_max_attempts:
            return False
        self.attempts[signature] = self.attempts.get(signature, 0) + 1
        try:
            text = await frame.locator("body").inner_text(timeout=2000)
            # An expired idle challenge must refresh before sending an image.
            if not re.search(r"select|click|drag|move|matching", text, re.I):
                refresh = frame.locator('.refresh.button[aria-label="Refresh Challenge."]')
                if await refresh.count() == 1:
                    await refresh.click(timeout=2000)
                    await page.wait_for_timeout(1500)
                    text = await frame.locator("body").inner_text(timeout=2000)
            dragging = bool(re.search(r"drag|move.{0,40}(?:shape|object|outline)", text, re.I))
            if not re.search(r"select|click|drag|move|matching", text, re.I):
                self.last_reason = "Image challenge has no readable instructions"
                return False
            box = await owner.bounding_box()
            if not box or max(box["width"], box["height"]) > 1000:
                return False
            screenshot = await owner.screenshot(type="jpeg", quality=80, scale="css", timeout=3000)
            if len(screenshot) > 600_000:
                self.last_reason = "Challenge image exceeds the solver size limit"
                return False
            prompt = (
                "Return exactly two points in order: first the center of the object to drag, then the center of its matching destination. "
                if dragging
                else "Click the requested objects in the puzzle. Do not click any buttons, Verify, Skip, Next, or menus. "
            )
            task = {
                "type": "CoordinatesTask",
                "body": base64.b64encode(screenshot).decode(),
                "comment": prompt + "Follow the instructions printed in the image.",
            }
            if dragging:
                task.update(minClicks=2, maxClicks=2)
            before = page.url
            solution, error = await task_result(
                "https://api.2captcha.com",
                self.config.twocaptcha_api_key,
                task,
                self.config.captcha_timeout,
                self.emit,
            )
            if error:
                self.last_reason = "2Captcha image fallback: " + error
                if error in {"ERROR_ZERO_BALANCE", "ERROR_KEY_DOES_NOT_EXIST", "ERROR_METHOD_CALL"}:
                    self.disabled.add("2captcha")
                return False
            points = (solution or {}).get("coordinates", [])
            # A fresh screenshot prevents clicking on a replacement/expired puzzle.
            current = await owner.screenshot(type="jpeg", quality=80, scale="css", timeout=3000)
            if page.url != before or hashlib.sha256(current).digest() != hashlib.sha256(screenshot).digest():
                self.last_reason = "Image challenge changed while the solver was working"
                return False
            if not points or len(points) > 20 or (dragging and len(points) != 2):
                return False
            # hCaptcha's last 90px contains navigation/security controls, never answers.
            if any(
                not isinstance(p.get("x"), (int, float))
                or not isinstance(p.get("y"), (int, float))
                or not 0 <= p["x"] < box["width"]
                or not 0 <= p["y"] < box["height"] - 90
                for p in points
            ):
                self.last_reason = "Solver returned coordinates outside the puzzle"
                return False
            if dragging:
                start, end = points
                await page.mouse.move(box["x"] + start["x"], box["y"] + start["y"])
                await page.mouse.down()
                try:
                    await page.mouse.move(box["x"] + end["x"], box["y"] + end["y"], steps=20)
                finally:
                    await page.mouse.up()
            else:
                for p in points:
                    await page.mouse.click(box["x"] + p["x"], box["y"] + p["y"])
                    await page.wait_for_timeout(100)
            button = frame.locator('.button-submit').filter(has_text=re.compile(r"^(Verify|Next)$", re.I))
            if await button.count() == 1 and await button.is_visible():
                await button.click(timeout=3000)
            await page.wait_for_timeout(1500)
            accepted = not await challenge_frame(page)
            self.last_reason = (
                "Website accepted the image challenge"
                if accepted
                else "Image challenge needs another round or was rejected"
            )
            self.emit("captcha", self.last_reason)
            return accepted
        except PlaywrightError:
            self.last_reason = "Image challenge changed during interaction"
            return False


async def solve(config, page, emit, max_seconds=45):
    solver = CaptchaSolver(config, emit)
    applied = await solver.solve(page)
    return {"solved": applied, "reason": solver.last_reason}
