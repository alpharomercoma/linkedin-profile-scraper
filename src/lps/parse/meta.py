"""Open Graph / meta-tag parser.

LinkedIn renders these for link previews, and they survive on profiles where
the body is redacted or nearly empty. `og:description` follows a fixed
template:

    {about snippet} · Experience: {company} · Education: {school} ·
    Location: {city} · {N}+ connections on LinkedIn. View ...

which yields the connection count and current company/school even when the
guest page shows asterisks. `og:title` is "{name} - {headline} | LinkedIn".
"""

from __future__ import annotations

import html as htmllib
import re

from selectolax.parser import HTMLParser

from ..models import Education, Experience, Profile

LAYER = "meta"

_WANTED = {
    "og:title", "og:description", "og:image", "og:url",
    "profile:first_name", "profile:last_name", "description",
}
#: Trailing boilerplate on every og:description.
_BOILERPLATE_RE = re.compile(r"\s*View .+?’s profile on LinkedIn.*$|\s*\d+ connections on LinkedIn.*$")
_CONNECTIONS_RE = re.compile(r"([\d,]+)\s*(\+?)\s*connections?", re.I)
_FOLLOWERS_RE = re.compile(r"([\d,]+)\s*(K|M)?\+?\s*followers?", re.I)
#: og:title for a profile with no headline reads "... | Professional Profile |".
_NO_HEADLINE = ("| professional profile", "| linkedin profile")


def _text(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", htmllib.unescape(value)).strip() or None


def _tags(tree: HTMLParser) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.css("meta"):
        key = node.attributes.get("property") or node.attributes.get("name")
        if key in _WANTED:
            value = _text(node.attributes.get("content"))
            if value:
                out[key] = value
    return out


def _segments(description: str) -> tuple[str | None, dict[str, str]]:
    """Split "{about} · Experience: X · Education: Y · Location: Z" apart."""
    fields: dict[str, str] = {}
    lead: str | None = None
    for chunk in description.split("·"):
        chunk = chunk.strip()
        if not chunk:
            continue
        label, sep, value = chunk.partition(":")
        if sep and label.strip().lower() in ("experience", "education", "location"):
            fields[label.strip().lower()] = value.strip()
        elif lead is None and "connections on linkedin" not in chunk.lower():
            lead = chunk
    return lead, fields


def parse(html: str) -> Profile:
    profile = Profile()
    tags = _tags(HTMLParser(html))
    if not tags:
        return profile

    profile.first_name = tags.get("profile:first_name")
    profile.last_name = tags.get("profile:last_name")
    profile.avatar_url = tags.get("og:image")
    profile.url = tags.get("og:url")

    title = tags.get("og:title") or ""
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title)
    name, sep, rest = title.partition(" - ")
    if sep:
        profile.full_name = name.strip() or None
        if not any(marker in rest.lower() for marker in _NO_HEADLINE):
            profile.headline = rest.strip() or None
    elif title:
        profile.full_name = title.strip()

    if not profile.full_name and (profile.first_name or profile.last_name):
        profile.full_name = " ".join(x for x in (profile.first_name, profile.last_name) if x)

    description = tags.get("og:description") or tags.get("description") or ""
    if description:
        lead, fields = _segments(description)
        # The lead is the About text, truncated by LinkedIn with an ellipsis.
        if lead and not lead.lower().startswith(("location", "experience", "education")):
            profile.about = lead
        if fields.get("experience"):
            profile.experience.append(Experience(company=fields["experience"]))
            profile.current_company = fields["experience"]
        if fields.get("education"):
            profile.education.append(Education(school=fields["education"]))
        if fields.get("location"):
            profile.location = fields["location"]

        connections = _CONNECTIONS_RE.search(description)
        if connections:
            number, plus = connections.group(1), connections.group(2)
            profile.connection_count_text = f"{number}{plus}"
            # "500+" is LinkedIn's display cap, i.e. a lower bound. Reporting it
            # as the exact count would be a lie, so only set the int without it.
            if not plus:
                try:
                    profile.connection_count = int(number.replace(",", ""))
                except ValueError:
                    pass
        followers = _FOLLOWERS_RE.search(description)
        if followers:
            value = float(followers.group(1).replace(",", ""))
            unit = (followers.group(2) or "").upper()
            profile.follower_count = int(value * {"K": 1_000, "M": 1_000_000}.get(unit, 1))

    return profile
