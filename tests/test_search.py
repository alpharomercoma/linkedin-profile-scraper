"""Name resolution tests, against a fixture captured from LinkedIn's public
people directory (which answers without a session)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lps.config import Settings
from lps.fetch.base import FetchResult
from lps.scraper import ProfileNotFound, scrape_query
from lps.search import looks_like_slug, parse_directory, split_name

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def directory_html() -> str:
    return (FIXTURES / "directory_search.html").read_text()


@pytest.mark.parametrize(
    "query,is_slug",
    [
        ("williamhgates", True),
        ("alpha-romer-coma-470875390", True),
        ("https://www.linkedin.com/in/williamhgates", True),
        ("/in/williamhgates", True),
        ("Alpha Romer Coma", False),
        ("Bill Gates", False),
    ],
)
def test_looks_like_slug(query: str, is_slug: bool) -> None:
    assert looks_like_slug(query) is is_slug


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Alpha Romer Coma", ("Alpha", "Coma")),  # directory takes two fields only
        ("Bill Gates", ("Bill", "Gates")),
        ("Cher", ("Cher", "")),
        ("  Ada   Lovelace  ", ("Ada", "Lovelace")),
    ],
)
def test_split_name(name: str, expected: tuple[str, str]) -> None:
    assert split_name(name) == expected


def test_parse_directory_extracts_candidates(directory_html: str) -> None:
    hits = parse_directory(directory_html)

    assert [h.username for h in hits] == ["alpharomercoma", "alpha-romer-coma-470875390"]
    assert hits[0].name == "Alpha Romer Coma"
    assert hits[0].location == "San Francisco Bay Area"
    assert hits[0].url == "https://www.linkedin.com/in/alpharomercoma"
    # Two different people share this name, which is exactly why the caller
    # gets every candidate rather than just the pick.
    assert hits[1].location == "San Jose del Monte"


def test_scrape_query_resolves_a_name_and_reports_alternatives(
    directory_html: str, public_html: str, monkeypatch, tmp_path
) -> None:
    settings = Settings(cache_dir=tmp_path, strategy="http")

    def fake_fetch(url: str, *a, **kw) -> FetchResult:
        html = directory_html if "/pub/dir" in url else public_html
        return FetchResult(url=url, final_url=url, status=200, html=html, via="http")

    monkeypatch.setattr("lps.fetch.http_fetcher.HttpFetcher.fetch", staticmethod(fake_fetch))

    profile = scrape_query("Alpha Romer Coma", settings, strategy="http")

    assert profile.username == "alpharomercoma"  # top-ranked match
    assert profile.meta.resolved_from == "name-search"
    assert profile.meta.query == "Alpha Romer Coma"
    assert [c.username for c in profile.meta.candidates] == [
        "alpharomercoma",
        "alpha-romer-coma-470875390",
    ]


def test_scrape_query_skips_the_directory_for_a_slug(
    public_html: str, monkeypatch, tmp_path
) -> None:
    settings = Settings(cache_dir=tmp_path, strategy="http")
    seen: list[str] = []

    def fake_fetch(url: str, *a, **kw) -> FetchResult:
        seen.append(url)
        return FetchResult(url=url, final_url=url, status=200, html=public_html, via="http")

    monkeypatch.setattr("lps.fetch.http_fetcher.HttpFetcher.fetch", staticmethod(fake_fetch))

    profile = scrape_query("adalovelace", settings, strategy="http")

    assert profile.meta.resolved_from == "slug"
    assert not any("/pub/dir" in url for url in seen)


def test_unresolvable_name_is_a_404_not_a_wrong_person(monkeypatch, tmp_path) -> None:
    settings = Settings(cache_dir=tmp_path, strategy="http")

    def empty(url: str, *a, **kw) -> FetchResult:
        return FetchResult(
            url=url, final_url=url, status=200, html="<html>" + "x" * 9000 + "</html>", via="http"
        )

    monkeypatch.setattr("lps.fetch.http_fetcher.HttpFetcher.fetch", staticmethod(empty))

    with pytest.raises(ProfileNotFound):
        scrape_query("Nobody Whatsoever", settings, strategy="http")
