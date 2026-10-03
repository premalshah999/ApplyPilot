"""A fake Gmail API mailbox for owned fixtures: authenticated (or not) messages, served to
MailService through an httpx MockTransport exactly like the real Gmail REST API."""

import base64
import html
import re
import secrets
import time
from urllib.parse import parse_qs

import httpx


class FakeGmail:
    def __init__(self, address):
        self.address = address.lower()
        self.messages = []

    def deliver(self, to, sender, subject, text, link=None, link_text="Verify", authenticated=True, received=None):
        domain = sender.rpartition("@")[2]
        body = f"<p>{html.escape(text)}</p>" + (f'<p><a href="{link}">{html.escape(link_text)}</a></p>' if link else "")
        auth = (
            f"mx.google.com; dkim=pass header.i=@{domain}; spf=pass; dmarc=pass (p=REJECT) header.from={domain}"
            if authenticated
            else f"mx.google.com; dkim=fail; spf=fail; dmarc=fail header.from={domain}"
        )
        message = {
            "id": "m" + secrets.token_hex(6),
            "internalDate": str(int((received or time.time()) * 1000)),
            "payload": {
                "mimeType": "text/html",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "To", "value": to},
                    {"name": "Subject", "value": subject},
                    {"name": "Authentication-Results", "value": auth},
                ],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
            },
        }
        self.messages.append(message)
        return message

    def route(self, request):
        if request.url.path.endswith("/messages"):
            q = parse_qs(request.url.query.decode()).get("q", [""])[0]
            after = int(m[1]) if (m := re.search(r"after:(\d+)", q)) else 0
            ids = [
                {"id": m["id"]}
                for m in reversed(self.messages)
                if int(m["internalDate"]) / 1000 >= after - 1
            ]
            return httpx.Response(200, json={"messages": ids[:20]})
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": self.address})
        wanted = request.url.path.rsplit("/", 1)[1]
        message = next((m for m in self.messages if m["id"] == wanted), None)
        return httpx.Response(200, json=message) if message else httpx.Response(404, json={})

    def transport(self):
        return httpx.MockTransport(self.route)

    def connect(self, service):
        """A connected OAuth mailbox with fake tokens; every request goes to this fake."""
        from jobpilot.db import Mailbox

        with service.db.exclusive() as s:
            s.add(
                Mailbox(
                    email=self.address,
                    credentials=service.mail.vault.seal(
                        {"access_token": "fixture", "refresh_token": "fixture", "expires_at": time.time() + 86400}
                    ),
                )
            )
        service.mail.transport = self.transport()
        return self
