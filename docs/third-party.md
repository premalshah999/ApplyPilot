# Third-party notices

The repository keeps AGPL-3.0 licensing and attribution to its predecessor, [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot). The Studio application is a replacement runtime, not a continuation of the old Claude Code pipeline.

Runtime and build dependencies retain their respective licenses. Exact dependency versions and hashes are recorded in `uv.lock` and `frontend/package-lock.json`.

- Browser Use, FastAPI, PydanticAI, SQLAlchemy, React, Vite, and many utility libraries are independently maintained open-source projects.
- The official MCP Python SDK provides protocol transport; cryptography provides authenticated encryption. Their installed distributions retain their license notices.
- DBOS, Playwright, and their dependencies include their own license notices in installed distributions.
- DM Sans and Manrope fonts are self-hosted via Fontsource packages under SIL Open Font License 1.1. Their package distributions include the full license texts.
- Lucide icons retain the Lucide/Feather license notices included in the package.
- MiMo inference, Telegram, and CapSolver are external services with separate terms and billing. A source-available application does not make those services free.

The dashboard build bundles only the selected fonts/icons and frontend code. Private profile data, uploaded resumes, employer sessions, model keys, and bot keys are not source-code assets.
