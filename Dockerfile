FROM node:22-bookworm-slim AS dashboard
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    ANONYMIZED_TELEMETRY=false \
    BROWSER_USE_LOGGING_LEVEL=warning \
    DATA_DIR=/app/.data \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && \
    .venv/bin/playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*
COPY jobpilot/ ./jobpilot/
COPY LICENSE ./
COPY docs/third-party.md ./docs/third-party.md
COPY docs/licenses/ ./docs/licenses/
COPY --from=dashboard /build/jobpilot/static ./jobpilot/static
RUN uv sync --frozen --no-dev && \
    useradd --uid 10001 --create-home pilot && \
    mkdir -p /app/.data && chown -R pilot:pilot /app/.data && \
    chmod -R a+rX /opt/browsers
USER pilot
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["jobpilot", "serve", "--host", "0.0.0.0", "--port", "8080"]
