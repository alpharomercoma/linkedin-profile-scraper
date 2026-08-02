"""Embedded Voyager-model parser.

LinkedIn ships hydration payloads inside `<code id="bpr-guid-*">` blocks (and,
on some renders, a single `<script type="application/json">` bootstrap). When
present these carry far more than JSON-LD -- skills, certifications, languages,
full position descriptions. Presence varies by render, so this layer is
best-effort by design and never raises.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from selectolax.parser import HTMLParser

from ..models import (
    Certification,
    DateRange,
    Education,
    Experience,
    Language,
    Profile,
    Volunteering,
)

LAYER = "embedded"

_MONTHS = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _iter_payloads(html: str) -> Iterator[Any]:
    tree = HTMLParser(html)
    selectors = ('code[id^="bpr-guid-"]', 'script[type="application/json"]', "code")
    # Selectors overlap (a bpr-guid <code> also matches "code"), and each css()
    # call returns fresh Node objects, so dedupe on the payload text itself.
    seen: set[str] = set()
    for selector in selectors:
        for node in tree.css(selector):
            raw = (node.text() or "").strip()
            if not raw.startswith(("{", "[")) or raw in seen:
                continue
            seen.add(raw)
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def _included(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        items = payload.get("included")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    return []


def _typename(entity: dict) -> str:
    return str(entity.get("$type") or entity.get("_type") or "")


def _has(entity: dict, needle: str) -> bool:
    return needle.lower() in _typename(entity).lower()


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("text", "name", "localizedName", "title"):
            found = _text(value.get(key))
            if found:
                return found
    return None


def _date(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    year, month = value.get("year"), value.get("month")
    if year and month:
        return f"{_MONTHS.get(int(month), month)} {year}"
    return str(year) if year else None


def _date_range(value: Any) -> DateRange | None:
    if not isinstance(value, dict):
        return None
    start = _date(value.get("startDate") or value.get("start"))
    end = _date(value.get("endDate") or value.get("end"))
    if not (start or end):
        return None
    raw = " - ".join(x for x in (start, end or "Present") if x)
    return DateRange(start=start, end=end, raw=raw)


def _image_url(value: Any) -> str | None:
    """Voyager images are {rootUrl, artifacts:[{fileIdentifyingUrlPathSegment}]}."""
    if isinstance(value, str):
        return value if value.startswith("http") else None
    if not isinstance(value, dict):
        return None
    for key in ("displayImageReference", "vectorImage", "image", "rootUrl"):
        nested = value.get(key)
        if key == "rootUrl" and isinstance(nested, str):
            artifacts = value.get("artifacts") or []
            if artifacts and isinstance(artifacts[0], dict):
                return nested + str(artifacts[0].get("fileIdentifyingUrlPathSegment", ""))
            return nested
        if isinstance(nested, dict):
            found = _image_url(nested)
            if found:
                return found
    return None


def parse(html: str) -> Profile:
    profile = Profile()
    entities: list[dict] = []
    for payload in _iter_payloads(html):
        entities.extend(_included(payload))
    if not entities:
        return profile

    for entity in entities:
        if _has(entity, "identity.profile.Profile") or _has(entity, "dash.identity.profile.Profile"):
            profile.first_name = profile.first_name or _text(entity.get("firstName"))
            profile.last_name = profile.last_name or _text(entity.get("lastName"))
            profile.headline = profile.headline or _text(entity.get("headline"))
            profile.about = profile.about or _text(entity.get("summary"))
            profile.location = profile.location or _text(
                entity.get("geoLocationName")
                or entity.get("locationName")
                or entity.get("geoLocation")
            )
            profile.country = profile.country or _text(entity.get("geoCountryName"))
            profile.avatar_url = profile.avatar_url or _image_url(entity.get("profilePicture"))
            profile.banner_url = profile.banner_url or _image_url(entity.get("backgroundImage"))
            if isinstance(entity.get("followingState"), dict):
                count = entity["followingState"].get("followerCount")
                if isinstance(count, int):
                    profile.follower_count = count

        elif _has(entity, "Position"):
            profile.experience.append(
                Experience(
                    title=_text(entity.get("title")),
                    company=_text(entity.get("companyName") or entity.get("company")),
                    company_url=entity.get("companyUrn") if isinstance(entity.get("companyUrn"), str) else None,
                    company_logo=_image_url(entity.get("companyLogo")),
                    location=_text(entity.get("locationName")),
                    employment_type=_text(entity.get("employmentTypeUrn")),
                    description=_text(entity.get("description")),
                    dates=_date_range(entity.get("dateRange") or entity.get("timePeriod")),
                )
            )

        elif _has(entity, "Education"):
            profile.education.append(
                Education(
                    school=_text(entity.get("schoolName") or entity.get("school")),
                    school_logo=_image_url(entity.get("logo") or entity.get("schoolLogo")),
                    degree=_text(entity.get("degreeName")),
                    field_of_study=_text(entity.get("fieldOfStudy")),
                    description=_text(entity.get("description")),
                    dates=_date_range(entity.get("dateRange") or entity.get("timePeriod")),
                )
            )

        elif _has(entity, "Skill"):
            name = _text(entity.get("name"))
            if name:
                profile.skills.append(name)

        elif _has(entity, "Certification"):
            profile.certifications.append(
                Certification(
                    name=_text(entity.get("name")),
                    issuer=_text(entity.get("authority") or entity.get("companyName")),
                    issued=_date(
                        (entity.get("dateRange") or entity.get("timePeriod") or {}).get("startDate")
                    ),
                    credential_url=entity.get("url") if isinstance(entity.get("url"), str) else None,
                )
            )

        elif _has(entity, "Language"):
            name = _text(entity.get("name"))
            if name:
                profile.languages.append(
                    Language(name=name, proficiency=_text(entity.get("proficiency")))
                )

        elif _has(entity, "VolunteerExperience"):
            profile.volunteering.append(
                Volunteering(
                    role=_text(entity.get("role")),
                    organization=_text(entity.get("companyName")),
                    cause=_text(entity.get("cause")),
                    description=_text(entity.get("description")),
                    dates=_date_range(entity.get("dateRange") or entity.get("timePeriod")),
                )
            )

    if profile.first_name or profile.last_name:
        profile.full_name = " ".join(x for x in (profile.first_name, profile.last_name) if x)
    if profile.experience:
        current = profile.experience[0]
        profile.current_title = profile.current_title or current.title
        profile.current_company = profile.current_company or current.company

    return profile
