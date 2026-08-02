"""Fetch escalation: cache -> HTTP (each mapped endpoint) -> browser."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Settings
from ..endpoints import PROFILE_ENDPOINTS
from .base import FetchResult, block_reason
from .browser_fetcher import BrowserFetcher
from .cache import RawCache
from .challenge import get_solver
from .http_fetcher import HttpFetcher
from .proxies import ProxyPool

log = logging.getLogger(__name__)


@dataclass
class Attempt:
    endpoint: str
    url: str
    via: str
    status: int
    size: int
    blocked: str | None
    notes: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    result: FetchResult | None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None

    def failure_summary(self) -> str:
        if not self.attempts:
            return "no attempts made"
        return "; ".join(f"{a.endpoint}[{a.via}] {a.status}: {a.blocked}" for a in self.attempts)


class FetchPipeline:
    def __init__(self, settings: Settings, use_cache: bool = True) -> None:
        self.settings = settings
        self.pool = ProxyPool(settings.proxies)
        self.cache = RawCache(settings.cache_dir, settings.cache_ttl)
        self.use_cache = use_cache and settings.cache_enabled
        self.http = HttpFetcher(settings, self.pool)
        self.browser = BrowserFetcher(settings, self.pool, get_solver(settings))

    def fetch_profile(self, username: str, strategy: str | None = None) -> PipelineResult:
        strategy = strategy or self.settings.strategy
        attempts: list[Attempt] = []

        for endpoint in PROFILE_ENDPOINTS:
            url = endpoint.url(username)

            if self.use_cache:
                cached = self.cache.get(username, url)
                if cached and not block_reason(cached):
                    log.info("cache hit for %s", url)
                    return PipelineResult(cached, attempts)

            if strategy in ("auto", "http"):
                result = self.http.fetch(url)
                reason = block_reason(result)
                attempts.append(
                    Attempt(endpoint.name, url, "http", result.status, result.size, reason, result.notes)
                )
                if reason is None:
                    self._store(username, result)
                    return PipelineResult(result, attempts)
                log.info("http %s blocked: %s", endpoint.name, reason)
                if reason == "not found":
                    return PipelineResult(None, attempts)

        if strategy in ("auto", "browser") and not self.settings.browser_enabled:
            log.info("browser path disabled (LPS_BROWSER_ENABLED=false)")
            attempts.append(
                Attempt("browser", "", "browser", 0, 0, "browser path disabled in this environment")
            )
        elif strategy in ("auto", "browser"):
            for endpoint in PROFILE_ENDPOINTS[:2]:
                url = endpoint.url(username)
                result = self.browser.fetch(url)
                reason = block_reason(result)
                attempts.append(
                    Attempt(endpoint.name, url, "browser", result.status, result.size, reason, result.notes)
                )
                if reason is None:
                    self._store(username, result)
                    return PipelineResult(result, attempts)
                log.info("browser %s blocked: %s", endpoint.name, reason)
                if reason == "not found":
                    return PipelineResult(None, attempts)

        return PipelineResult(None, attempts)

    def _store(self, username: str, result: FetchResult) -> None:
        if result.via != "cache" and self.settings.cache_enabled:
            self.cache.put(username, result)
