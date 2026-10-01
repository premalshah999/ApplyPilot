import argparse
import asyncio
import hashlib
from urllib.parse import urlsplit

from .config import settings


async def capture_session(config, url):
    from playwright.async_api import async_playwright

    from .network import public_url

    await public_url(url, config.allow_private_urls)
    host = urlsplit(url).hostname
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, executable_path=config.chromium_path or None)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        print("Sign in in the browser. Return here and press Enter when the application is visible.")
        await asyncio.to_thread(input)
        state = await context.storage_state()
        state["cookies"] = [
            c
            for c in state["cookies"]
            if host == c["domain"].lstrip(".") or host.endswith("." + c["domain"].lstrip("."))
        ]
        state["origins"] = [o for o in state["origins"] if urlsplit(o["origin"]).hostname == host]
        import json

        path = config.data_dir / "sessions" / (hashlib.sha256(host.encode()).hexdigest() + ".json")
        path.write_text(json.dumps(state))
        path.chmod(0o600)
        await browser.close()
        print(f"Saved employer session for {host}. Requeue the application in Studio.")


def main():
    parser = argparse.ArgumentParser(description="ApplyPilot Studio")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    sub.add_parser("token", help="Print this installation's dashboard access token")
    sub.add_parser("doctor", help="Check local configuration without contacting providers")
    login = sub.add_parser("login", help="Capture an employer session using your own local browser")
    login.add_argument("url")
    args = parser.parse_args()
    config = settings()
    if args.command == "token":
        print(config.app_token)
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="warning")
    elif args.command == "login":
        asyncio.run(capture_session(config, args.url))
    else:
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = Path(config.chromium_path or pw.chromium.executable_path).exists()
        for label, ready in [
            ("Chromium", browser),
            ("MiMo", bool(config.mimo_api_key)),
            ("Telegram", bool(config.telegram_bot_token and config.telegram_user_id)),
            ("CapSolver (optional)", bool(config.capsolver_api_key)),
        ]:
            print(f"{'READY' if ready else 'MISSING':7} {label}")
        print(
            f"{config.workers} workers · {config.application_timeout}s budget · {config.daily_application_limit} submissions/day"
        )
        print("Run `jobpilot token` to view your dashboard access token.")


if __name__ == "__main__":
    main()
