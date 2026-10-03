import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)
    data_dir: Path = Path(".data")
    database_url: str = ""
    dbos_database_url: str = ""
    app_token: str = ""
    secure_cookies: bool = False
    base_url: str = "http://127.0.0.1:8080"
    mimo_api_key: str = ""
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.6-pro"
    mimo_input_price: float = Field(default=0.435, ge=0)
    mimo_output_price: float = Field(default=0.87, ge=0)
    # MiMo enables deep thinking by default. Form answers need fast JSON, not reasoning traces.
    # "disabled", "enabled", or empty to send nothing (for other OpenAI-compatible providers).
    mimo_thinking: str = "disabled"
    telegram_bot_token: str = ""
    telegram_user_id: str = ""
    # Seconds a worker keeps its page open waiting for a Telegram answer before deferring the job.
    telegram_wait_seconds: int = Field(default=120, ge=0, le=900)
    capsolver_api_key: str = ""
    # Image-coordinate fallback for hCaptcha puzzles. Never sent to CapSolver.
    twocaptcha_api_key: str = ""
    captcha_timeout: int = Field(default=90, ge=10, le=180)
    captcha_max_attempts: int = Field(default=2, ge=1, le=5)
    # Paid image rounds per run (one hCaptcha challenge often needs 2-3 rounds).
    captcha_max_rounds: int = Field(default=4, ge=1, le=8)
    # Optional password for NEW employer accounts; otherwise one is generated and encrypted once.
    # ACCOUNT_PASSWORD is accepted as an older name for the same setting.
    application_password: str = Field(
        default="", validation_alias=AliasChoices("application_password", "account_password")
    )
    # Optional login email override; defaults to the profile email.
    account_email: str = ""
    # Recover an employer account through its reset email when the saved password is rejected.
    account_password_reset: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    gmail_address: str = ""
    gmail_app_password: str = ""
    mail_encryption_key: str = ""
    mail_poll_seconds: float = Field(default=3, ge=0.1, le=60)
    mail_wait_seconds: int = Field(default=90, ge=5, le=300)
    workers: int = Field(default=3, ge=1, le=12)
    # Single-page forms (Greenhouse, Lever, Ashby...).
    application_timeout: int = Field(default=180, ge=30, le=900)
    # Multi-page wizards with accounts and email verification (Workday, Oracle, iCIMS, Taleo...).
    multipage_timeout: int = Field(default=480, ge=60, le=1200)
    daily_application_limit: int = Field(default=100, ge=1, le=500)
    daily_budget_usd: float = Field(default=5, gt=0)
    max_model_calls: int = Field(default=18, ge=1, le=80)
    timezone: str = "America/New_York"
    chromium_path: str = ""
    # Desktop Chrome for Docker on Mac (`python -m jobpilot.cli browser`), e.g. http://host.docker.internal:9224
    browser_cdp_url: str = ""
    headless: bool = True
    # Skip images, media and fonts on employer pages. Captcha providers are never blocked.
    block_assets: bool = True
    # Save a DOM snapshot, screenshot and decision per wizard step under DATA_DIR/runs/<id>/steps.
    trace_steps: bool = True
    # "adapters": deterministic ATS drivers for multi-page portals, the model only for answers.
    # "agent": previous behavior for every non single-page form.
    engine: str = "adapters"
    enable_workers: bool = True
    enable_demo: bool = True
    allow_private_urls: bool = False  # Tests only. Never enabled by the production image.

    def prepare(self):
        self.data_dir = self.data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("resumes", "runs", "sessions"):
            (self.data_dir / name).mkdir(exist_ok=True, mode=0o700)
        if not self.app_token:
            token_file = self.data_dir / "access-token"
            if not token_file.exists():
                fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_urlsafe(32))
            self.app_token = token_file.read_text().strip()
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'application.db'}"
        if not self.dbos_database_url:
            self.dbos_database_url = f"sqlite:///{self.data_dir / 'workflows.db'}"
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")
        return self


def password_problems(password: str) -> list[str]:
    """The strictest common ATS policy (Workday, Taleo, iCIMS, SuccessFactors)."""
    problems = []
    if len(password) < 10:
        problems.append("use at least 10 characters")
    if len(password) > 30:
        problems.append("use at most 30 characters (some Taleo and Oracle tenants reject longer)")
    if not any(c.isupper() for c in password):
        problems.append("add an uppercase letter")
    if not any(c.islower() for c in password):
        problems.append("add a lowercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("add a digit")
    if not any(not c.isalnum() for c in password):
        problems.append("add a symbol")
    return problems


@lru_cache
def settings() -> Settings:
    return Settings().prepare()
