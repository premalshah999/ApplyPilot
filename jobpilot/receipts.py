"""Match authenticated acknowledgement emails to an already attempted application."""

import base64
import re
from datetime import datetime, timedelta, UTC
from email.utils import getaddresses

from bs4 import BeautifulSoup
from sqlalchemy import select

from .db import Job, Mailbox, Run, record


# Senders that deliver ATS acknowledgements; employers' own domains are accepted by identity.
RECEIPT_SENDERS = [
    "greenhouse.io",
    "greenhouse-mail.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "workablemail.com",
    "workable.com",
    "bamboohr.com",
    "jobvite.com",
    "myworkday.com",
    "workday.com",
    "icims.com",
    "oraclecloud.com",
    "oracle.com",
    "taleo.net",
    "successfactors.com",
    "sapsf.com",
    "eightfold.ai",
    "avature.net",
    "phenompeople.com",
    "rippling.com",
    "breezy.hr",
    "recruitee.com",
]


def trusted_sender(domain, job):
    from .mail import sender_allowed, tenant_tokens

    if sender_allowed(
        domain, RECEIPT_SENDERS, tenant_tokens(job.get("url", ""), job.get("company", "")), True
    ):
        return True
    # The employer's own domain, by exact name: "zip.co" for Zip Co ("zip"), "ramp.com" for Ramp.
    # Prefix look-alikes ("ziprecruiter.com", "stripe-careers-notify.com") are not the employer.
    from .mail import employer_label

    name = employer_label(domain)
    words = [w for w in re.split(r"[^a-z0-9]+", (job.get("company") or "").casefold()) if w]
    compact = "".join(words)
    first = words[0] if words else ""
    return len(name) >= 3 and name in {
        compact,
        first,
        compact.removesuffix("co"),
        compact.removesuffix("inc"),
    }


def acknowledgement(message, job, run, allow_missing_title=False):
    headers = {}
    for h in message.get("payload", {}).get("headers", []):
        headers.setdefault(h["name"].lower(), []).append(h["value"])
    sender = getaddresses(headers.get("from", []))
    if len(sender) != 1:
        return None
    domain = sender[0][1].rpartition("@")[2].lower()
    auth = next(
        (
            a
            for a in headers.get("authentication-results", [])
            if re.match(r"\s*mx\.google\.com\s*;", a, re.I)
        ),
        "",
    )
    if not re.search(r"\bdmarc=pass\b", auth, re.I) or not re.search(
        r"header\.from=" + re.escape(domain) + r"(?:\s|;|$)", auth, re.I
    ):
        return None
    trusted = trusted_sender(domain, job)
    recipient = run["packet"]["profile"]["email"].lower()
    if recipient not in {
        a.lower() for _, a in getaddresses(headers.get("to", []) + headers.get("delivered-to", []))
    }:
        return None
    if int(message.get("internalDate", "0")) / 1000 < datetime.fromisoformat(run["created_at"]).timestamp():
        return None
    text = []

    def walk(part):
        if part.get("mimeType") in {"text/plain", "text/html"}:
            data = part.get("body", {}).get("data", "")
            if len(data) < 350000:
                raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                text.append(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True))
        for p in part.get("parts", [])[:30]:
            walk(p)

    walk(message.get("payload", {}))
    subject = headers.get("subject", [""])[0]
    body = " ".join([subject, *text]).casefold()
    company = re.sub(r"[^a-z0-9]", "", job["company"].casefold())
    compact_body = re.sub(r"[^a-z0-9]", "", body)
    first = job["company"].split()[0].casefold() if job["company"].split() else ""
    company_match = company in compact_body or (
        len(first) >= 3 and bool(re.search(r"\bto\s+" + re.escape(first) + r"\s*[!.]?$", subject, re.I))
    )
    if (
        not job["company"]
        or not job["title"]
        or not company_match
        or (job["title"].casefold() not in body and not allow_missing_title)
    ):
        return None
    if re.search(r"security code|verification code|verify your email|resubmit your application", body):
        return None
    if not re.search(
        r"thank (?:you|you!) for (?:applying|your (?:application|interest))|thanks for applying|"
        r"(?:received|reviewing) your application|application.{0,60}(?:received|submitted|landed successfully|is in good hands)",
        body,
    ):
        return None
    return {
        "message_id": message["id"],
        # Authenticated is not enough to confirm a submission: the sender must also be this
        # employer or an ATS that sends on its behalf.
        "sender_trusted": trusted,
        "from": sender[0][1],
        "subject": subject,
        "received_at": datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC).isoformat(),
    }


async def check_receipts(mail, run_id=None):
    cutoff = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with mail.db.session() as s:
        query = (
            select(Run, Job)
            .join(Job, Run.job_id == Job.id)
            .where(
                Run.state.in_(["confirmed", "submission_unknown"]),
                Run.created_at > cutoff,
                Job.demo.is_(False),
            )
            .order_by(Run.created_at.desc())
            .limit(20)
        )
        if run_id:
            query = (
                select(Run, Job).join(Job, Run.job_id == Job.id).where(Run.id == run_id, Job.demo.is_(False))
            )
        candidates = [
            (record(r), record(j))
            for r, j in s.execute(query)
            # Done once a trusted acknowledgement is recorded (legacy email receipts were trusted).
            if not (r.receipt.get("email") and r.receipt["email"].get("sender_trusted", True))
            and r.state in {"confirmed", "submission_unknown"}
        ]
        boxes = {
            b.email.lower(): b.id for b in s.scalars(select(Mailbox).where(Mailbox.state == "connected"))
        }
    found = 0
    for run, job in candidates:
        recipient = run["packet"]["profile"]["email"].lower()
        if recipient not in boxes:
            continue
        since = int(datetime.fromisoformat(run["created_at"]).timestamp())
        listed = await mail.request(
            boxes[recipient],
            "/messages",
            {"q": f"after:{since} to:{recipient}", "maxResults": 50},
        )
        with mail.db.session() as s:
            same_company_jobs = {
                j.id
                for j in s.scalars(
                    select(Job)
                    .join(Run, Run.job_id == Job.id)
                    .where(
                        Job.company == job["company"],
                        Run.state.in_(["confirmed", "submission_unknown"]),
                        Run.created_at > cutoff,
                    )
                )
            }
        for m in listed.get("messages", []):
            msg = await mail.request(boxes[recipient], "/messages/" + m["id"], {"format": "full"})
            if receipt := acknowledgement(msg, job, run, allow_missing_title=len(same_company_jobs) == 1):
                if not receipt["sender_trusted"]:
                    # Kept as context only; keep looking for the employer's own acknowledgement.
                    with mail.db.exclusive() as s:
                        row = s.get(Run, run["id"])
                        if not row.receipt.get("email_untrusted"):
                            row.receipt = {**row.receipt, "email_untrusted": receipt}
                    continue
                with mail.db.exclusive() as s:
                    row = s.get(Run, run["id"])
                    row.receipt = {
                        **row.receipt,
                        "email": receipt,
                        "email_confirmed": True,
                        "website_confirmed": bool(row.receipt.get("website_confirmed")),
                    }
                    if row.state == "submission_unknown":
                        row.state = "confirmed"
                        row.reason = "Submission confirmed by authenticated employer acknowledgement email"
                        s.get(Job, row.job_id).status = "confirmed"
                mail.db.event(run["id"], "email_receipt", "Company acknowledgement email received", receipt)
                found += 1
                break
    return {"emails_confirmed": found}
