"""Pipeline / config tests. Fetchers are stubbed, so nothing touches the network."""

from __future__ import annotations

from pathlib import Path

import pytest

from lps.config import Settings
from lps.endpoints import normalize_username
from lps.fetch.base import FetchResult
from lps.fetch.cache import RawCache
from lps.fetch.pipeline import FetchPipeline
from lps.fetch.http_fetcher import HttpFetcher
from lps.fetch.proxies import NoProxyAvailable, ProxyPool, as_playwright_proxy
from lps.scraper import ScrapeError, scrape_profile


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(cache_dir=tmp_path / "cache", proxies=[], strategy="http")


def _stub(html: str, status: int = 200):
    def fetch(url: str, *a, **kw) -> FetchResult:
        return FetchResult(url=url, final_url=url, status=status, html=html, via="http")

    return fetch


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("adalovelace", "adalovelace"),
        ("https://www.linkedin.com/in/adalovelace/", "adalovelace"),
        ("https://uk.linkedin.com/in/adalovelace?trk=x", "adalovelace"),
        ("  /in/adalovelace  ", "adalovelace"),
    ],
)
def test_normalize_username(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


def test_scrape_uses_first_usable_endpoint(settings: Settings, public_html: str, monkeypatch) -> None:
    monkeypatch.setattr("lps.fetch.http_fetcher.HttpFetcher.fetch", staticmethod(_stub(public_html)))
    profile = scrape_profile("adalovelace", settings, strategy="http")
    assert profile.full_name == "Ada Lovelace"
    assert profile.username == "adalovelace"
    assert profile.meta.fetched_via == "http"


def test_scrape_raises_with_attempt_detail_when_walled(
    settings: Settings, authwall_html: str, monkeypatch
) -> None:
    monkeypatch.setattr(
        "lps.fetch.http_fetcher.HttpFetcher.fetch", staticmethod(_stub(authwall_html))
    )
    with pytest.raises(ScrapeError) as exc:
        scrape_profile("adalovelace", settings, strategy="http")
    assert exc.value.attempts, "the error must carry which walls were hit"
    assert all(a.blocked for a in exc.value.attempts)


def test_cache_round_trip(settings: Settings, public_html: str, monkeypatch) -> None:
    calls = {"n": 0}
    inner = _stub(public_html)

    def counting(url: str, *a, **kw) -> FetchResult:
        calls["n"] += 1
        return inner(url)

    monkeypatch.setattr("lps.fetch.http_fetcher.HttpFetcher.fetch", staticmethod(counting))
    pipeline = FetchPipeline(settings)
    assert pipeline.fetch_profile("adalovelace", "http").ok
    assert calls["n"] == 1

    second = FetchPipeline(settings).fetch_profile("adalovelace", "http")
    assert second.result.via == "cache"
    assert calls["n"] == 1  # served from disk, no second request


def test_cache_respects_ttl(tmp_path: Path, public_html: str) -> None:
    cache = RawCache(tmp_path, ttl=0)
    url = "https://www.linkedin.com/in/adalovelace"
    cache.put("ada", FetchResult(url=url, final_url=url, status=200, html=public_html, via="http"))
    assert cache.get("ada", url) is not None  # ttl=0 disables expiry

    expiring = RawCache(tmp_path, ttl=-1)
    assert expiring.get("ada", url) is None


def test_proxy_pool_quarantines_after_failures() -> None:
    pool = ProxyPool(["http://a:1", "http://b:2"], max_failures=2)
    assert pool.size == 2
    for _ in range(2):
        pool.report_failure("http://a:1")
    assert pool.available == ["http://b:2"]
    assert pool.next() == "http://b:2"
    pool.report_success("http://a:1")
    assert pool.size == 2


def test_empty_pool_goes_direct() -> None:
    assert ProxyPool([]).next() is None


def test_exhausted_pool_refuses_to_leak_the_direct_ip() -> None:
    pool = ProxyPool(["http://dead:1"], max_failures=1)
    pool.report_failure("http://dead:1")
    with pytest.raises(NoProxyAvailable):
        pool.next()


def test_fetcher_reports_exhausted_pool_as_a_transport_error() -> None:
    settings = Settings(proxies=["http://dead:1"])
    fetcher = HttpFetcher(settings, ProxyPool(["http://dead:1"], max_failures=1))
    fetcher.pool.report_failure("http://dead:1")
    result = fetcher.fetch("https://www.linkedin.com/in/adalovelace")
    assert result.status == 0 and "quarantined" in result.error


def test_playwright_proxy_splits_credentials() -> None:
    assert as_playwright_proxy("http://user:pw@host:8080") == {
        "server": "http://host:8080",
        "username": "user",
        "password": "pw",
    }
    assert as_playwright_proxy(None) is None


def test_settings_parse_proxies_from_csv_and_file(tmp_path: Path) -> None:
    assert Settings(proxies="http://a:1, http://b:2").proxies == ["http://a:1", "http://b:2"]

    path = tmp_path / "proxies.txt"
    path.write_text("# comment\nhttp://c:3\n\nhttp://d:4\n")
    assert Settings(proxies=str(path)).proxies == ["http://c:3", "http://d:4"]
