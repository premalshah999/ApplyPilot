"""Read-only Gmail adapter. The app never sends mail or marks messages read."""

import base64
import email
import imaplib
import re
import ssl
from datetime import UTC, datetime
from email import policy


def mime_payload(message):
    result = {
        "mimeType": message.get_content_type(),
        "headers": [{"name": k, "value": str(v)} for k, v in message.items()],
    }
    if message.is_multipart():
        result["parts"] = [mime_payload(p) for p in message.iter_parts()]
    elif message.get_content_type() in {"text/plain", "text/html"}:
        content = message.get_payload(decode=True) or b""
        text = content.decode(message.get_content_charset() or "utf-8", "replace")
        result["body"] = {"data": base64.urlsafe_b64encode(text.encode()).decode()}
    return result


def request(address, password, path, params=None):
    params = params or {}
    with imaplib.IMAP4_SSL(
        "imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=12
    ) as client:
        client.login(address, password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Could not open Gmail inbox")
        if path == "/profile":
            return {"emailAddress": address}
        if path == "/messages":
            query = str(params.get("q", "newer_than:1d"))
            # Quote the IMAP search value as a string; never permit injected search commands.
            query = (
                '"'
                + query.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
                + '"'
            )
            status, data = client.uid("search", None, "X-GM-RAW", query)
            if status != "OK":
                raise ValueError("Gmail search failed")
            ids = data[0].decode().split()
            limit = min(int(params.get("maxResults", 20)), 50)
            result = {"messages": [{"id": i} for i in ids[-limit:]]}
            if len(ids) > limit:
                result["nextPageToken"] = "more"
            return result
        uid = path.removeprefix("/messages/")
        if not uid.isdigit():
            raise ValueError("Invalid Gmail message ID")
        status, data = client.uid("fetch", uid, "(INTERNALDATE RFC822.SIZE)")
        metadata = b" ".join(x for x in data if isinstance(x, bytes))
        size = re.search(rb"RFC822.SIZE (\d+)", metadata)
        if status != "OK" or not size or int(size[1]) > 1_000_000:
            raise ValueError("Gmail message exceeds the verification size limit")
        status, data = client.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[])")
        item = next((x for x in data if isinstance(x, tuple)), None)
        if status != "OK" or not item:
            raise ValueError("Gmail message unavailable")
        date_match = re.search(rb'INTERNALDATE "([^"]+)"', item[0])
        received = (
            datetime.strptime(date_match[1].decode(), "%d-%b-%Y %H:%M:%S %z")
            if date_match
            else datetime.now(UTC)
        )
        msg = email.message_from_bytes(item[1], policy=policy.default)
        return {
            "id": uid,
            "internalDate": str(int(received.timestamp() * 1000)),
            "payload": mime_payload(msg),
        }
