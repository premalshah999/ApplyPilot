"""Read-only MCP over stdio for local clients. It uses the running app's auth."""

import re

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations


def create_server(config, transport=None):
    server = MCPServer(
        "ApplyPilot",
        instructions="Read-only application operations. No mailbox bodies, OTPs, credentials, or submission tools are exposed.",
    )
    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    async def get(path):
        async with httpx.AsyncClient(
            base_url=config.base_url,
            transport=transport,
            headers={"Authorization": "Bearer " + config.app_token},
            timeout=15,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            r = await client.get("/api" + path)
            if r.status_code != 200:
                raise ValueError(
                    f"ApplyPilot API returned HTTP {r.status_code}; check server and local configuration"
                )
            return r.json()

    @server.tool(annotations=annotations)
    async def application_status() -> dict:
        """Return worker controls, configured integration flags, and application counts."""
        data = await get("/snapshot")
        return {k: data[k] for k in ("control", "stats", "config")}

    @server.tool(annotations=annotations)
    async def list_applications(limit: int = 20) -> list[dict]:
        """List recent application IDs, companies, titles, and states; never submit or modify them."""
        data = await get("/snapshot")
        return [
            {k: j[k] for k in ("id", "company", "title", "ats", "status", "score")}
            for j in data["jobs"][: max(1, min(limit, 100))]
        ]

    @server.tool(annotations=annotations)
    async def run_result(run_id: str) -> dict:
        """Read a run's result and timing without exposing its private answers or browser state."""
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid run ID")
        data = await get("/runs/" + run_id)
        return {
            k: data["run"][k]
            for k in ("id", "job_id", "state", "mode", "reason", "elapsed", "cost", "model_calls")
        }

    @server.tool(annotations=annotations)
    async def email_verification_status() -> dict:
        """Read Gmail connection and verification status; no codes, links, or mail contents."""
        data = await get("/mail")
        return {k: data[k] for k in ("configured", "mailboxes", "challenges")}

    return server
