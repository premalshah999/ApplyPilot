import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


async def public_url(url: str, allow_private=False):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Only public http(s) URLs are supported")
    if allow_private:
        return
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, p.hostname, p.port or 443, 0, socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ValueError("The URL hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Private, loopback and metadata-service URLs are blocked")


async def fetch(url, *, method="GET", payload=None, allow_private=False):
    """Validate every redirect, bound response size, and do not forward credentials."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            await public_url(url, allow_private)
            async with client.stream(
                method, url, json=payload, headers={"User-Agent": "ApplyPilot/1.0"}
            ) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    from urllib.parse import urljoin

                    url = urljoin(url, r.headers["location"])
                    if r.status_code == 303:
                        method, payload = "GET", None
                    continue
                r.raise_for_status()
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > 4_000_000:
                        raise ValueError("Source response exceeds 4 MB")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace"), str(r.url)
    raise ValueError("Too many redirects")
