"""Answers learned from you (Telegram/dashboard), reused across employers, plus the live ask loop."""

import asyncio
import difflib
import re
import time

from sqlalchemy import select

from .db import Knowledge, Review, now, record
from .widgets import best_option, norm

STOP = {
    "a",
    "an",
    "the",
    "you",
    "your",
    "are",
    "is",
    "do",
    "does",
    "have",
    "has",
    "of",
    "to",
    "in",
    "for",
    "with",
    "and",
    "or",
    "on",
    "at",
    "be",
    "this",
    "that",
    "please",
    "will",
    "would",
    "can",
    "any",
    "if",
    "we",
    "our",
    "us",
    "as",
    "by",
    "it",
    "i",
    "my",
    "me",
    "employer",
    "did",
    "ever",
    "been",
}


def stem(word):
    for suffix in ("ingly", "edly", "ing", "ed", "ly", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text):
    return {stem(w) for w in text.split()} - STOP


CHOICE_TYPES = {"dropdown", "pills", "radio", "radiogroup", "select", "combobox"}


def employer_variants(employer):
    e = (employer or "").strip()
    base = re.sub(
        r",?\s+(inc\.?|llc|ltd\.?|corp(?:oration)?\.?|co\.?|plc|gmbh|company|group|holdings)$",
        "",
        e,
        flags=re.I,
    )
    return [v for v in {e, base} if len(v) >= 3]


def kb_norm(question, employer=""):
    q = f" {question.lower()} "
    for name in sorted(employer_variants(employer), key=len, reverse=True):
        q = q.replace(name.lower(), " employer ")
    q = re.sub(r"\(required\)|\*|\brequired\b", " ", q)
    # Keep comparison symbols: "< 5 years" and "> 5 years" are different questions.
    q = re.sub(r"[^\w\s<>=%$+]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def scope_for(employer):
    return "employer:" + norm(employer) if employer else "global"


def as_checkbox(answer):
    v = answer.strip().lower()
    if v in {"yes", "true", "checked", "agree", "i agree", "accept", "y", "1"}:
        return "true"
    if v in {"no", "false", "unchecked", "n", "0"}:
        return "false"
    return None


class KnowledgeBase:
    def __init__(self, db):
        self.db = db
        self.entries = []
        self.refresh()

    def refresh(self):
        with self.db.session() as s:
            self.entries = [
                record(x)
                for x in s.scalars(select(Knowledge).order_by(Knowledge.updated_at.desc()).limit(5000))
            ]

    def _compatible(self, entry, options, kind):
        answer = entry["answer"]
        if kind == "checkbox":
            return as_checkbox(answer)
        if options:
            return best_option(options, answer)
        if kind in CHOICE_TYPES:
            return None
        return answer

    def lookup(self, question, options, employer="", kind=""):
        key = kb_norm(question, employer)
        if not key:
            return None
        allowed = {"global", scope_for(employer)}
        hits = [e for e in self.entries if e["norm"] == key and e["scope"] in allowed]
        hits.sort(key=lambda e: e["scope"] == "global")
        for entry in hits:
            value = self._compatible(entry, options, kind)
            if value:
                return entry, value
        return None

    def similar(self, question, employer="", k=4):
        key = kb_norm(question, employer)
        mine = tokens(key)
        allowed = {"global", scope_for(employer)}
        scored = []
        for entry in self.entries:
            if entry["scope"] not in allowed:
                continue
            other = tokens(entry["norm"])
            if not mine or not other:
                continue
            overlap = len(mine & other) / len(mine | other)
            if overlap < 0.15:
                continue
            ratio = difflib.SequenceMatcher(None, key, entry["norm"]).ratio()
            scored.append((0.6 * overlap + 0.4 * ratio, entry))
        scored.sort(key=lambda x: -x[0])
        # Context for the model only (never a direct answer), so recall beats precision here.
        return [entry for score, entry in scored[:k] if score >= 0.28]


def upsert(db, question, answer, options=(), employer="", source="dashboard", scope=None):
    key = kb_norm(question, employer)
    if not key:
        raise ValueError("Question is empty after normalization")
    if scope is None:
        # Motivation prose about one employer must not be reused for another.
        specific = (
            employer
            and any(v.lower() in question.lower() for v in employer_variants(employer))
            and not options
            and len(answer) > 60
        )
        scope = scope_for(employer) if specific else "global"
    with db.exclusive() as s:
        row = s.scalar(select(Knowledge).where(Knowledge.norm == key, Knowledge.scope == scope))
        if row:
            row.answer, row.options, row.question, row.source, row.updated_at = (
                answer,
                list(options),
                question,
                source,
                now(),
            )
        else:
            row = Knowledge(
                question=question, norm=key, options=list(options), answer=answer, scope=scope, source=source
            )
            s.add(row)
        s.flush()
        return record(row)


def search(db, text="", limit=10):
    with db.session() as s:
        rows = [
            record(x) for x in s.scalars(select(Knowledge).order_by(Knowledge.updated_at.desc()).limit(5000))
        ]
    if text:
        want = set(norm(text).split()) - STOP
        rows = [r for r in rows if want & set(r["norm"].split()) or norm(text) in r["norm"]]
    return rows[:limit]


def forget(db, entry_id):
    with db.exclusive() as s:
        row = s.get(Knowledge, entry_id)
        if not row:
            raise ValueError("Knowledge entry not found")
        s.delete(row)


def match_reply(text, options):
    """Telegram replies: an option number, an option label, or free text when no options exist."""
    text = text.strip()
    if not options:
        return text
    if re.fullmatch(r"\d{1,3}", text) and 1 <= int(text) <= len(options):
        return options[int(text) - 1]
    choice = best_option(options, text)
    if choice is None:
        raise ValueError("Reply with one of the listed options or its number")
    return choice


async def ask(service, run_id, pending, wait_seconds):
    """Persist questions, ask on Telegram, and wait briefly while the browser holds its place."""
    waiting = []
    with service.db.exclusive() as s:
        existing = {r.key: r for r in s.scalars(select(Review).where(Review.run_id == run_id))}
        for q in pending:
            row = existing.get(q["key"])
            if row is None:
                row = Review(
                    run_id=run_id,
                    question=q["question"][:8000],
                    options=list(q.get("options", []))[:250],
                    key=q["key"],
                    reason=q.get("reason", "")[:500],
                )
                s.add(row)
                s.flush()
                existing[q["key"]] = row
            if row.answer is None:
                waiting.append(row.id)
    if not waiting:
        return 0
    service.db.event(run_id, "asking", f"Asked you {len(waiting)} question(s)", {"reviews": waiting})
    from .telegram import send_reviews

    delivered = await send_reviews(service, waiting)
    if not delivered or wait_seconds <= 0:
        return 0
    deadline = time.monotonic() + wait_seconds
    remaining = waiting
    while time.monotonic() < deadline:
        with service.db.session() as s:
            rows = list(s.scalars(select(Review).where(Review.id.in_(waiting))))
            remaining = [r.id for r in rows if r.answer is None]
        if not remaining or service.db.get_setting("control", {}).get("paused"):
            break
        await asyncio.sleep(1.0)
    answered = len(waiting) - len(remaining)
    if answered:
        service.db.event(run_id, "answered", f"Received {answered} answer(s); continuing")
    return answered
