"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Fingerprints rotated across attempts so repeated hits don't share one JA3.
IMPERSONATE_POOL = ("chrome", "chrome131", "chrome124", "safari18_0", "firefox133")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LPS_", env_file=".env", extra="ignore"
    )

    # fetching
    strategy: str = "auto"  # auto | http | browser
    # NoDecode: without it pydantic-settings tries to JSON-decode the env value
    # before the validator below can accept "a,b" or a file path.
    proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)
    timeout: float = 30.0
    retries: int = 2
    min_delay: float = 0.4
    max_delay: float = 1.6
    locale: str = "en-US"
    accept_language: str = "en-US,en;q=0.9"

    # browser
    #: Set false where no Chrome exists (serverless). The pipeline then stops
    #: at the HTTP path instead of failing on a missing browser.
    browser_enabled: bool = True
    browser_channel: str = "chrome"
    browser_headless: bool = False
    user_data_dir: Path = Path(".patchright-profile")

    # cache
    cache_dir: Path = Path(".cache")
    cache_enabled: bool = True
    cache_ttl: int = 86_400

    # captcha (disabled unless a provider *and* key are configured)
    captcha_provider: str | None = None  # capsolver | 2captcha
    captcha_api_key: str | None = None
    captcha_max_per_run: int = 3
    captcha_timeout: float = 180.0

    # api
    api_max_concurrency: int = 2

    @field_validator("proxies", mode="before")
    @classmethod
    def _parse_proxies(cls, v: object) -> object:
        """Accept a comma-separated list or a path to a newline-delimited file."""
        if not isinstance(v, str):
            return v
        v = v.strip()
        if not v:
            return []
        candidate = Path(v)
        if candidate.exists() and candidate.is_file():
            return [
                line.strip()
                for line in candidate.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
        return [part.strip() for part in v.split(",") if part.strip()]

    @property
    def captcha_enabled(self) -> bool:
        return bool(self.captcha_provider and self.captcha_api_key)


settings = Settings()
