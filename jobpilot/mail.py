"""Gmail OAuth and narrowly scoped verification. No sending or inbox export."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from email.utils import getaddresses
from urllib.parse import urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import Mailbox, MailChallenge, MailConsumption, MailOAuth, MailRule, Run, now, record
from .network import public_url

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
LIVE = {"pending", "matched", "consumed"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin(url):
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise ValueError("Provide an http(s) origin without embedded credentials")
    host = p.hostname.lower()
    port = p.port
    if ":" in host:
        host = f"[{host}]"
    return f"{p.scheme}://{host}" + (
        f":{port}" if port and port != (443 if p.scheme == "https" else 80) else ""
    )


class RuleInput(BaseModel):
    mailbox_id: str
    employer_origin: str = Field(max_length=2048)
    sender_domains: list[str] = Field(min_length=1, max_length=20)
    link_origins: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True


class Vault:
    def __init__(self, config):
        path = config.data_dir / "mail-key"
        if config.mail_encryption_key:
            key = config.mail_encryption_key.encode()
        else:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            key = path.read_bytes().strip()
        self.cipher = Fernet(key)

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))


def extract_message(message, rule, challenge):
    """Return one unambiguous token; message text never becomes model instructions."""
    received = int(message.get("internalDate", "0")) / 1000
    if received < challenge["since"] or received > challenge["expires"]:
        return None
    headers = message.get("payload", {}).get("headers", [])
    values = {}
    for h in headers:
        values.setdefault(h["name"].lower(), []).append(h["value"])
    senders = getaddresses(values.get("from", []))
    if len(senders) != 1:
        return None
    domain = senders[0][1].rpartition("@")[2].lower()
    if domain not in rule["sender_domains"]:
        return None
    recipients = getaddresses(values.get("to", []) + values.get("delivered-to", []))
    if challenge["recipient"].lower() not in {a.lower() for _, a in recipients}:
        return None
    # Only trust Google's receiving authentication result with aligned DMARC.
    receiving_auth = next(
        (
            value
            for value in values.get("authentication-results", [])
            if re.match(r"\s*mx\.google\.com\s*;", value, re.I)
        ),
        "",
    )
    authenticated = re.search(r"\bdmarc=pass\b", receiving_auth, re.I) and re.search(
        r"\bheader\.from=" + re.escape(domain) + r"(?:\s|;|$)", receiving_auth, re.I
    )
    if not authenticated:
        return None
    texts, links, size = [], [], 0

    def walk(part, depth=0):
        nonlocal size
        if depth > 15 or size > 256_000:
            return
        encoded = part.get("body", {}).get("data", "")
        if encoded and part.get("mimeType") in {"text/plain", "text/html"}:
            if len(encoded) > 350_000:
                return
            try:
                raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", "replace")
            except (ValueError, TypeError):
                return
            size += len(raw)
            if size > 256_000:
                return
            soup = BeautifulSoup(raw, "html.parser") if part["mimeType"] == "text/html" else None
            text = soup.get_text(" ", strip=True) if soup else raw
            texts.append(text)
            if soup:
                for a in soup.select("a[href]"):
                    if re.search(r"verif|confirm|sign.?in|log.?in|continue|activate", a.get_text(" "), re.I):
                        links.append(a["href"])
            else:
                # Plain-text links need a verification cue on the adjacent line.
                for match in re.finditer(r"https://[^\s<>\"']+", raw):
                    if re.search(
                        r"verif|confirm|sign.?in|log.?in|activate",
                        raw[max(0, match.start() - 120) : match.end()],
                        re.I,
                    ):
                        links.append(match.group().rstrip(".,)"))
        for child in part.get("parts", [])[:30]:
            walk(child, depth + 1)

    walk(message.get("payload", {}))
    text = "\n".join(texts)
    codes = set(
        re.findall(
            r"(?i:\b(?:verification code|security code|authentication code|one.time (?:code|passcode|password)|confirmation code|login code|sign.in code|otp|code|pin)\s*(?:(?:is|below)\s*)?[:#-]?\s*)\b([A-Z0-9]{4,10})\b",
            text,
        )
    )
    codes.update(
        re.findall(
            r"\b([0-9]{4,10})\b(?i:\s+is your (?:verification|security|authentication|one.time|login|sign.in) (?:code|passcode|password))",
            text,
        )
    )
    codes -= {"PLEASE", "BELOW", "EXPIRES", "VALID", "YOUR", "REQUEST"}
    valid_links = set()
    for link in links:
        try:
            if urlsplit(link).scheme == "https" and origin(link) in rule["link_origins"]:
                valid_links.add(link)
        except ValueError:
            continue
    kind = challenge["kind"]
    if kind in {"code", "auto"} and len(codes) == 1:
        return {"kind": "code", "value": next(iter(codes))}
    if kind in {"link", "auto"} and len(valid_links) == 1 and not codes:
        return {"kind": "link", "value": next(iter(valid_links))}
    return None


class MailService:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.vault = Vault(config)
        self.transport = None  # An injected transport is used only by owned fixtures.
        self.locks = {}
        self.poll_lock = asyncio.Lock()

    def client(self):
        return httpx.AsyncClient(
            transport=self.transport, timeout=12, follow_redirects=False, trust_env=False
        )

    @property
    def callback_url(self):
        return self.config.base_url.rstrip("/") + "/oauth/gmail/callback"

    def summary(self):
        with self.db.session() as s:
            boxes = [
                {k: v for k, v in record(m).items() if k != "credentials"} for m in s.scalars(select(Mailbox))
            ]
            rules = [record(r) for r in s.scalars(select(MailRule))]
            challenges = [
                {k: v for k, v in record(c).items() if k not in {"payload", "message_id"}}
                for c in s.scalars(select(MailChallenge).order_by(MailChallenge.created_at.desc()).limit(50))
            ]
        return {
            "configured": bool(self.config.google_client_id and self.config.google_client_secret),
            "redirect_uri": self.callback_url,
            "mailboxes": boxes,
            "rules": rules,
            "challenges": challenges,
        }

    def begin_oauth(self):
        if not self.config.google_client_id or not self.config.google_client_secret:
            raise ValueError("Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server first")
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.db.exclusive() as s:
            for old in s.scalars(select(MailOAuth).where(MailOAuth.expires < time.time())):
                s.delete(old)
            s.add(
                MailOAuth(
                    state_hash=digest(state),
                    browser_hash=digest(browser),
                    verifier=self.vault.seal(verifier),
                    expires=time.time() + 600,
                )
            )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.config.google_client_id,
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent select_account",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ), browser

    async def finish_oauth(self, state, browser, code):
        with self.db.exclusive() as s:
            pending = s.get(MailOAuth, digest(state))
            if (
                not pending
                or pending.expires < time.time()
                or not browser
                or not hmac.compare_digest(pending.browser_hash, digest(browser))
            ):
                raise ValueError("Gmail authorization expired or belongs to another browser. Start again.")
            verifier = self.vault.open(pending.verifier)
            s.delete(pending)  # Single-use even when token exchange fails.
        async with self.client() as client:
            r = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "redirect_uri": self.callback_url,
                    "code_verifier": verifier,
                },
            )
            if r.status_code != 200:
                raise ValueError("Google could not complete authorization. Reconnect Gmail.")
            token = r.json()
            if (
                SCOPE not in token.get("scope", "").split()
                or not token.get("access_token")
                or not token.get("refresh_token")
            ):
                raise ValueError("Read-only Gmail access and offline consent are required. Reconnect Gmail.")
            p = await client.get(
                API_URL + "/profile", headers={"Authorization": "Bearer " + token["access_token"]}
            )
            if p.status_code != 200:
                raise ValueError("Gmail API is unavailable. Enable it in your Google Cloud project.")
            email = p.json()["emailAddress"].lower()
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        with self.db.exclusive() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.email == email))
            if box:
                box.credentials, box.state, box.error = self.vault.seal(token), "connected", ""
            else:
                s.add(Mailbox(email=email, credentials=self.vault.seal(token)))
        return email

    async def request(self, mailbox_id, path, params=None):
        async with self.locks.setdefault(mailbox_id, asyncio.Lock()):
            with self.db.session() as s:
                box = s.get(Mailbox, mailbox_id)
                if not box or box.state != "connected":
                    raise ValueError("Reconnect the Gmail mailbox")
                stored = box.credentials
                token = self.vault.open(stored)
            async with self.client() as client:
                if token.get("expires_at", 0) < time.time() + 60:
                    r = await client.post(
                        TOKEN_URL,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"],
                            "client_id": self.config.google_client_id,
                            "client_secret": self.config.google_client_secret,
                        },
                    )
                    if r.status_code != 200:
                        if r.status_code in {400, 401}:
                            self.connection_error(
                                mailbox_id,
                                "reconnect_required",
                                "Google authorization expired or was revoked",
                            )
                        raise ValueError("Gmail token refresh failed")
                    token.update(r.json())
                    token["expires_at"] = time.time() + token.get("expires_in", 3600)
                    with self.db.exclusive() as s:
                        box = s.get(Mailbox, mailbox_id)
                        if not box or box.credentials != stored:
                            raise ValueError("Gmail connection changed; retry the operation")
                        box.credentials = self.vault.seal(token)
                r = await client.get(
                    API_URL + path,
                    params=params,
                    headers={"Authorization": "Bearer " + token["access_token"]},
                )
                if r.status_code in {401, 403}:
                    self.connection_error(
                        mailbox_id, "reconnect_required", "Gmail permission or API access needs attention"
                    )
                if r.status_code != 200:
                    raise ValueError("Gmail request failed; check connection or retry later")
                with self.db.exclusive() as s:
                    box = s.get(Mailbox, mailbox_id)
                    if box:
                        box.checked_at, box.error = now(), ""
                return r.json()

    def connection_error(self, mailbox_id, state, error):
        with self.db.exclusive() as s:
            if box := s.get(Mailbox, mailbox_id):
                box.state, box.error = state, error

    async def save_rule(self, data):
        employer = origin(data.employer_origin)
        employer_path = urlsplit(data.employer_origin).path.rstrip("/") or "/"
        links = list(dict.fromkeys([employer] + [origin(x) for x in data.link_origins]))
        for url in links:
            if not url.startswith("https://") and not self.config.allow_private_urls:
                raise ValueError("Employer and verification origins must use HTTPS")
            await public_url(url, self.config.allow_private_urls)
        domains = [d.strip().lower() for d in data.sender_domains]
        if any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", d) or ".." in d
            for d in domains
        ):
            raise ValueError("Use exact sender domains, without @, wildcards, or URLs")
        with self.db.exclusive() as s:
            if not s.get(Mailbox, data.mailbox_id):
                raise ValueError("Connect this mailbox first")
            rule = s.scalar(
                select(MailRule).where(
                    MailRule.employer_origin == employer, MailRule.employer_path == employer_path
                )
            )
            if rule and s.scalar(
                select(MailChallenge.id).where(
                    MailChallenge.rule_id == rule.id, MailChallenge.state.in_(LIVE)
                )
            ):
                raise ValueError("Wait for this employer's active verification before changing its rule")
            if not rule:
                rule = MailRule(employer_origin=employer, employer_path=employer_path)
                s.add(rule)
            rule.mailbox_id, rule.sender_domains, rule.link_origins, rule.enabled = (
                data.mailbox_id,
                domains,
                links,
                data.enabled,
            )
            s.flush()
            return record(rule)

    def rule_for(self, url):
        with self.db.session() as s:
            rows = list(
                s.scalars(
                    select(MailRule).where(
                        MailRule.employer_origin == origin(url), MailRule.enabled.is_(True)
                    )
                )
            )
            path = urlsplit(url).path
            for rule in sorted(rows, key=lambda r: len(r.employer_path), reverse=True):
                if (
                    rule.employer_path == "/"
                    or path == rule.employer_path
                    or path.startswith(rule.employer_path + "/")
                ):
                    return record(rule)
        return None

    def begin(self, run_id, rule, recipient, kind="auto", since=None):
        if kind not in {"auto", "code", "link"} or not re.fullmatch(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", recipient
        ):
            raise ValueError("Email verification requires a valid applicant email address")
        current = time.time()
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            box = s.get(Mailbox, rule["mailbox_id"])
            if not run or run.state != "running" or not box or box.state != "connected":
                raise ValueError("Verification requires a running application and connected mailbox")
            previous = s.scalar(
                select(MailChallenge).where(MailChallenge.run_id == run_id, MailChallenge.state.in_(LIVE))
            )
            if previous:
                return record(previous)
            # Serialize ambiguous sender groups even when different employers share a vendor.
            for other in s.scalars(
                select(MailChallenge).where(
                    MailChallenge.mailbox_id == box.id,
                    MailChallenge.state.in_(LIVE),
                    MailChallenge.expires > current,
                )
            ):
                other_rule = s.get(MailRule, other.rule_id)
                if other_rule and set(other_rule.sender_domains) & set(rule["sender_domains"]):
                    raise ValueError(
                        "Another application is verifying with this sender; retry after it finishes"
                    )
            c = MailChallenge(
                run_id=run_id,
                rule_id=rule["id"],
                mailbox_id=box.id,
                recipient=recipient.lower(),
                kind=kind,
                since=since or current,
                expires=current + self.config.mail_wait_seconds,
            )
            s.add(c)
            s.flush()
            return record(c)

    def outcome(self, challenge_id, state, reason=""):
        with self.db.exclusive() as s:
            if (c := s.get(MailChallenge, challenge_id)) and c.state in LIVE:
                c.state, c.reason, c.payload = state, reason[:200], ""

    def claim(self, challenge_id):
        with self.db.exclusive() as s:
            c = s.get(MailChallenge, challenge_id)
            if not c or c.state != "matched" or c.expires < time.time():
                return None
            token = self.vault.open(c.payload)
            c.state, c.payload = "consumed", ""
            return token

    async def wait(self, challenge_id):
        while True:
            with self.db.session() as s:
                c = s.get(MailChallenge, challenge_id)
                if not c or c.state not in {"pending", "matched"}:
                    raise ValueError("Email verification was cancelled or expired")
                expires = c.expires
            if token := self.claim(challenge_id):
                return token
            if time.time() >= expires:
                self.outcome(
                    challenge_id, "expired", "No uniquely matching verification email arrived in time"
                )
                raise ValueError("Verification email did not arrive before the deadline")
            await asyncio.sleep(min(self.config.mail_poll_seconds, max(0.05, expires - time.time())))

    async def poll_once(self):
        async with self.poll_lock:
            with self.db.exclusive() as s:
                current = time.time()
                for c in s.scalars(select(MailChallenge).where(MailChallenge.state.in_(LIVE))):
                    run = s.get(Run, c.run_id)
                    if c.expires < current or not run or run.state != "running":
                        c.state, c.payload, c.reason = (
                            "expired",
                            "",
                            "Application or verification deadline ended",
                        )
                pending = [
                    record(c)
                    for c in s.scalars(select(MailChallenge).where(MailChallenge.state == "pending"))
                ]
            for c in pending:
                with self.db.session() as s:
                    row = s.get(MailRule, c["rule_id"])
                    rule = record(row) if row and row.enabled else None
                if not rule:
                    self.outcome(c["id"], "cancelled", "Verification rule was removed or disabled")
                    continue
                try:
                    query = (
                        f"after:{int(c['since'])} to:{c['recipient']} "
                        + "{"
                        + " ".join("from:" + d for d in rule["sender_domains"])
                        + "}"
                    )
                    listed = await self.request(
                        c["mailbox_id"],
                        "/messages",
                        {"q": query, "maxResults": 20, "includeSpamTrash": "false"},
                    )
                    if listed.get("nextPageToken"):
                        self.outcome(
                            c["id"],
                            "failed",
                            "Too many candidate messages to match within the verification budget",
                        )
                        continue
                    matches = []
                    for m in listed.get("messages", [])[:20]:
                        with self.db.session() as s:
                            if s.get(MailConsumption, c["mailbox_id"] + ":" + m["id"]):
                                continue
                        msg = await self.request(c["mailbox_id"], "/messages/" + m["id"], {"format": "full"})
                        if token := extract_message(msg, rule, c):
                            matches.append((m["id"], token))
                    if len(matches) != 1:
                        if len(matches) > 1:
                            self.outcome(
                                c["id"], "failed", "Multiple matching messages; no code or link was guessed"
                            )
                        continue
                    message_id, token = matches[0]
                    with self.db.exclusive() as s:
                        row = s.get(MailChallenge, c["id"])
                        key = c["mailbox_id"] + ":" + message_id
                        if (
                            row
                            and row.state == "pending"
                            and row.expires > time.time()
                            and not s.get(MailConsumption, key)
                        ):
                            row.payload, row.message_id, row.state = (
                                self.vault.seal(token),
                                message_id,
                                "matched",
                            )
                            s.add(MailConsumption(key=key, challenge_id=row.id))
                except (ValueError, httpx.HTTPError, KeyError, TypeError):
                    # Do not include provider responses, URLs, codes, or credentials in diagnostics.
                    with self.db.exclusive() as s:
                        row = s.get(MailChallenge, c["id"])
                        box = s.get(Mailbox, c["mailbox_id"])
                        if row:
                            row.reason = "Mailbox request failed; check the connection"
                            if not box or box.state != "connected":
                                row.state, row.payload = "failed", ""

    async def poll(self):
        while True:
            try:
                await self.poll_once()
            except Exception:
                pass  # Connection/challenge status is the public diagnostic, never secret-bearing errors.
            await asyncio.sleep(self.config.mail_poll_seconds)

    async def disconnect(self, mailbox_id):
        async with self.locks.setdefault(mailbox_id, asyncio.Lock()):
            with self.db.exclusive() as s:
                box = s.get(Mailbox, mailbox_id)
                if not box:
                    raise ValueError("Mailbox not found")
                token = self.vault.open(box.credentials)
                s.delete(box)
                for rule in s.scalars(select(MailRule).where(MailRule.mailbox_id == mailbox_id)):
                    s.delete(rule)
                for c in s.scalars(
                    select(MailChallenge).where(
                        MailChallenge.mailbox_id == mailbox_id, MailChallenge.state.in_(LIVE)
                    )
                ):
                    c.state, c.payload, c.reason = "cancelled", "", "Mailbox disconnected"
            revoked = False
            try:
                async with self.client() as client:
                    r = await client.post(
                        "https://oauth2.googleapis.com/revoke", data={"token": token["refresh_token"]}
                    )
                    revoked = r.status_code == 200
            except httpx.HTTPError:
                pass
            return {"disconnected": True, "revoked": revoked}
