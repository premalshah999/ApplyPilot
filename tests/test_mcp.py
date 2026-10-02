import json

import httpx
from mcp import Client

from jobpilot.mcp_server import create_server
from test_mail import configured
from test_browser import server as server


async def test_mcp_protocol_tools_auth_redaction_and_no_mutations(app, config):
    box, run, rule = await configured(app.state.service)
    server = create_server(config, transport=httpx.ASGITransport(app))
    async with Client(server) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {
            "application_status",
            "list_applications",
            "run_result",
            "email_verification_status",
        }
        assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in listing.tools)
        status = await client.call_tool("application_status")
        assert not status.is_error
        result = await client.call_tool("email_verification_status")
        assert not result.is_error
        encoded = json.dumps(result.model_dump())
        assert (
            "alex@example.test" in encoded and "fake-access" not in encoded and "fake-refresh" not in encoded
        )
        result = await client.call_tool("run_result", {"run_id": run["id"]})
        assert not result.is_error and "running" in json.dumps(result.model_dump())
        invalid = await client.call_tool("run_result", {"run_id": "../profile"})
        assert invalid.is_error
        unknown = await client.call_tool("submit_application", {})
        assert unknown.is_error


async def test_stdio_cli_initializes_and_calls_running_app(server):
    import os
    import sys
    from pathlib import Path
    from mcp.client.stdio import StdioServerParameters

    url, cfg, _ = server
    process = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from jobpilot.cli import main;main()", "mcp"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "BASE_URL": url, "APP_TOKEN": cfg.app_token, "DATA_DIR": str(cfg.data_dir)},
    )
    async with Client(process) as client:
        result = await client.call_tool("application_status")
        assert not result.is_error
        assert "control" in json.dumps(result.model_dump())
