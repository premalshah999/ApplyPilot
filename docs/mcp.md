# MCP integration

ApplyPilot includes a read-only MCP server for local Codex, Claude, VS Code, and other compatible clients. The running application remains responsible for queueing, browser execution, verification, and final submission.

## Start the server

Start ApplyPilot normally. From the same repository/environment, configure your MCP client to launch this stdio command:

```sh
uv run jobpilot mcp
```

Set the working directory to the repository so `.env`, `DATA_DIR`, and `BASE_URL` resolve to the same installation. The MCP process reads that installation's access token and calls its authenticated API. Do not paste the token into a committed MCP config. The app must be running; the MCP process does not start its workers.

For clients with no working-directory setting, an equivalent absolute command is:

```sh
uv --directory /absolute/path/to/ApplyPilot run jobpilot mcp
```

For an existing Docker installation, a local client can launch:

```sh
docker compose -f /absolute/path/to/ApplyPilot/compose.yaml exec -T app jobpilot mcp
```

Use the actual Compose filename from your checkout. Stdio must stay attached (`-T` avoids terminal control bytes). Client configuration formats differ; configure a local stdio server with the command/arguments above. There is no public MCP HTTP endpoint.

## Included tools

| Tool | Use |
| --- | --- |
| `application_status` | Worker controls, daily counts, integration configuration flags |
| `list_applications` | Up to 100 recent jobs with company, role, ATS, score, and state |
| `run_result` | A specific run's result, reason, elapsed time, and model cost/call count |
| `email_verification_status` | Connected mailbox status and redacted verification history |

Example requests to your coding assistant: “Which applications need review?”, “Why did this run stop?”, or “Is Gmail connected and are any email challenges pending?” The server does not expose inbox bodies, codes, tokenized links, stored credentials, submission commands, shell access, or arbitrary URLs. Tool annotations declare read-only behavior, and the implementation contains only fixed authenticated GET operations.

## Where other MCP servers help

| MCP use | Decision |
| --- | --- |
| ApplyPilot operations | Implemented here; inspect status from your coding environment |
| Microsoft Playwright MCP | Useful during development to inspect an employer's actual widgets and build/debug adapters; not added to the autonomous production loop |
| GitHub MCP | Useful in development for issue/PR/CI inspection; no repository credential is required by the running application |
| Broad Gmail/workspace MCP | Not needed for OTP processing; direct Gmail API requests keep challenge correlation, token handling, and deadlines inside ApplyPilot |

MCP standardizes tool discovery and calls; it does not solve answer quality, account state, dropdown selection, or browser reliability on its own. The production browser path uses the existing bounded browser tools, and email verification makes no LLM calls. We avoid a second general-purpose browser or mailbox agent competing for control of the same application.

The MCP tests initialize a real protocol client/server, enumerate tools, make authenticated calls into the app, verify redaction, reject malformed IDs, and confirm there is no submission tool. Public provider access and individual client installations are separate deployment checks.

References: [MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture), [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp).
