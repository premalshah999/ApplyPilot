import asyncio

import httpx
from sqlalchemy import select

from .db import Job, Review, Run


async def api(config, method, payload):
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/{method}", json=payload
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram API rejected request")
        return result.get("result")


async def notify_run(service, run_id):
    c = service.config
    if not c.telegram_bot_token or not c.telegram_user_id:
        return
    with service.db.session() as s:
        run = s.get(Run, run_id)
        job = s.get(Job, run.job_id)
        reviews = list(s.scalars(select(Review).where(Review.run_id == run_id, Review.answer.is_(None))))
        text = f"{job.title} · {job.company}\n{run.state}\n{run.reason}\n{run.elapsed:.0f}s · ${run.cost:.4f}"
    try:
        await api(c, "sendMessage", {"chat_id": c.telegram_user_id, "text": text})
        for r in reviews[:5]:
            msg = f"Review {r.id}\n{r.question}\nReply: /answer {r.id} YOUR ANSWER"
            keyboard = [
                [{"text": option[:50], "callback_data": f"a:{r.id}:{i}"}]
                for i, option in enumerate(r.options[:8])
            ]
            payload = {"chat_id": c.telegram_user_id, "text": msg}
            if keyboard:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            await api(c, "sendMessage", payload)
    except Exception:
        service.db.event(run_id, "notification_error", "Telegram delivery failed; details remain in the app")


async def handle_update(service, update):
    c = service.config
    callback = update.get("callback_query")
    message = update.get("message", {})
    sender = (callback or message).get("from", {}).get("id")
    if str(sender) != c.telegram_user_id:
        return None
    if callback:
        parts = callback.get("data", "").split(":")
        try:
            if len(parts) != 3 or parts[0] != "a":
                raise ValueError("Invalid callback")
            with service.db.session() as s:
                review = s.get(Review, parts[1])
                idx = int(parts[2])
                if not review or idx < 0 or idx >= len(review.options):
                    raise ValueError("Expired or invalid review")
                value = review.options[idx]
            service.answer_review(parts[1], value)
            reply = "Answer saved. Requeue when all questions are resolved."
        except (ValueError, IndexError):
            reply = "This review is invalid or already resolved."
        await api(c, "answerCallbackQuery", {"callback_query_id": callback["id"], "text": reply})
        return reply
    text = message.get("text", "").strip()
    cmd, _, rest = text.partition(" ")
    cmd = cmd.split("@")[0]
    if cmd in {"/pause", "/resume"}:
        control = service.db.get_setting("control", {})
        control["paused"] = cmd == "/pause"
        service.db.set_setting("control", control)
        if cmd == "/resume" and service.dispatch:
            from .workers import resume

            await resume(service)
        reply = "Workers paused." if cmd == "/pause" else "Workers resumed."
    elif cmd == "/answer":
        review_id, _, answer = rest.partition(" ")
        try:
            reply = service.answer_review(review_id, answer)["message"]
        except ValueError as exc:
            reply = str(exc)
    elif cmd in {"/status", "/queue", "/report"}:
        with service.db.session() as s:
            rows = list(s.scalars(select(Run).order_by(Run.created_at.desc()).limit(10)))
            reply = (
                "\n".join(f"{r.id[:8]} · {r.state} · {r.elapsed:.0f}s" for r in rows)
                or "No applications yet."
            )
    elif cmd == "/apply":
        from .discovery import add_job, rank
        from .schemas import JobInput

        try:
            job, _ = add_job(service.db, JobInput(url=rest.strip()))
            await rank(service.db, c, job["id"])
            control = service.db.get_setting("control", {})
            run = await service.queue(job["id"], "submit" if control.get("auto_submit") else "dry_run")
            reply = f"Queued {run['id']} ({run['mode']})"
        except Exception as exc:
            reply = f"Could not queue: {type(exc).__name__}. Check job details in the app."
    else:
        reply = "/status · /queue · /pause · /resume · /apply URL · /answer REVIEW_ID ANSWER"
    await api(c, "sendMessage", {"chat_id": c.telegram_user_id, "text": reply})
    return reply


async def poll(service):
    c = service.config
    offset = service.db.get_setting("telegram", {}).get("offset", 0)
    while True:
        try:
            updates = await api(
                c,
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
            )
            for update in updates:
                # Persist before side effects for at-most-once command handling. User can retry manually.
                offset = update["update_id"] + 1
                service.db.set_setting("telegram", {"offset": offset})
                await handle_update(service, update)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)
