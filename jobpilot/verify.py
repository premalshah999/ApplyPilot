"""Deployment acceptance against this installation's synthetic application only."""

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup


class VerificationFailed(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationFailed(message)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fetch(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    require(response.is_success, f"{method} {path} returned HTTP {response.status_code}")
    return response


def inspect_run(client, run_id, mode, deadline):
    require(bool(re.fullmatch(r"[a-f0-9]{32}", run_id)), "Report contains an invalid run ID")
    while True:
        detail = fetch(client, "GET", "/api/runs/" + run_id).json()
        run = detail["run"]
        if run["state"] not in {"queued", "running", "submitting"}:
            break
        require(time.monotonic() < deadline, f"Run {run_id} did not finish before the check deadline")
        time.sleep(0.5)
    expected = "confirmed" if mode == "submit" else "dry_run_passed"
    require(run["state"] == expected, f"Run {run_id}: {run['state']}: {run.get('reason', '')[:250]}")
    require(run["mode"] == mode, f"Run {run_id} has an unexpected mode")
    require(run["model_calls"] == 0 and run["cost"] == 0, "Synthetic check unexpectedly used a provider")
    answers = detail["answers"]
    require(
        bool(answers) and all(a.get("verified") for a in answers.values()), "Answer evidence is incomplete"
    )
    received = detail.get("demo_receipt")
    require(isinstance(received, dict), "Run is not an owned synthetic application")
    expected_count = 1 if mode == "submit" else 0
    require(received.get("submissions") == expected_count, "Server-side submission count is incorrect")
    if mode == "submit":
        require(
            run["receipt"].get("type") == "explicit_confirmation_page", "Confirmation evidence is missing"
        )
        require(
            run["receipt"].get("resume_sha256") == received.get("resume_sha256"),
            "The received PDF differs from the selected resume",
        )
    else:
        require(not run["receipt"], "A dry run unexpectedly recorded a submission receipt")
    screenshot = fetch(client, "GET", f"/api/runs/{run_id}/screenshot").content
    require(screenshot.startswith(b"\x89PNG\r\n\x1a\n"), "Screenshot evidence is missing or invalid")
    return {
        "id": run_id,
        "mode": mode,
        "state": run["state"],
        "elapsed_seconds": round(run["elapsed"], 3),
        "submissions_received": received["submissions"],
        "receipt_sha256": fingerprint(run["receipt"]),
        "server_receipt_sha256": fingerprint(received),
        "answers_sha256": fingerprint(answers),
        "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
    }


def verify_deployment(config, *, recheck=None, timeout=420):
    """Return an evidence manifest, or fail. Never submit to an external employer."""
    target = config.base_url.rstrip("/")
    previous = json.loads(Path(recheck).read_text()) if recheck else None
    if previous:
        require(previous.get("scope") == "owned_demo_only" and previous.get("ok"), "Invalid evidence report")
        require(previous.get("target") == target, "Report belongs to a different BASE_URL")
        require(
            sorted(r.get("mode", "") for r in previous.get("runs", [])) == ["dry_run", "submit"],
            "Report must contain both a dry run and a submission",
        )
    checks = []
    deadline = time.monotonic() + timeout
    with httpx.Client(
        base_url=target,
        headers={"Authorization": "Bearer " + config.app_token},
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        health = fetch(client, "GET", "/healthz").json()
        require(health.get("status") == "ok", "Database health check failed")
        checks.append("database_health")
        require(
            client.get("/api/snapshot", headers={"Authorization": ""}).status_code == 401,
            "Unauthenticated dashboard access was not rejected",
        )
        snapshot = fetch(client, "GET", "/api/snapshot").json()
        checks.append("authenticated_api")
        markup = fetch(client, "GET", "/").text
        page = BeautifulSoup(markup, "html.parser")
        assets = [s.get("src", "") for s in page.select('script[type="module"][src]')]
        assets += [s.get("href", "") for s in page.select('link[rel="stylesheet"][href]')]
        require(bool(assets), "Built dashboard assets are missing")
        for asset in assets:
            require(asset.startswith("/assets/"), "Dashboard asset has an unexpected origin")
            require(bool(fetch(client, "GET", asset).content), "Dashboard asset is empty")
        checks.append("dashboard_assets")
        if previous:
            runs = [inspect_run(client, r["id"], r["mode"], deadline) for r in previous["runs"]]
            require(runs == previous["runs"], "Stored receipt, answer ledger, or screenshot changed")
            checks.append("persisted_evidence_matches")
        else:
            require(snapshot["config"]["demo"], "Set ENABLE_DEMO=true and restart before verification")
            require(snapshot["config"]["workers_enabled"], "Workers are disabled")
            require(not snapshot["control"]["paused"], "Workers are paused; resume them before verification")
            runs = []
            for mode in ("dry_run", "submit"):
                run = fetch(client, "POST", "/api/demo", json={"mode": mode}).json()
                runs.append(inspect_run(client, run["id"], mode, deadline))
            checks.extend(["dry_run_zero_submissions", "browser_submission_received_once", "saved_evidence"])
    return {
        "version": 1,
        "ok": True,
        "scope": "owned_demo_only",
        "target": target,
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "runs": runs,
    }


def write_report(path, report):
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="verification-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
