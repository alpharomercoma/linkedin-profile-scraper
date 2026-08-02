"""Normalized profile schema. Every field is optional: partial extraction is a
normal outcome for unauthenticated scraping, not an error."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class Keyed(Base):
    """An entry that can be deduped across parser layers.

    The key deliberately excludes dates: each layer formats them differently
    ("2019-04" vs "Apr 2019 - Present"), so including them would let the same
    job through twice.
    """

    KEY_FIELDS: ClassVar[tuple[str, ...]] = ()

    def key(self) -> tuple[str, ...]:
        return tuple((getattr(self, f) or "").strip().lower() for f in self.KEY_FIELDS)


class DateRange(Base):
    start: str | None = None
    end: str | None = None
    duration: str | None = None
    raw: str | None = None


class Entry(Keyed):
    """Generic profile section entry: honor, publication, course, project,
    patent, organization, test score. They all render identically (title,
    subtitle, date, description, link), so one model covers them."""

    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("name", "issuer")

    name: str | None = None
    issuer: str | None = None
    date: str | None = None
    description: str | None = None
    url: str | None = None


class Recommendation(Keyed):
    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("author", "text")

    author: str | None = None
    author_url: str | None = None
    author_headline: str | None = None
    relationship: str | None = None
    text: str | None = None
    date: str | None = None


class Experience(Keyed):
    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("title", "company")

    title: str | None = None
    company: str | None = None
    #: The company's own blurb, which LinkedIn shows when a position has no
    #: description of its own. Kept apart from `description` so callers can
    #: tell "what they did" from "what the company does".
    company_description: str | None = None
    company_url: str | None = None
    company_logo: str | None = None
    location: str | None = None
    employment_type: str | None = None
    description: str | None = None
    dates: DateRange | None = None



class Education(Keyed):
    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("school", "degree")

    school: str | None = None
    #: The school's own blurb, kept apart from the degree for the same reason
    #: Experience.company_description is.
    school_description: str | None = None
    school_url: str | None = None
    school_logo: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    description: str | None = None
    dates: DateRange | None = None



class Certification(Keyed):
    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("name", "issuer")

    name: str | None = None
    issuer: str | None = None
    issued: str | None = None
    credential_url: str | None = None



class Language(Keyed):
    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("name",)

    name: str | None = None
    proficiency: str | None = None



class Volunteering(Keyed):
    KEY_FIELDS: ClassVar[tuple[str, ...]] = ("role", "organization")

    role: str | None = None
    organization: str | None = None
    cause: str | None = None
    description: str | None = None
    dates: DateRange | None = None



class SearchHit(Base):
    """One result from LinkedIn's public people directory."""

    username: str
    url: str | None = None
    name: str | None = None
    location: str | None = None
    summary: str | None = None


class Meta(Base):
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_url: str | None = None
    fetched_via: str | None = None  # cache | http | browser
    status: int | None = None
    #: field name -> which parser layer supplied the value
    field_sources: dict[str, str] = Field(default_factory=dict)
    #: How it was fetched (fingerprint, proxy, challenge outcome).
    notes: list[str] = Field(default_factory=list)
    #: Things that went wrong but didn't stop the scrape.
    warnings: list[str] = Field(default_factory=list)
    #: True when LinkedIn served asterisk-redacted values for this profile.
    redacted: bool = False
    #: field -> how many entries LinkedIn withheld. An empty list plus a count
    #: here means "hidden from logged-out visitors", not "this person has none".
    redacted_counts: dict[str, int] = Field(default_factory=dict)
    #: Set when a free-text name was resolved to this profile, so the caller
    #: can see which person was picked and who else matched.
    query: str | None = None
    resolved_from: str | None = None  # slug | url | name-search
    candidates: list["SearchHit"] = Field(default_factory=list)


class Profile(Base):
    username: str | None = None
    url: str | None = None

    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    headline: str | None = None
    location: str | None = None
    country: str | None = None
    about: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None

    current_title: str | None = None
    current_company: str | None = None

    follower_count: int | None = None
    #: Only set when LinkedIn shows an exact number. It caps the display at
    #: "500+", which is a lower bound, not a count -- see connection_count_text.
    connection_count: int | None = None
    #: The count exactly as displayed, e.g. "500+".
    connection_count_text: str | None = None
    recommendations_count: int | None = None

    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    volunteering: list[Volunteering] = Field(default_factory=list)
    honors: list[Entry] = Field(default_factory=list)
    publications: list[Entry] = Field(default_factory=list)
    courses: list[Entry] = Field(default_factory=list)
    projects: list[Entry] = Field(default_factory=list)
    patents: list[Entry] = Field(default_factory=list)
    organizations: list[Entry] = Field(default_factory=list)
    test_scores: list[Entry] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    also_viewed: list[str] = Field(default_factory=list)
    websites: list[str] = Field(default_factory=list)
    #: e.g. "Creator, Top Voice" -- LinkedIn's badge line under the name.
    creator_badge: str | None = None
    #: Any `data-section` we don't have a typed model for yet, so a section
    #: LinkedIn adds tomorrow shows up as data instead of vanishing silently.
    extra_sections: dict[str, list[str]] = Field(default_factory=dict)

    meta: Meta = Field(default_factory=Meta, alias="_meta")

    #: Scalar fields the merge layer walks when folding parser outputs together.
    SCALARS: ClassVar[tuple[str, ...]] = (
        "full_name",
        "first_name",
        "last_name",
        "headline",
        "location",
        "country",
        "about",
        "avatar_url",
        "banner_url",
        "current_title",
        "current_company",
        "creator_badge",
        "follower_count",
        "connection_count",
        "connection_count_text",
        "recommendations_count",
    )
    #: List fields whose items expose .key() for dedupe.
    KEYED_LISTS: ClassVar[tuple[str, ...]] = (
        "experience",
        "education",
        "certifications",
        "languages",
        "volunteering",
        "honors",
        "publications",
        "courses",
        "projects",
        "patents",
        "organizations",
        "test_scores",
        "recommendations",
    )
    #: Plain string list fields.
    STRING_LISTS: ClassVar[tuple[str, ...]] = ("skills", "also_viewed", "websites")

    def is_empty(self) -> bool:
        return not (self.full_name or self.headline or self.experience or self.education)
