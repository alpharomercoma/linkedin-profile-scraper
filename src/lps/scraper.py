"""Orchestrator: username -> Profile."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Settings, settings as default_settings
from .endpoints import ALL_ENDPOINTS, normalize_username
from .fetch.base import block_reason
from .fetch.challenge import detect_challenge
from .fetch.pipeline import FetchPipeline, PipelineResult
from .models import Profile
from .parse.merge import merge

log = logging.getLogger(__name__)


class ScrapeError(RuntimeError):
    """Every source was walled or errored."""

    def __init__(self, message: str, pipeline: PipelineResult | None = None) -> None:
        super().__init__(message)
        self.pipeline = pipeline

    @property
    def attempts(self) -> list:
        return self.pipeline.attempts if self.pipeline else []


class ProfileNotFound(ScrapeError):
    pass


def scrape_profile(
    username: str,
    settings: Settings | None = None,
    strategy: str | None = None,
    use_cache: bool = True,
) -> Profile:
    settings = settings or default_settings
    username = normalize_username(username)
    if not username:
        raise ValueError("empty username")

    pipeline = FetchPipeline(settings, use_cache=use_cache)
    outcome = pipeline.fetch_profile(username, strategy=strategy)

    if not outcome.ok or outcome.result is None:
        if any(a.blocked == "not found" for a in outcome.attempts):
            raise ProfileNotFound(f"profile {username!r} does not exist", outcome)
        raise ScrapeError(
            f"all sources blocked for {username!r}: {outcome.failure_summary()}", outcome
        )

    result = outcome.result
    profile = merge(result.html)
    profile.username = username
    profile.url = profile.url or f"https://www.linkedin.com/in/{username}"
    profile.meta.source_url = result.final_url
    profile.meta.fetched_via = result.via
    profile.meta.status = result.status
    profile.meta.notes.extend(result.notes)

    if profile.is_empty():
        challenge = detect_challenge(result.html, result.final_url)
        detail = f"; challenge={challenge.kind}" if challenge else ""
        raise ScrapeError(
            f"fetched {result.final_url} ({result.size} bytes) but no profile fields "
            f"could be parsed{detail}",
            outcome,
        )
    return profile


@dataclass
class ProbeRow:
    endpoint: str
    url: str
    via: str
    status: int
    size: int
    blocked: str | None
    challenge: str | None
    title: str
    parsed_name: str | None
    expectation: str
    notes: list[str]


def probe(
    username: str,
    settings: Settings | None = None,
    include_browser: bool = True,
) -> list[ProbeRow]:
    """Measure every mapped endpoint. This is what the endpoint ordering in
    endpoints.py should be based on -- run it before trusting the defaults."""
    from .fetch.base import title_of

    settings = settings or default_settings
    username = normalize_username(username)
    pipeline = FetchPipeline(settings, use_cache=False)
    rows: list[ProbeRow] = []

    for endpoint in ALL_ENDPOINTS:
        url = endpoint.url(username)
        fetchers = [("http", pipeline.http.fetch)]
        if include_browser and endpoint.parseable:
            fetchers.append(("browser", pipeline.browser.fetch))

        for via, fetch in fetchers:
            result = fetch(url)
            parsed_name = None
            if endpoint.parseable and result.html:
                try:
                    parsed_name = merge(result.html).full_name
                except Exception:  # probing must never crash
                    parsed_name = None
            challenge = detect_challenge(result.html, result.final_url)
            rows.append(
                ProbeRow(
                    endpoint=endpoint.name,
                    url=url,
                    via=via,
                    status=result.status,
                    size=result.size,
                    blocked=block_reason(result),
                    challenge=challenge.kind if challenge else None,
                    title=title_of(result.html),
                    parsed_name=parsed_name,
                    expectation=endpoint.expectation,
                    notes=result.notes + ([result.error] if result.error else []),
                )
            )
            if via == "http" and parsed_name:
                break  # http already worked; no need to spend a browser launch
    return rows
