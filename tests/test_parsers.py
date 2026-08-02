"""Parser tests run entirely off committed fixtures -- no network -- so they
stay green even when LinkedIn is walling every request."""

from __future__ import annotations

from lps.parse import dom, embedded, jsonld
from lps.parse.merge import merge


def test_jsonld_extracts_person(public_html: str) -> None:
    p = jsonld.parse(public_html)
    assert p.full_name == "Ada Lovelace"
    assert p.first_name == "Ada"
    # jobTitle is a list of position titles, not the headline -- it must not be
    # joined into one, and the country code must not be glued onto the location.
    assert p.headline is None
    assert p.current_title == "Principal Engineer"
    assert p.location == "London, England"
    assert p.country == "United Kingdom"
    assert p.avatar_url.endswith("ada-avatar.jpg")
    assert p.follower_count == 18400
    assert p.experience[0].company == "Analytical Engines Ltd"
    assert p.education[0].school == "University of London"
    assert p.websites == ["https://example.com/ada"]


def test_embedded_extracts_voyager_models(public_html: str) -> None:
    p = embedded.parse(public_html)
    assert p.full_name == "Ada Lovelace"
    assert p.headline == "Principal Engineer at Analytical Engines Ltd"
    assert p.avatar_url == "https://media.licdn.com/dms/image/ada-400.jpg"
    assert [e.title for e in p.experience] == ["Principal Engineer", "Mathematician"]
    assert p.experience[0].dates.start == "Apr 2019"
    assert p.experience[1].dates.raw == "Jan 1842 - Dec 1843"
    assert p.education[0].field_of_study == "Analysis"
    assert p.skills == ["Algorithms", "Punch Cards"]
    assert p.certifications[0].issuer == "Royal Society"
    assert p.languages[0].name == "English"
    assert p.volunteering[0].organization == "Difference Engine Club"


def test_dom_extracts_sections(public_html: str) -> None:
    p = dom.parse(public_html)
    assert p.full_name == "Ada Lovelace"
    assert p.headline == "Principal Engineer at Analytical Engines Ltd"
    assert p.location == "London, England, United Kingdom"
    assert p.about == "Working on analytical engines and the first algorithms."
    assert p.follower_count == 18000
    assert len(p.experience) == 2
    assert p.experience[0].description == "Designing the note-G algorithm pipeline."
    assert p.education[0].degree == "Mathematics"
    assert p.education[0].field_of_study == "Analysis"
    assert p.experience[0].dates.end == "Present"
    assert "Technical Writing" in p.skills
    assert [lang.name for lang in p.languages] == ["English", "French"]
    assert p.also_viewed == ["charlesbabbage", "maryboole"]


def test_dom_splits_duration_from_date_range() -> None:
    # Real markup glues the duration onto the range in one node.
    html = """
    <section class="experience"><ul class="experience__list">
      <li class="experience-item">
        <h3 class="experience-item__title">Co-chair</h3>
        <h4 class="experience-item__subtitle">Gates Foundation</h4>
        <span class="date-range">2000 - Present 26 years</span>
      </li>
    </ul></section>
    """
    dates = dom.parse(html).experience[0].dates
    assert (dates.start, dates.end, dates.duration) == ("2000", "Present", "26 years")
    assert dates.raw == "2000 - Present 26 years"


def test_dom_ignores_duration_text_in_the_location_slot() -> None:
    # The generic meta-item sibling holds the duration; it must not become the
    # job location, and "41M followers" must not become the profile location.
    html = """
    <section class="top-card-layout">
      <h1 class="top-card-layout__title">Bill Gates</h1>
      <div class="top-card-layout__first-subline">
        <span class="top-card__subline-item">41M followers</span>
        <span class="top-card__subline-item">Seattle, Washington, United States</span>
      </div>
    </section>
    <section class="experience"><ul class="experience__list">
      <li class="experience-item">
        <h3 class="experience-item__title">Co-chair</h3>
        <span class="experience-item__meta-item">2000 - Present 26 years</span>
      </li>
    </ul></section>
    """
    p = dom.parse(html)
    assert p.location == "Seattle, Washington, United States"
    assert p.follower_count == 41_000_000
    assert p.experience[0].location is None


def test_merge_prefers_jsonld_and_unions_lists(public_html: str) -> None:
    p = merge(public_html)

    # jsonld wins the scalars it supplies...
    assert p.meta.field_sources["full_name"] == "jsonld"
    assert p.meta.field_sources["headline"] == "embedded"  # jsonld has no headline
    assert p.headline == "Principal Engineer at Analytical Engines Ltd"
    # ...and lower layers fill what it lacks.
    assert p.meta.field_sources["about"] == "jsonld"
    assert p.about == "Working on analytical engines and the first algorithms."
    # "500+" is a display cap, so it survives as text but never as an int.
    assert p.connection_count_text == "500+ connections"
    assert p.connection_count is None

    # Lists union across layers and dedupe on key().
    titles = [e.title for e in p.experience]
    assert titles.count("Principal Engineer") == 1
    assert "Mathematician" in titles
    # One education entry, enriched across layers: jsonld knew the school and
    # URL, embedded knew the degree, dom knew the field of study.
    assert len(p.education) == 1
    edu = p.education[0]
    assert (edu.school, edu.degree, edu.field_of_study) == (
        "University of London",
        "Mathematics",
        "Analysis",
    )
    assert edu.school_url.endswith("university-of-london")
    # Same job from three layers stays one entry but keeps the richest fields.
    assert p.experience[0].description == "Designing the note-G algorithm pipeline."
    assert p.experience[0].location == "London, United Kingdom"
    assert set(p.skills) == {"Algorithms", "Punch Cards", "Technical Writing"}
    assert {lang.name for lang in p.languages} == {"English", "French"}
    assert not p.is_empty()
    assert not p.meta.warnings


def test_authwall_does_not_masquerade_as_a_profile(authwall_html: str) -> None:
    # The wall's own <h1> is "Join LinkedIn" -- it must not become a full_name.
    for module in (jsonld, embedded, dom):
        assert module.parse(authwall_html).is_empty()
    assert merge(authwall_html).is_empty()


def test_parsers_return_empty_on_garbage() -> None:
    for module in (jsonld, embedded, dom):
        assert module.parse("<html><body>nothing here</body></html>").is_empty()


def test_merge_survives_a_broken_layer(public_html: str, monkeypatch) -> None:
    def boom(_: str):
        raise ValueError("selector exploded")

    monkeypatch.setattr(dom, "parse", boom)
    p = merge(public_html)
    assert p.full_name == "Ada Lovelace"  # other layers still delivered
    assert any("parser dom failed" in w for w in p.meta.warnings)
