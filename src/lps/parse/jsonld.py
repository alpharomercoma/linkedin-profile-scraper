"""JSON-LD parser -- highest priority.

LinkedIn's public profile page embeds a schema.org Person (usually inside an
@graph alongside a WebPage/Organization). This is the most stable surface on
the page: it survives markup churn that breaks CSS selectors.
"""

from __future__ import annotations

import json

from selectolax.parser import HTMLParser

from ..models import DateRange, Education, Entry, Experience, Language, Profile

LAYER = "jsonld"


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _date(value) -> str | None:
    """Dates arrive as "2019-04", 1833 (int), or a nested dict."""
    if isinstance(value, (int, float)):
        return str(int(value))
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _date(value.get("startDate") or value.get("@value"))
    return None


def _range(raw_start, raw_end) -> DateRange | None:
    start, end = _date(raw_start), _date(raw_end)
    if not (start or end):
        return None
    # `raw` is reserved for the text the page actually displays, which only the
    # DOM layer sees; leaving it empty lets the merge fill it from there.
    return DateRange(start=start, end=end)


def _image(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("contentUrl") or value.get("url")
    if isinstance(value, list) and value:
        return _image(value[0])
    return None


def _flatten(doc) -> list[dict]:
    """Yield every node, following @graph."""
    out: list[dict] = []
    for item in _as_list(doc):
        if not isinstance(item, dict):
            continue
        if "@graph" in item:
            out.extend(n for n in _as_list(item["@graph"]) if isinstance(n, dict))
        else:
            out.append(item)
    return out


def _types(node: dict) -> set[str]:
    return {str(t).lower() for t in _as_list(node.get("@type"))}


def parse(html: str) -> Profile:
    profile = Profile()
    tree = HTMLParser(html)

    nodes: list[dict] = []
    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text(strip=True)
        if not raw:
            continue
        try:
            nodes.extend(_flatten(json.loads(raw)))
        except json.JSONDecodeError:
            continue

    person = next((n for n in nodes if "person" in _types(n)), None)
    if person is None:
        return profile

    profile.full_name = _text(person.get("name"))
    profile.first_name = _text(person.get("givenName"))
    profile.last_name = _text(person.get("familyName"))
    profile.about = _text(person.get("description"))
    profile.avatar_url = _image(person.get("image"))
    profile.url = person.get("url") if isinstance(person.get("url"), str) else None

    # jobTitle is a list of position titles, not the headline -- joining them
    # produces "Co-chair, Founder, Co-founder", which is not what the page
    # shows. Let the DOM layer supply the real headline.
    job_titles = [t for t in (_text(j) for j in _as_list(person.get("jobTitle"))) if t]
    if job_titles:
        profile.current_title = job_titles[0]

    address = person.get("address")
    if isinstance(address, dict):
        # addressLocality already reads "Seattle, Washington, United States";
        # addressCountry is an ISO code and belongs in `country`, not appended.
        parts = [address.get("addressLocality"), address.get("addressRegion")]
        joined = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
        country = address.get("addressCountry")
        country = country if isinstance(country, str) else _text(country)
        profile.location = joined or country or None
        profile.country = country
    elif isinstance(address, str):
        profile.location = address.strip() or None

    works_for = [e for e in _as_list(person.get("worksFor")) if isinstance(e, dict)]
    # LinkedIn emits jobTitle positionally aligned with worksFor. Use that
    # alignment only when the lengths agree; guessing would mislabel jobs.
    aligned = job_titles if len(job_titles) == len(works_for) else []

    for index, entry in enumerate(works_for):
        name = _text(entry.get("name"))
        if not name:
            continue
        member = entry.get("member") if isinstance(entry.get("member"), dict) else {}
        dates = _range(member.get("startDate"), member.get("endDate"))
        # member.description is the *company's* blurb, not the job title --
        # using it as a title yields "Manas AI leverages proprietary AI…".
        profile.experience.append(
            Experience(
                title=aligned[index] if aligned else None,
                company_description=_text(member.get("description")),
                company=name,
                company_url=entry.get("url") if isinstance(entry.get("url"), str) else None,
                company_logo=_image(entry.get("logo")),
                location=_text(entry.get("location")),
                dates=dates,
            )
        )
    if profile.experience and not profile.current_company:
        profile.current_company = profile.experience[0].company

    for entry in _as_list(person.get("alumniOf")):
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        if not name:
            continue
        member = entry.get("member") if isinstance(entry.get("member"), dict) else {}
        dates = _range(member.get("startDate"), member.get("endDate"))
        profile.education.append(
            Education(
                school=name,
                school_url=entry.get("url") if isinstance(entry.get("url"), str) else None,
                school_logo=_image(entry.get("logo")),
                # As with worksFor, member.description is the *school's* blurb.
                degree=_text(entry.get("degree")),
                school_description=_text(member.get("description")),
                dates=dates,
            )
        )

    profile.websites = [u for u in _as_list(person.get("sameAs")) if isinstance(u, str)]
    profile.creator_badge = _text(person.get("disambiguatingDescription"))

    # Fields LinkedIn publishes in JSON-LD that have no guest-page section of
    # their own on many profiles -- honors, languages, memberships.
    for award in _as_list(person.get("awards")):
        name = _text(award)
        if name:
            profile.honors.append(Entry(name=name))

    for language in _as_list(person.get("knowsLanguage")):
        name = _text(language)
        if name:
            profile.languages.append(Language(name=name))

    for org in _as_list(person.get("memberOf")):
        name = _text(org)
        if name:
            profile.organizations.append(
                Entry(name=name, url=org.get("url") if isinstance(org, dict) else None)
            )

    interaction = person.get("interactionStatistic")
    for stat in _as_list(interaction):
        if isinstance(stat, dict) and "follow" in str(stat.get("interactionType", "")).lower():
            count = stat.get("userInteractionCount")
            if isinstance(count, (int, float)):
                profile.follower_count = int(count)

    return profile
