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
    action:el.dataset.action,cdata:el.dataset.cdata,s:el.dataset.s,invisible:el.dataset.size==='invisible'};
 }
 for(const el of document.querySelectorAll('iframe[src]')) {
  try {const u=new URL(el.src);
   if(/(?:google\.com|recaptcha\.net)$/.test(u.hostname) && /recaptcha\/(?:api2|enterprise)\/anchor/.test(u.pathname))
    return {kind:'recaptcha',key:u.searchParams.get('k'),enterprise:u.pathname.includes('enterprise'),
      invisible:u.searchParams.get('size')==='invisible'};
   const t=u.hostname==='challenges.cloudflare.com' && u.pathname.match(/\/(0x[0-9A-Za-z_-]{10,})\//);
   if(t && document.querySelector('[name="cf-turnstile-response"]')) return {kind:'turnstile',key:t[1]};
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


async def checkbox_frame(page):
    """A visible hCaptcha "I am human" checkbox whose response is still empty."""
    for frame in page.frames:
        if (
            frame == page.main_frame
            or "hcaptcha" not in (frame.url or "")
            or "frame=checkbox" not in frame.url
        ):
            continue
        try:
            owner = await frame.frame_element()
            if not await owner.is_visible():
                continue
            parent = await owner.evaluate_handle("e=>e.ownerDocument")
            empty = await parent.evaluate(
                "d=>[...d.querySelectorAll('[name=\"h-captcha-response\"]')].every(e=>!e.value)"
            )
            if empty:
                return frame
        except PlaywrightError:
            continue
    return None


class CaptchaSolver:
    def __init__(self, config, emit):
        self.config, self.emit = config, emit
        self.attempts = {}
        self.rounds = 0  # Paid image rounds this run.
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
        if not challenge and (box := await checkbox_frame(page)):
            # A visible checkbox gates the step: open it once per page, then solve what it shows.
            signature = ("checkbox", page.url.split("?")[0])
            if self.attempts.get(signature, 0) < self.config.captcha_max_attempts:
                self.attempts[signature] = self.attempts.get(signature, 0) + 1
                try:
                    await box.locator("#checkbox").click(timeout=3000)
                except PlaywrightError:
                    pass
                for _ in range(12):
                    await page.wait_for_timeout(250)
                    if challenge := await challenge_frame(page):
                        break
                if not challenge and await self.token_present(page):
                    self.last_reason = "Security checkbox accepted"
                    self.emit("captcha", self.last_reason)
                    return True
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
            if kind == "recaptcha" and info.get("invisible") and not info.get("v3"):
                task["isInvisible"] = True
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
        return await self.text_image(page)

    async def visual(self, page, frame, owner):
        """Image puzzle fallback using 2Captcha's documented CoordinatesTask.

        Contract: only the puzzle area is sent (never the application, the prompt header, or the
        control bar), the printed instructions travel separately as text (plus the example image
        when small enough), returned points are mapped from image pixels to page coordinates, a
        solution for a puzzle that changed meanwhile is discarded, out-of-bounds points are
        rejected, every round is re-observed, and success means the website closed the challenge.
        Bounded by captcha_max_attempts challenges per page and captcha_max_rounds paid rounds per
        run."""
        if not self.config.twocaptcha_api_key:
            self.last_reason = "hCaptcha image fallback needs TWOCAPTCHA_API_KEY"
            return False
        signature = ("image", page.url.split("?")[0])
        if "2captcha" in self.disabled or self.attempts.get(signature, 0) >= self.config.captcha_max_attempts:
            return False
        self.attempts[signature] = self.attempts.get(signature, 0) + 1
        answered = set()
        try:
            while self.rounds < self.config.captcha_max_rounds:
                puzzle = await self.observe_puzzle(frame, owner)
                if not puzzle:
                    return False
                if puzzle["hash"] in answered:
                    # The website kept the same puzzle after an answer: it was not accepted.
                    self.last_reason = "Image challenge rejected the answer"
                    return False
                self.rounds += 1
                points = await self.ask_coordinates(puzzle)
                if points is None:
                    return False
                current = await self.capture(page, owner, puzzle["target"])
                if not current or hashlib.sha256(current[0]).hexdigest() != puzzle["hash"]:
                    # A fresh capture prevents clicking on a replacement/expired puzzle.
                    self.last_reason = "Image challenge changed while the solver was working"
                    return False
                puzzle["region"] = current[1]  # Measured now: the page may have scrolled.
                answered.add(puzzle["hash"])
                await self.act(page, puzzle, points)
                button = frame.locator(".button-submit").filter(
                    has_text=re.compile(r"^(Verify|Next|Submit)$", re.I)
                )
                if await button.count() == 1 and await button.is_visible():
                    await button.click(timeout=3000)
                await page.wait_for_timeout(1500)
                if not await challenge_frame(page):
                    accepted = await self.token_present(page)
                    self.last_reason = (
                        "Website accepted the image challenge"
                        if accepted
                        else "Image challenge closed without a response token"
                    )
                    self.emit("captcha", self.last_reason, {"rounds": self.rounds})
                    return accepted
                self.emit("captcha", "Image challenge needs another round", {"rounds": self.rounds})
            self.last_reason = "Image challenge needs more rounds than CAPTCHA_MAX_ROUNDS allows"
            return False
        except PlaywrightError:
            self.last_reason = "Image challenge changed during interaction"
            return False

    async def observe_puzzle(self, frame, owner):
        text = await frame.locator("body").inner_text(timeout=2000)
        if not CUE.search(text):
            # An expired idle challenge must refresh before sending an image.
            refresh = frame.locator('.refresh.button[aria-label="Refresh Challenge."]')
            if await refresh.count() == 1:
                await refresh.click(timeout=2000)
                await frame.page.wait_for_timeout(1500)
                text = await frame.locator("body").inner_text(timeout=2000)
        if not CUE.search(text):
            self.last_reason = "Image challenge has no readable instructions"
            return None
        layout = await frame.evaluate(PUZZLE)
        box = await owner.bounding_box()
        if not box or max(box["width"], box["height"]) > 1000:
            self.last_reason = "Image challenge is larger than supported"
            return None
        target = layout["target"]
        captured = await self.capture(frame.page, owner, target)
        if not captured:
            self.last_reason = "Image challenge could not be captured"
            return None
        image, region = captured
        if len(image) > 600_000:
            self.last_reason = "Challenge image exceeds the solver size limit"
            return None
        prompt = re.sub(r"\s+", " ", layout["prompt"] or "").strip()
        dragging = bool(re.search(r"drag|move.{0,40}(?:shape|object|outline|piece)", prompt or text, re.I))
        example = None
        if layout["example"]:
            try:
                shot = await frame.locator("[data-jp-example]").screenshot(
                    type="jpeg", quality=80, scale="css", timeout=2000
                )
                if len(shot) <= 100_000:
                    example = shot
            except PlaywrightError:
                example = None
        return {
            "image": image,
            "hash": hashlib.sha256(image).hexdigest(),
            "region": region,
            "size": jpeg_size(image) or (region["width"], region["height"]),
            "prompt": prompt,
            "dragging": dragging,
            "example": example,
            "target": target,
        }

    async def capture(self, page, owner, target):
        """JPEG of the puzzle area and its viewport region (CSS pixels).

        The image may be larger than the region (attached desktop Chrome on a Retina display
        ignores scale="css"); coordinates are mapped by the measured image size. The puzzle is
        scrolled into view first, and its box is read after the screenshot, so clicks never use
        a box measured before the page scrolled."""
        try:
            await owner.scroll_into_view_if_needed(timeout=2000)
            if target == "element":
                frame = await owner.content_frame()
                element = frame.locator("[data-jp-puzzle]").first
                image = await element.screenshot(type="jpeg", quality=80, scale="css", timeout=3000)
                region = await element.bounding_box()
                if not region:
                    return None
            else:
                box = await owner.bounding_box()
                if not box:
                    return None
                left, top = await owner.evaluate("e=>[e.clientLeft,e.clientTop]")
                inner_w, inner_h = await owner.evaluate("e=>[e.clientWidth,e.clientHeight]")
                # hCaptcha's last 90px hold refresh/skip/verify and accessibility controls, never answers.
                region = {
                    "x": box["x"] + left,
                    "y": box["y"] + top,
                    "width": inner_w,
                    "height": max(0, inner_h - 90),
                }
                if region["width"] < 50 or region["height"] < 50:
                    return None
                image = await page.screenshot(type="jpeg", quality=80, scale="css", clip=region, timeout=3000)
        except PlaywrightError:
            return None
        return image, region

    async def ask_coordinates(self, puzzle):
        dragging = puzzle["dragging"]
        lead = (
            "Drag task: point 1 is the piece to move, point 2 its destination. "
            if dragging
            else "Click every matching object. "
        )
        task = {
            "type": "CoordinatesTask",
            "body": base64.b64encode(puzzle["image"]).decode(),
            "comment": (lead + (puzzle["prompt"] or "Follow the instructions in the image."))[:140],
        }
        if puzzle["example"]:
            task["imgInstructions"] = base64.b64encode(puzzle["example"]).decode()
        if dragging:
            task.update(minClicks=2, maxClicks=2)
        else:
            task.update(minClicks=1, maxClicks=12)
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
            return None
        points = (solution or {}).get("coordinates", [])
        width, height = puzzle["size"]
        if not points or len(points) > 20 or (dragging and len(points) != 2):
            self.last_reason = "Solver returned no usable coordinates"
            return None
        if any(
            not isinstance(p, dict)
            or not isinstance(p.get("x"), (int, float))
            or not isinstance(p.get("y"), (int, float))
            or not 0 <= p["x"] < width
            or not 0 <= p["y"] < height
            for p in points
        ):
            self.last_reason = "Solver returned coordinates outside the puzzle"
            return None
        return points

    async def act(self, page, puzzle, points):
        region, (width, height) = puzzle["region"], puzzle["size"]
        sx, sy = region["width"] / width, region["height"] / height
        mapped = [(region["x"] + p["x"] * sx, region["y"] + p["y"] * sy) for p in points]
        if puzzle["dragging"]:
            (x1, y1), (x2, y2) = mapped
            await page.mouse.move(x1, y1)
            await page.mouse.down()
            try:
                await page.mouse.move(x2, y2, steps=20)
            finally:
                await page.mouse.up()
        else:
            for x, y in mapped:
                await page.mouse.click(x, y)
                await page.wait_for_timeout(120)

    async def token_present(self, page):
        """A closed challenge counts only if the widget's response field (when present) is filled."""
        found_field = False
        for frame in page.frames:
            try:
                values = await frame.evaluate(
                    '()=>[...document.querySelectorAll(\'[name="h-captcha-response"],[name="g-recaptcha-response"]\')].map(e=>e.value)'
                )
            except PlaywrightError:
                continue
            if values:
                found_field = True
                if any(values):
                    return True
        return not found_field

    async def text_image(self, page):
        """Classic distorted-text CAPTCHA next to an input: CapSolver ImageToTextTask."""
        if not self.config.capsolver_api_key or "capsolver" in self.disabled:
            return False
        for frame in page.frames:
            try:
                found = await frame.evaluate(TEXT_IMAGE)
            except PlaywrightError:
                continue
            if not found:
                continue
            signature = ("text", frame.url.split("?")[0])
            if self.attempts.get(signature, 0) >= self.config.captcha_max_attempts:
                return False
            self.attempts[signature] = self.attempts.get(signature, 0) + 1
            image = await frame.locator("[data-jp-captcha-img]").first.screenshot(type="png", timeout=3000)
            solution, error = await task_result(
                "https://api.capsolver.com",
                self.config.capsolver_api_key,
                {"type": "ImageToTextTask", "body": base64.b64encode(image).decode()},
                self.config.captcha_timeout,
                self.emit,
            )
            text = (solution or {}).get("text", "")
            if error or not re.fullmatch(r"[A-Za-z0-9]{3,12}", text or ""):
                self.last_reason = "CapSolver: " + (error or "UNREADABLE_IMAGE")
                return False
            await frame.locator("[data-jp-captcha-input]").first.fill(text, timeout=3000)
            self.last_reason = "Image text entered; waiting for the website"
            self.emit("captcha", self.last_reason)
            return True
        return False


def jpeg_size(data):
    """(width, height) from a JPEG's SOF marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if marker in {0xC0, 0xC1, 0xC2}:
            return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
        i += 2 + length
    return None


CUE = re.compile(r"select|click|tap|drag|move|matching|pick|choose|identify", re.I)

# The task area (canvas or tile grid) inside the challenge frame, the printed prompt, and the
# example image. Marked nodes are captured by element screenshots; nothing else is sent.
PUZZLE = r"""() => {
 const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect();const s=getComputedStyle(e);
  return r.width>=100&&r.height>=100&&s.visibility!=='hidden'&&s.display!=='none';};
 const area=e=>{const r=e.getBoundingClientRect();return r.width*r.height;};
 document.querySelectorAll('[data-jp-puzzle],[data-jp-example]').forEach(e=>{delete e.dataset.jpPuzzle;delete e.dataset.jpExample;});
 let el=null;
 for(const sel of ['.challenge-view canvas','.task-grid','.challenge-view','.challenge-container canvas','canvas']){
  const hits=[...document.querySelectorAll(sel)].filter(vis).filter(e=>!e.querySelector('.button-submit'));
  if(hits.length){el=hits.sort((a,b)=>area(b)-area(a))[0];break;}
 }
 const prompt=document.querySelector('.prompt-text,.challenge-prompt,[class*=prompt-text]');
 const example=document.querySelector('.challenge-example,[class*=challenge-example]');
 let ex=false;
 if(example){const r=example.getBoundingClientRect();
  if(r.width>=20&&r.height>=20&&r.width<=400&&r.height<=150){example.dataset.jpExample='1';ex=true;}}
 if(el) el.dataset.jpPuzzle='1';
 return {target: el?'element':'frame', prompt: prompt?prompt.innerText:'', example: ex};
}"""

TEXT_IMAGE = r"""() => {
 for(const img of document.querySelectorAll('img[src*="captcha" i],img[id*="captcha" i],img[alt*="captcha" i]')){
  if(!img.getClientRects().length) continue;
  const box=img.closest('form,div,td')||document.body;
  const input=box.querySelector('input[name*="captcha" i],input[id*="captcha" i]');
  if(!input||!input.getClientRects().length) continue;
  img.dataset.jpCaptchaImg='1'; input.dataset.jpCaptchaInput='1';
  return true;
 }
 return false;
}"""


async def solve(config, page, emit, max_seconds=45):
    solver = CaptchaSolver(config, emit)
    applied = await solver.solve(page)
    return {"solved": applied, "reason": solver.last_reason}
