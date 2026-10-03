"""Verification codes/links from your own mailbox. Gmail app password over IMAP (preferred) or the
connected Gmail OAuth mailbox. Message text is parsed locally and never sent to a model."""

import asyncio
import email
import email.policy
import imaplib
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from sqlalchemy.exc import IntegrityError

from .db import MailConsumption

FAMILY_SENDERS = {
    "workday": ["myworkday.com", "workday.com", "myworkdayjobs.com"],
    "oracle": ["oracle.com", "oraclecloud.com", "oracleemaildelivery.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net", "oracle.com"],
    "successfactors": ["successfactors.com", "successfactors.eu", "sap.com", "sapsf.com"],
    "eightfold": ["eightfold.ai", "eightfold.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "greenhouse": ["greenhouse.io", "greenhouse-mail.io"],
    "lever": ["lever.co"],
    "ashby": ["ashbyhq.com"],
    "jobvite": ["jobvite.com"],
    "avature": ["avature.net"],
    "phenom": ["phenompeople.com"],
}
# Shared (multi-tenant) hosts an ATS may link to. A link there must still name this employer.
FAMILY_LINK_HOSTS = {
    "workday": ["myworkdayjobs.com", "myworkdaysite.com", "myworkday.com", "workday.com"],
    "oracle": ["oraclecloud.com", "oracle.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net"],
    "successfactors": ["successfactors.com", "successfactors.eu", "sapsf.com", "jobs2web.com", "sap.com"],
    "eightfold": ["eightfold.ai"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
}


def link_belongs(url, host, tokens):
    """The employer's own host, or a shared ATS host whose URL names this employer/tenant."""
    p = urlsplit(url)
    if (p.hostname or "").lower() == host.lower():
        return True
    blob = (p.hostname or "").lower() + (p.path + "?" + p.query).lower()
    return any(t in blob for t in tokens)


CODE_PATTERNS = [
    re.compile(
        r"(?i:\b(?:verification code|security code|authentication code|one.time (?:code|passcode|password|pin)|"
        r"confirmation code|login code|sign.in code|access code|passcode|otp|code|pin)\b\s*(?:(?:is|below|:)\s*)*"
        r"[:#-]?\s*)\b([A-Z0-9]{4,10})\b"
    ),
    re.compile(
        r"\b([0-9]{4,10})\b(?i:\s+is your (?:\w+ )?(?:verification|security|authentication|one.time|login|sign.in|"
        r"confirmation|access) (?:code|passcode|password|pin))"
    ),
]
CODE_CUE = re.compile(
    r"verification|verify|one.time|passcode|\bpin\b|\bcode\b|confirm your identity|otp", re.I
)
NOT_CODES = {"PLEASE", "BELOW", "EXPIRES", "VALID", "YOUR", "REQUEST", "CODE", "ENTER", "THIS", "WITHIN"}
LINK_CUE = re.compile(
    r"verif|confirm|activat|validat|sign.?in|log.?in|reset|set (?:a |your )?password|continue", re.I
)
LINK_URL_CUE = re.compile(r"verif|confirm|activat|validat|token|reset|password|ticket|key=", re.I)


@dataclass
class Message:
    key: str
    received: float
    sender: str
    recipients: list[str]
    subject: str
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)
    authenticated: bool = False


def same_mailbox(a, b):
    """Gmail delivers user+tag@ and u.s.e.r@ to the same mailbox."""

    def base(addr):
        local, _, domain = addr.lower().strip().partition("@")
        local = local.split("+")[0]
        if domain in {"gmail.com", "googlemail.com"}:
            local, domain = local.replace(".", ""), "gmail.com"
        return local + "@" + domain

    return base(a) == base(b)


def host_matches(host, domains):
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def _bodies(msg):
    texts, links = [], []
    parts = msg.walk() if msg.is_multipart() else [msg]
    size = 0
    for part in parts:
        kind = part.get_content_type()
        if kind not in {"text/plain", "text/html"} or part.get_filename():
            continue
        try:
            raw = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            raw = payload.decode("utf-8", "replace")
        size += len(raw)
        if size > 400_000:
            break
        if kind == "text/html":
            soup = BeautifulSoup(raw, "html.parser")
            for a in soup.select("a[href]"):
                links.append((a["href"].strip(), a.get_text(" ", strip=True)))
            texts.append(soup.get_text(" ", strip=True))
        else:
            texts.append(raw)
            for m in re.finditer(r"https://[^\s<>\"')]+", raw):
                context = raw[max(0, m.start() - 120) : m.start()]
                links.append((m.group().rstrip(".,;"), context))
    return "\n".join(texts), links


def parse_raw(raw: bytes, key: str, received: float | None = None) -> Message:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    senders = getaddresses(msg.get_all("from", []))
    sender = senders[0][1].lower() if len(senders) == 1 else ""
    recipients = [a.lower() for _, a in getaddresses(msg.get_all("to", []) + msg.get_all("delivered-to", []))]
    if received is None:
        try:
            received = parsedate_to_datetime(msg.get("date")).timestamp()
        except (TypeError, ValueError):
            received = time.time()
    domain = sender.rpartition("@")[2]
    results = " ".join(str(v) for v in msg.get_all("authentication-results", []))
    authenticated = bool(
        domain
        and (
            re.search(r"\bdmarc=pass\b", results, re.I)
            or re.search(
                r"\bdkim=pass\b[^;]*header\.(?:d|i)=@?(?:[\w.-]*\.)?" + re.escape(domain), results, re.I
            )
        )
    )
    text, links = _bodies(msg)
    return Message(
        key=key,
        received=received,
        sender=sender,
        recipients=recipients,
        subject=str(msg.get("subject", "")),
        text=text,
        links=links,
        authenticated=authenticated,
    )


def extract(message: Message, kind: str, link_hosts: list[str]):
    """One unambiguous code or verification link, or None."""
    text = message.subject + "\n" + message.text
    codes = set()
    for pattern in CODE_PATTERNS:
        for c in pattern.findall(text):
            if c.upper() in NOT_CODES:
                continue
            # Words like "ACCOUNT" are not codes; real codes carry digits or are 6+ mixed tokens.
            if not re.search(r"\d", c) and (len(c) < 6 or c.isalpha()):
                continue
            codes.add(c)
    if not codes and CODE_CUE.search(text):
        # "...continue your application: 482915" - one standalone number in a code email.
        numbers = {
            n
            for n in re.findall(r"(?<![\d$.,:/-])\b(\d{4,8})\b(?![\d.,/-]\d)", text)
            if not 1900 <= int(n) <= 2100
        }
        if len(numbers) == 1:
            codes = numbers
    links = set()
    for href, context in message.links:
        try:
            p = urlsplit(href)
        except ValueError:
            continue
        if p.scheme != "https" or not host_matches(p.hostname, link_hosts):
            continue
        if LINK_CUE.search(context) or LINK_URL_CUE.search(p.path + "?" + p.query):
            links.add(href)
    if kind in {"code", "auto"} and len(codes) == 1:
        return {"kind": "code", "value": next(iter(codes))}
    if kind in {"link", "auto"} and len(links) == 1 and (kind == "link" or not codes):
        return {"kind": "link", "value": next(iter(links))}
    if kind == "link" and len(links) > 1:
        # Some emails repeat the same verification URL with different tracking parameters.
        paths = {urlsplit(x).path for x in links}
        if len(paths) == 1:
            return {"kind": "link", "value": sorted(links)[0]}
    return None


class IMAPBackend:
    def __init__(self, config):
        self.config = config
        self.conn = None
        self.cache = {}
        self.folder = None

    def _connect(self):
        if self.conn is not None:
            try:
                self.conn.noop()
                return self.conn
            except Exception:
                self.conn = None
        context = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(
            self.config.imap_host, self.config.imap_port, ssl_context=context, timeout=20
        )
        conn.login(self.config.gmail_address, self.config.gmail_app_password.replace(" ", ""))
        for folder in ('"[Gmail]/All Mail"', "INBOX"):
            status, _ = conn.select(folder, readonly=True)
            if status == "OK":
                self.folder = folder
                break
        self.conn = conn
        return conn

    def check(self):
        self._connect()
        return {"connected": True, "folder": self.folder.strip('"'), "host": self.config.imap_host}

    def fetch(self, since: float):
        conn = self._connect()
        day = (datetime.fromtimestamp(since, UTC) - timedelta(days=1)).strftime("%d-%b-%Y")
        gmail = "gmail" in self.config.imap_host
        minutes = max(5, int((time.time() - since) / 60) + 5)
        if gmail:
            status, data = conn.uid("SEARCH", "X-GM-RAW", f'"newer_than:{min(minutes // 60 + 1, 48)}h"')
        else:
            status, data = conn.uid("SEARCH", None, "SINCE", day)
        if status != "OK":
            raise RuntimeError("Mailbox search failed")
        uids = data[0].split()[-60:]
        out = []
        for uid in uids:
            if uid in self.cache:
                out.append(self.cache[uid])
                continue
            status, parts = conn.uid("FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            meta, raw = parts[0]
            internal = imaplib.Internaldate2tuple(meta)
            received = time.mktime(internal) if internal else None
            message = parse_raw(raw, "imap:" + uid.decode(), received)
            self.cache[uid] = message
            out.append(message)
        if len(self.cache) > 500:
            for key in list(self.cache)[:-200]:
                self.cache.pop(key, None)
        return [m for m in out if m.received >= since]


class GmailAPIBackend:
    """Adapter over the existing OAuth mailbox so either connection works."""

    def __init__(self, service, mailbox_id):
        self.service, self.mailbox_id = service, mailbox_id
        self.cache = {}

    async def fetch(self, since: float):
        import base64

        mail = self.service.mail
        hours = max(1, int((time.time() - since) / 3600) + 1)
        listed = await mail.request(
            self.mailbox_id, "/messages", {"q": f"newer_than:{hours}h", "maxResults": 30}
        )
        out = []
        for m in listed.get("messages", [])[:30]:
            if m["id"] in self.cache:
                out.append(self.cache[m["id"]])
                continue
            raw = await mail.request(self.mailbox_id, "/messages/" + m["id"], {"format": "raw"})
            data = base64.urlsafe_b64decode(raw["raw"] + "=" * (-len(raw["raw"]) % 4))
            message = parse_raw(data, "gmail:" + m["id"], int(raw.get("internalDate", "0")) / 1000 or None)
            self.cache[m["id"]] = message
            out.append(message)
        return [m for m in out if m.received >= since]


class Inbox:
    def __init__(self, service):
        self.service = service
        self.config = service.config
        self.imap = (
            IMAPBackend(self.config) if self.config.gmail_address and self.config.gmail_app_password else None
        )
        self.io_lock = asyncio.Lock()
        self.family_locks = {}
        self.fake = None  # Owned test fixtures inject a list of Message objects here.

    @property
    def configured(self):
        if self.fake is not None or self.imap:
            return True
        return bool(self._oauth_mailbox())

    def _oauth_mailbox(self):
        from sqlalchemy import select

        from .db import Mailbox

        with self.service.db.session() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.state == "connected"))
            return box.id if box else None

    def lock(self, family):
        """Serialize challenge windows per sender family so one code cannot satisfy two runs."""
        return self.family_locks.setdefault(family or "custom", asyncio.Lock())

    async def _fetch(self, since):
        if self.fake is not None:
            return [m for m in self.fake if m.received >= since]
        async with self.io_lock:
            if self.imap:
                return await asyncio.to_thread(self.imap.fetch, since)
            box = self._oauth_mailbox()
            if not box:
                raise ValueError("Connect Gmail (app password or OAuth) for email verification")
            return await GmailAPIBackend(self.service, box).fetch(since)

    def _consume(self, key, run_id):
        try:
            with self.service.db.session() as s:
                if s.get(MailConsumption, key):
                    return False
                s.add(MailConsumption(key=key, challenge_id=run_id))
            return True
        except IntegrityError:
            return False

    def _consumed(self, key):
        with self.service.db.session() as s:
            return s.get(MailConsumption, key) is not None

    async def wait(
        self, *, run_id, since, ats, employer, host, kind="auto", timeout=90, recipient="", exclusive=False
    ):
        """Poll for one matching message newer than `since`; returns {kind, value} or None."""
        if not self.configured:
            raise ValueError("Connect Gmail (GMAIL_ADDRESS + GMAIL_APP_PASSWORD) for email verification")
        senders = FAMILY_SENDERS.get(ats, [])
        hosts = sorted(set(FAMILY_LINK_HOSTS.get(ats, [])) | {host.lower()} | set(senders))
        employer_tokens = [t for t in re.split(r"[^a-z0-9]+", (employer or "").lower()) if len(t) >= 4][:3]
        tenant = (host or "").split(".")[0].lower()
        if len(tenant) >= 4 and tenant not in {"www", "careers", "jobs", "apply"}:
            employer_tokens.append(tenant)
        deadline = time.monotonic() + timeout
        delay = 1.5
        while time.monotonic() < deadline:
            try:
                messages = await self._fetch(since - 20)
            except ValueError:
                raise
            except Exception:
                messages = []
                self.service.db.event(run_id, "mail_error", "Mailbox check failed; retrying")
            candidates = []
            for m in sorted(messages, key=lambda x: -x.received):
                if self._consumed(m.key):
                    continue
                if recipient and m.recipients and not any(same_mailbox(r, recipient) for r in m.recipients):
                    continue
                domain = m.sender.rpartition("@")[2]
                blob = (m.sender + " " + m.subject + " " + m.text[:3000]).lower()
                mentions = any(t in blob for t in employer_tokens)
                if not (host_matches(domain, senders) or mentions):
                    continue
                token = extract(m, kind, hosts)
                if not token:
                    continue
                if token["kind"] == "link":
                    # Another tenant's link on the same ATS must never be opened for this run.
                    if not link_belongs(token["value"], host, employer_tokens):
                        continue
                    candidates.append((m, token, True))
                elif mentions or exclusive:
                    # Codes rarely name the tenant; accept unnamed ones only while this run holds
                    # the sender family's lock, which serializes challenges across workers.
                    candidates.append((m, token, mentions))
            if candidates:
                named = [c for c in candidates if c[2]]
                pool = named or candidates
                if len(pool) == 1 or len({c[1]["value"] for c in pool}) == 1 or named:
                    m, token, _ = pool[0]
                    if self._consume(m.key, run_id):
                        self.service.db.event(
                            run_id,
                            "email_matched",
                            "Matched a verification email",
                            {"kind": token["kind"], "sender_domain": m.sender.rpartition("@")[2]},
                        )
                        return token
            await asyncio.sleep(delay)
            delay = min(4.0, delay + 0.5)
        return None
