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


async def probe(config, url, company=""):
    """Run one dry run outside the queue and print what each step saw. Nothing is submitted."""
    from .db import Database, Resume, Run
    from .discovery import add_job
    from .schemas import JobInput
    from .service import Service

    db = Database(config.database_url)
    service = Service(db, config)
    with db.session() as s:
        resume = s.query(Resume).filter(Resume.demo.is_(False)).first()
        if not resume:
            raise SystemExit("Upload a resume in the dashboard first")
        resume_id = resume.id
    job, _ = add_job(db, JobInput(url=url, company=company))
    try:
        run = await service.queue(job["id"], "dry_run", resume_id, retry=True)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Run {run['id']} (dry run) on {job['ats']} ...")
    await service.execute(run["id"])
    with db.session() as s:
        row = s.get(Run, run["id"])
        print(f"Result: {row.state} - {row.reason} ({row.elapsed:.0f}s, {row.model_calls} model calls)")
    print_trace(config, run["id"])


def print_trace(config, run_id):
    import json

    folder = config.data_dir / "runs" / run_id / "steps"
    if not folder.exists():
        print("No step trace for this run (TRACE_STEPS=false or it ended before the first step).")
        return
    for path in sorted(folder.glob("*.json")):
        step = json.loads(path.read_text())
        print(f"\n[{step['n']:02d}] {step['step']}  {step['url']}")
        if step["headings"]:
            print("     headings: " + " | ".join(step["headings"][:4]))
        for error in step["errors"][:4]:
            print("     error:    " + error[:160])
        for f in step["fields"][:40]:
            mark = "*" if f["required"] else " "
            state = "filled" if f["filled"] else "empty "
            print(
                f"     {mark} {state} {f['type']:<10} {f['label'][:70]}"
                + (f"  [{f['group']}]" if f.get("group") else "")
            )
        print("     controls: " + ", ".join(c["label"] for c in step["controls"][:14]))
    print(f"\nSnapshots (HTML + screenshots): {folder}")


def main():
    parser = argparse.ArgumentParser(description="ApplyPilot Studio")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    sub.add_parser("token", help="Print this installation's dashboard access token")
    doctor = sub.add_parser("doctor", help="Check local configuration")
    doctor.add_argument("--check-mail", action="store_true", help="Log in to Gmail over IMAP once")
    probe = sub.add_parser("probe", help="Dry-run one job URL and print the step trace (never submits)")
    probe.add_argument("url")
    probe.add_argument("--headed", action="store_true", help="Show the browser window (needs a display)")
    probe.add_argument("--company", default="")
    trace = sub.add_parser("trace", help="Summarize the step trace of a run")
    trace.add_argument("run_id")
    sub.add_parser("mcp", help="Serve read-only application tools over MCP stdio")
    verify = sub.add_parser(
        "verify", help="Exercise the running installation with two synthetic browser runs"
    )
    verify.add_argument("--report", help="Write evidence manifest (default: DATA_DIR/verification.json)")
    verify.add_argument(
        "--recheck", help="Verify an existing manifest after restart; creates no applications"
    )
    verify.add_argument("--timeout", type=int, default=420, help="Total queue wait budget in seconds")
    login = sub.add_parser("login", help="Capture an employer session using your own local browser")
    login.add_argument("url")
    desktop = sub.add_parser("browser", help="Open the local application browser for Docker workers")
    desktop.add_argument("--port", type=int, default=9224)
    args = parser.parse_args()
    config = settings()
    if args.command == "token":
        print(config.app_token)
    elif args.command == "mcp":
        from .mcp_server import create_server

        create_server(config).run(transport="stdio")
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="warning")
    elif args.command == "login":
        asyncio.run(capture_session(config, args.url))
    elif args.command == "probe":
        if args.headed:
            config.headless = False
        asyncio.run(probe(config, args.url, args.company))
    elif args.command == "trace":
        print_trace(config, args.run_id)
    elif args.command == "browser":
        import subprocess
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        with sync_playwright() as pw:
            executable = config.chromium_path or (
                str(chrome) if chrome.exists() else pw.chromium.executable_path
            )
        profile = config.data_dir / "desktop-browser"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.Popen(
            [
                executable,
                f"--remote-debugging-port={args.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("Application browser opened. Leave this Chrome window open while applications run.")
    elif args.command == "verify":
        import sys

        import httpx

        from .verify import VerificationFailed, verify_deployment, write_report

        if args.timeout < 1:
            parser.error("--timeout must be positive")
        try:
            report = verify_deployment(config, recheck=args.recheck, timeout=args.timeout)
            path = args.report or config.data_dir / "verification.json"
            write_report(path, report)
        except (VerificationFailed, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
            # Do not print transport errors, which can contain request credentials.
            reason = str(exc) if isinstance(exc, VerificationFailed) else type(exc).__name__
            print(f"FAIL: {reason}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"PASS: {', '.join(report['checks'])}")
        print(f"Evidence: {path}")
        print(
            "This verifies the owned demo. Live ATS flows and provider integrations remain separate checks."
        )
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
            ("Gmail app password (OTP/links)", bool(config.gmail_address and config.gmail_app_password)),
            ("Gmail OAuth (optional)", bool(config.google_client_id and config.google_client_secret)),
            ("TWOCAPTCHA image fallback (optional)", bool(config.twocaptcha_api_key)),
            ("Employer account password (else generated once)", bool(config.application_password)),
        ]:
            print(f"{'READY' if ready else 'MISSING':7} {label}")
        from .config import password_problems

        if config.application_password and (problems := password_problems(config.application_password)):
            print("WARN    APPLICATION_PASSWORD: " + "; ".join(problems))
        print(f"        Account email: {config.account_email or '(profile email)'} · engine: {config.engine}")
        if args.check_mail and config.gmail_address and config.gmail_app_password:
            from .gmail_imap import request

            try:
                request(
                    config.gmail_address.strip().lower(),
                    "".join(config.gmail_app_password.split()),
                    "/profile",
                )
                print("READY   Gmail IMAP login (read-only)")
            except Exception as exc:
                print(
                    f"FAIL    Gmail IMAP login: {type(exc).__name__}. Check the app password and that IMAP is enabled."
                )
        print(
            f"{config.workers} workers · {config.application_timeout}s budget · {config.daily_application_limit} submissions/day"
        )
        print("Run `jobpilot token` to view your dashboard access token.")


if __name__ == "__main__":
    main()
