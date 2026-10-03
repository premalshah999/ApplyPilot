import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
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
    telegram_bot_token: str = ""
    telegram_user_id: str = ""
    capsolver_api_key: str = ""
    twocaptcha_api_key: str = ""
    application_password: str = ""
    captcha_timeout: int = Field(default=90, ge=10, le=180)
    captcha_max_attempts: int = Field(default=2, ge=1, le=3)
    google_client_id: str = ""
    google_client_secret: str = ""
    gmail_address: str = ""
    gmail_app_password: str = ""
    mail_encryption_key: str = ""
    mail_poll_seconds: float = Field(default=3, ge=0.1, le=60)
    mail_wait_seconds: int = Field(default=60, ge=5, le=120)
    workers: int = Field(default=3, ge=1, le=8)
    application_timeout: int = Field(default=180, ge=30, le=600)
    daily_application_limit: int = Field(default=100, ge=1, le=200)
    daily_budget_usd: float = Field(default=5, gt=0)
    max_model_calls: int = Field(default=18, ge=1, le=40)
    timezone: str = "America/New_York"
    chromium_path: str = ""
    browser_cdp_url: str = ""
    headless: bool = True
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


@lru_cache
def settings() -> Settings:
    return Settings().prepare()
