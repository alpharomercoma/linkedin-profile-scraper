"""Redaction and Open Graph tests, against a fixture cut from a real
LinkedIn page that LinkedIn serves asterisk-redacted to logged-out visitors."""

from __future__ import annotations

from pathlib import Path

import pytest

from lps.models import Entry, Experience, Profile
from lps.parse import meta
from lps.parse.merge import merge
from lps.parse.redact import is_masked, scrub

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def redacted_html() -> str:
    return (FIXTURES / "profile_redacted.html").read_text()


@pytest.mark.parametrize(
    "value,masked",
    [
        ("***** *************", True),
        ("*******", True),
        ("**", False),
        ("Trinity Ventures", False),
        ("Ernst & Young*", False),  # a real value that merely contains an asterisk
        ("", False),
        (None, False),
    ],
)
def test_is_masked(value, masked) -> None:
    assert is_masked(value) is masked


def test_scrub_drops_fully_masked_entries_and_counts_them() -> None:
    profile = Profile(
        full_name="Gus Tai",
        current_title="***** ******",
        experience=[
            Experience(title="Partner", company="Trinity Ventures"),
            Experience(title="***** ******", company="**********"),
            Experience(title="***** ******", company="*********"),
        ],
        skills=["Venture Capital", "*********"],
    )
    counts = scrub(profile)

    assert [e.company for e in profile.experience] == ["Trinity Ventures"]
    assert profile.current_title is None
    assert profile.skills == ["Venture Capital"]
    assert counts == {"current_title": 1, "experience": 2, "skills": 1}


def test_scrub_keeps_partially_masked_entries_but_nulls_the_masked_field() -> None:
    profile = Profile(experience=[Experience(title="***** ******", company="Trinity Ventures")])
    counts = scrub(profile)

    assert len(profile.experience) == 1
    assert profile.experience[0].company == "Trinity Ventures"
    assert profile.experience[0].title is None  # masked value never surfaces as data
    assert "experience" not in counts


def test_real_redacted_page_reports_what_was_withheld(redacted_html: str) -> None:
    p = merge(redacted_html)

    assert p.full_name == "Gus Tai"
    # The one entry LinkedIn leaves unredacted survives; the rest are counted.
    assert [e.company for e in p.experience] == ["Trinity Ventures"]
    assert p.meta.redacted is True
    assert p.meta.redacted_counts.get("experience", 0) >= 1
    assert any("redacted part of this profile" in w for w in p.meta.warnings)
    # No asterisk placeholder may ever reach the output.
    assert "***" not in p.model_dump_json()


def test_open_graph_recovers_fields_the_body_redacts(redacted_html: str) -> None:
    p = meta.parse(redacted_html)

    assert (p.first_name, p.last_name) == ("Gus", "Tai")
    assert p.headline == "Trinity Ventures"
    assert p.education[0].school == "Harvard University"
    assert p.location == "Menlo Park"
    # "500+" is a lower bound LinkedIn displays, not a count.
    assert p.connection_count_text == "500+"
    assert p.connection_count is None
    assert p.avatar_url.startswith("https://media.licdn.com/")


def test_open_graph_ignores_the_headline_slot_when_it_holds_a_location() -> None:
    # Profiles with no headline render "Name - City, Country | Professional
    # Profile | LinkedIn"; that location must not become the headline.
    html = """<html><head>
      <meta property="og:title" content="Anderee Berengian - Los Angeles, California,
            United States | Professional Profile | LinkedIn">
      <meta property="og:description" content="Location: Los Angeles &middot; 500+ connections
            on LinkedIn. View Anderee Berengian&#39;s profile on LinkedIn.">
    </head><body></body></html>"""
    p = meta.parse(html)

    assert p.full_name == "Anderee Berengian"
    assert p.headline is None
    assert p.connection_count_text == "500+"


def test_entry_sections_survive_merge() -> None:
    # Generic Entry sections (honors, publications, ...) dedupe like the rest.
    profile = Profile(honors=[Entry(name="Marshall Medal", issuer="UK Marshall Commission")])
    assert scrub(profile) == {}
    assert profile.honors[0].name == "Marshall Medal"
