"""Read-only Gmail adapter. The app never sends mail or marks messages read."""

import base64
import email
import hashlib
import imaplib
import re
import ssl
import threading
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


# One logged-in connection per mailbox instead of a login per request. MailService serializes
# requests per mailbox; the lock also covers direct callers. A dropped connection is replaced once.
_clients = {}
_lock = threading.Lock()


def _connect(address, password):
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=12)
    client.login(address, password)
    return client


def _close(client):
    try:
        client.logout()
    except Exception:
        pass


def request(address, password, path, params=None):
    key = (address, hashlib.sha256(password.encode()).hexdigest())
    with _lock:
        for attempt in range(2):
            client = _clients.pop(key, None)
            reused = client is not None
            try:
                client = client or _connect(address, password)
                # Re-selecting refreshes the mailbox view so newly delivered mail is searchable.
                status, _ = client.select("INBOX", readonly=True)
                if status != "OK":
                    raise ValueError("Could not open Gmail inbox")
                result = _request(client, address, path, params or {})
            except (imaplib.IMAP4.abort, OSError):
                if client:
                    _close(client)
                if reused and not attempt:
                    continue  # A stale cached connection; log in once more.
                raise
            except Exception:
                if client:
                    _close(client)
                raise
            _clients[key] = client
            return result
    raise ValueError("Gmail connection failed")


def _request(client, address, path, params):
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
