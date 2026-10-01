"""Optional CapSolver client. Unsupported challenges stay in review."""

import asyncio
import time

import httpx


async def solve(config, page, emit, max_seconds=45):
    if not config.capsolver_api_key:
        return {"solved": False, "reason": "CapSolver is not configured"}
    info = await page.evaluate("""() => {
      for(const [selector,type] of [['.g-recaptcha','ReCaptchaV2TaskProxyLess'],
                                   ['.cf-turnstile','AntiTurnstileTaskProxyLess']]) {
        const el=document.querySelector(selector);
        if(el?.dataset.sitekey) return {type,websiteKey:el.dataset.sitekey, callback:el.dataset.callback};
      }
      return null;
    }""")
    if not info:
        return {"solved": False, "reason": "No supported visible reCAPTCHA v2 or Turnstile widget detected"}
    started = time.monotonic()
    async with httpx.AsyncClient(base_url="https://api.capsolver.com", timeout=15) as client:
        task = {"type": info["type"], "websiteURL": page.url, "websiteKey": info["websiteKey"]}
        r = await client.post("/createTask", json={"clientKey": config.capsolver_api_key, "task": task})
        r.raise_for_status()
        result = r.json()
        if result.get("errorId"):
            return {"solved": False, "reason": result.get("errorCode", "CapSolver rejected task")}
        emit("captcha", "Challenge sent to configured solver", {"type": info["type"]})
        while time.monotonic() - started < max_seconds:
            if result.get("status") == "ready":
                token = result.get("solution", {}).get("gRecaptchaResponse") or result.get(
                    "solution", {}
                ).get("token")
                if not token:
                    break
                applied = await page.evaluate(
                    """({token,callback}) => {
                    const inputs=document.querySelectorAll('[name="g-recaptcha-response"],[name="cf-turnstile-response"]');
                    for(const el of inputs){ el.value=token; el.dispatchEvent(new Event('input',{bubbles:true}));
                      el.dispatchEvent(new Event('change',{bubbles:true})); }
                    if(callback && typeof window[callback]==='function') window[callback](token);
                    return inputs.length>0;
                }""",
                    {"token": token, "callback": info.get("callback")},
                )
                return {
                    "solved": bool(applied),
                    "reason": "Token delivered; website acceptance must be verified",
                }
            await asyncio.sleep(2)
            r = await client.post(
                "/getTaskResult", json={"clientKey": config.capsolver_api_key, "taskId": result.get("taskId")}
            )
            r.raise_for_status()
            new = r.json()
            new.setdefault("taskId", result.get("taskId"))
            result = new
            if result.get("errorId"):
                return {"solved": False, "reason": result.get("errorCode", "Solver failed")}
    return {"solved": False, "reason": "Challenge exceeded solver budget"}
