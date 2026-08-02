"""Section-harvest tests against a fixture cut from a real, unredacted
LinkedIn guest page.

These cover the sections the class-name-based parser used to miss entirely:
skills, honors, publications and recommendations were all returning empty
while the page carried them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lps.parse import dom
from lps.parse.merge import merge

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rich_html() -> str:
    return (FIXTURES / "profile_rich.html").read_text()


def test_data_section_harvest_finds_every_section(rich_html: str) -> None:
    p = dom.parse(rich_html)

    assert p.skills[:3] == ["CEOs", "Founding", "Early-Stage Startups"]
    assert p.honors[0].name == "Sigillum Magnum"
    assert p.honors[0].issuer == "University of Bologna"
    assert p.honors[0].date == "Sep 2023"
    assert p.publications[0].name == "Superagency"
    assert p.recommendations[0].author == "Mike Maples, Jr"
    assert p.recommendations[0].text
    assert p.volunteering[0].organization == "Opportunity@Work"


def test_publication_issuer_drops_the_trailing_date(rich_html: str) -> None:
    # The subtitle renders as "Authors Equity January 28, 2025".
    publication = dom.parse(rich_html).publications[0]
    assert publication.issuer == "Authors Equity"
    assert publication.date == "January 28, 2025"


def test_experience_titles_are_titles_not_company_blurbs(rich_html: str) -> None:
    # JSON-LD's member.description is the company's marketing blurb; using it
    # as the job title produced "Manas AI leverages proprietary AI…".
    p = merge(rich_html)
    first = p.experience[0]

    assert first.title == "Co-Founder, Executive Board Chair"
    assert first.company == "Manas AI"
    assert first.company_description.startswith("Manas AI leverages")


def test_layers_complement_rather_than_duplicate(rich_html: str) -> None:
    # The fixture keeps all 13 jobs in JSON-LD but only 2 DOM cards. The union
    # must be 13 -- one entry per job, enriched, with no near-duplicates.
    p = merge(rich_html)

    assert len(p.experience) == 13
    companies = [e.company for e in p.experience]
    assert len(companies) == len(set(companies))
    assert len(p.education) == 6


def test_unknown_sections_are_recorded_not_dropped() -> None:
    html = """
    <section class="core-section-container" data-section="brand-new-thing">
      <h2>Something New</h2>
      <ul><li class="profile-section-card"><h3>An Item</h3></li></ul>
    </section>
    """
    assert dom.parse(html).extra_sections == {"brand-new-thing": ["An Item"]}
