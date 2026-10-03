import asyncio

import httpx
from sqlalchemy import select

from .db import Account, Job, Review, Run, TelegramPrompt

HELP = (
    "/status · /queue · /pause · /resume · /apply URL\n"
    "Answer a question: reply to its message, tap an option, or /answer REVIEW_ID ANSWER\n"
    "/learn QUESTION = ANSWER · /kb [search] · /forget ID · /accounts"
)


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


def configured(config):
    return bool(config.telegram_bot_token and config.telegram_user_id)


async def send_reviews(service, review_ids):
    """Send each open question once. Returns True when at least one message was delivered."""
    c = service.config
    if not configured(c):
        return False
    delivered = False
    for review_id in review_ids:
        with service.db.session() as s:
            r = s.get(Review, review_id)
            if not r or r.answer is not None:
                continue
            if s.scalar(select(TelegramPrompt.message_id).where(TelegramPrompt.review_id == r.id)):
                delivered = True
                continue
            run = s.get(Run, r.run_id)
            job = s.get(Job, run.job_id) if run else None
            where = f"{job.title} · {job.company}\n" if job else ""
            options = list(r.options)
            question, reason = r.question, r.reason
        lines = [f"❓ {where}{question}"]
        if options:
            lines += [f"{i + 1}. {o}" for i, o in enumerate(options[:30])]
            if len(options) > 30:
                lines.append(f"… {len(options) - 30} more; reply with the exact text")
        if reason:
            lines.append(f"({reason[:160]})")
        lines.append("Reply to this message with your answer. I'll remember it for future applications.")
        payload = {"chat_id": c.telegram_user_id, "text": "\n".join(lines)[:4000]}
        if 0 < len(options) <= 8:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": o[:50], "callback_data": f"a:{review_id}:{i}"}] for i, o in enumerate(options)
                ]
            }
        try:
            message = await api(c, "sendMessage", payload)
        except Exception:
            service.db.event(
                r.run_id, "notification_error", "Telegram delivery failed; details remain in the app"
            )
            continue
        with service.db.session() as s:
            s.add(TelegramPrompt(message_id=str(message["message_id"]), review_id=review_id))
        delivered = True
    return delivered


async def notify_run(service, run_id):
    c = service.config
    if not configured(c):
        return
    with service.db.session() as s:
        run = s.get(Run, run_id)
        job = s.get(Job, run.job_id)
        reviews = [
            r.id for r in s.scalars(select(Review).where(Review.run_id == run_id, Review.answer.is_(None)))
        ]
        icon = {"confirmed": "✅", "dry_run_passed": "🧪", "needs_review": "⏸", "failed": "❌"}.get(
            run.state, "•"
        )
        text = (
            f"{icon} {job.title} · {job.company}\n{run.state}\n{run.reason}\n"
            f"{run.elapsed:.0f}s · ${run.cost:.4f} · {run.model_calls} model calls"
        )
    try:
        await api(c, "sendMessage", {"chat_id": c.telegram_user_id, "text": text[:4000]})
    except Exception:
        service.db.event(run_id, "notification_error", "Telegram delivery failed; details remain in the app")
        return
    await send_reviews(service, reviews[:10])


async def _answer(service, review_id, text):
    from .knowledge import match_reply

    with service.db.session() as s:
        review = s.get(Review, review_id)
        if not review:
            raise ValueError("Review not found")
        options = list(review.options)
    value = match_reply(text, options)
    result = service.answer_review(review_id, value)
    await service.after_answer(review_id)
    return result["message"], value


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
            await service.after_answer(parts[1])
            reply = f"Saved “{value}”. I'll use it from now on."
        except (ValueError, IndexError):
            reply = "This review is invalid or already resolved."
        await api(c, "answerCallbackQuery", {"callback_query_id": callback["id"], "text": reply[:190]})
        return reply
    text = message.get("text", "").strip()
    replied = message.get("reply_to_message", {}).get("message_id")
    if replied and not text.startswith("/"):
        with service.db.session() as s:
            prompt = s.get(TelegramPrompt, str(replied))
            review_id = prompt.review_id if prompt else None
        if review_id:
            try:
                note, value = await _answer(service, review_id, text)
                reply = f"Saved “{value[:200]}”. {note}"
            except ValueError as exc:
                reply = str(exc)
            await api(c, "sendMessage", {"chat_id": c.telegram_user_id, "text": reply[:4000]})
            return reply
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
            reply = (await _answer(service, review_id, answer))[0]
        except ValueError as exc:
            reply = str(exc)
    elif cmd == "/learn":
        from .knowledge import upsert

        question, sep, answer = rest.partition("=")
        if not sep or not question.strip() or not answer.strip():
            reply = "Use /learn QUESTION = ANSWER"
        else:
            entry = upsert(service.db, question.strip(), answer.strip(), source="telegram")
            reply = f"Learned ({entry['id'][:8]}): {entry['question'][:120]} → {entry['answer'][:120]}"
    elif cmd == "/kb":
        from .knowledge import search

        rows = search(service.db, rest.strip(), limit=12)
        reply = "\n".join(f"{r['id'][:8]} · {r['question'][:70]} → {r['answer'][:50]}" for r in rows) or (
            "No knowledge entries yet."
        )
    elif cmd == "/forget":
        from .knowledge import forget, search

        prefix = rest.strip()
        matches = [r for r in search(service.db, "", limit=5000) if prefix and r["id"].startswith(prefix)]
        if len(matches) != 1:
            reply = "Give the entry ID prefix shown by /kb"
        else:
            forget(service.db, matches[0]["id"])
            reply = "Forgotten."
    elif cmd == "/accounts":
        with service.db.session() as s:
            rows = list(s.scalars(select(Account).order_by(Account.updated_at.desc()).limit(20)))
            reply = "\n".join(f"{a.realm} · {a.state}" for a in rows) or "No employer accounts yet."
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
        reply = HELP
    await api(c, "sendMessage", {"chat_id": c.telegram_user_id, "text": reply[:4000]})
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
                try:
                    await handle_update(service, update)
                except Exception:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)
