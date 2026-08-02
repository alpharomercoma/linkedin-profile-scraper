"""DOM parser for the guest profile page.

Driven by `section[data-section="..."]` and `li.profile-section-card`, which are
LinkedIn's own semantic hooks and stay put while the Tailwind-generated class
names churn. Every section renders identically -- `h3` title, `h4` subtitle,
`.date-range` dates, `.show-more-less-text__text--less` description -- so one
card reader serves all of them.

Legacy class selectors are kept as a fallback for the older guest render.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from ..models import (
    Certification,
    DateRange,
    Education,
    Entry,
    Experience,
    Language,
    Profile,
    Recommendation,
    Volunteering,
)

LAYER = "dom"

#: data-section value -> what it holds. Several names map to the same list
#: because LinkedIn splits current/past positions on some renders.
SECTION_KINDS = {
    "experience": "experience",
    "positions": "experience",
    "currentPositions": "experience",
    "currentPositionsDetails": "experience",
    "pastPositions": "experience",
    "pastPositionsDetails": "experience",
    "education": "education",
    "educations": "education",
    "educationsDetails": "education",
    "volunteering": "volunteering",
    "volunteeringExperiences": "volunteering",
    "skills": "skills",
    "certifications": "certifications",
    "licenses-and-certifications": "certifications",
    "languages": "languages",
    "honors-and-awards": "honors",
    "honors": "honors",
    "awards": "honors",
    "publications": "publications",
    "courses": "courses",
    "projects": "projects",
    "patents": "patents",
    "organizations": "organizations",
    "test-scores": "test_scores",
    "recommendations": "recommendations",
    "summary": "about",
}

#: Sections that carry no profile facts we model.
IGNORED_SECTIONS = {
    "posts", "articles", "instructor-courses", "picture", "websites", "browsemap",
    "related-posts", "course-recommendations", "interests", "people-also-viewed",
    "similar-profiles", "activity",
}

#: Legacy selectors for the older render, used only when data-section is absent.
LEGACY_ITEMS = {
    "experience": ("section.experience li.experience-item", "ul.experience__list > li"),
    "education": ("section.education li.education__list-item", "ul.education__list > li"),
    "volunteering": ("section.volunteering li", "ul.volunteering__list > li"),
    "certifications": ("section.certifications li", "ul.certifications__list > li"),
    "languages": ("section.languages li", "ul.languages__list > li"),
    "skills": ("section.skills li.skills__list-item", "ul.skills__list > li"),
}

# Matches "18K followers", "500+ connections", "1,204 followers".
_COUNT_RE = re.compile(r"([\d,.]+)\s*([KkMm]?)\+?\s*(followers?|connections?)")
# "2000 - Present · 26 yrs" -- a duration, not a place.
_DATEISH_RE = re.compile(
    r"(19|20)\d{2}|present|\b\d+\s*(yrs?|years?|mos?|months?)\b|followers?|connections?", re.I
)
#: Trailing "26 years", "· 2 yrs 3 mos".
_DURATION_RE = re.compile(
    r"·?\s*\d+\s*(?:yrs?|years?|mos?|months?)(?:\s*\d+\s*(?:mos?|months?))?\s*$", re.I
)
_SIGNIN_NAG_RE = re.compile(r"sign in to view|join now to view|登录后查看", re.I)


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _placeholder(value: str | None) -> str | None:
    """LinkedIn renders an em-dash where a field is absent."""
    return None if value in (None, "-", "–", "—", "") else value


def _first(node: Node | HTMLParser, selectors: tuple[str, ...]) -> Node | None:
    for selector in selectors:
        found = node.css_first(selector)
        if found is not None:
            return found
    return None


def _first_text(node: Node | HTMLParser, selectors: tuple[str, ...]) -> str | None:
    found = _first(node, selectors)
    return _clean(found.text()) if found is not None else None


def _attr(node: Node | HTMLParser, selectors: tuple[str, ...], attr: str) -> str | None:
    found = _first(node, selectors)
    if found is None:
        return None
    value = found.attributes.get(attr)
    return value.strip() if value else None


def _is_dateish(value: str | None) -> bool:
    return bool(value and _DATEISH_RE.search(value))


def _is_signin_nag(value: str | None) -> bool:
    return bool(value and _SIGNIN_NAG_RE.search(value))


def _parse_count(text: str) -> int | None:
    match = _COUNT_RE.search(text or "")
    if not match:
        return None
    number, suffix, _ = match.groups()
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if suffix.lower() == "k":
        value *= 1_000
    elif suffix.lower() == "m":
        value *= 1_000_000
    return int(value)


def _dates(node: Node) -> DateRange | None:
    raw = _first_text(
        node,
        (
            ".date-range",
            ".experience-item__duration",
            ".education__item--duration",
            ".certifications__date-range",
            ".volunteering__duration",
            "time",
        ),
    )
    if not raw:
        return None
    duration, body = None, raw
    match = _DURATION_RE.search(body)
    if match:
        duration = match.group(0).strip(" ·-")
        body = body[: match.start()].strip(" ·-")
    parts = [p.strip() for p in re.split(r"\s*[-–]\s*", body, maxsplit=1)]
    start = parts[0] or None
    end = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return DateRange(start=start, end=end, duration=duration, raw=raw)


class Card:
    """One `li.profile-section-card`, read once and reused by every section."""

    def __init__(self, node: Node) -> None:
        self.node = node
        self.title = _placeholder(_first_text(node, ("h3",)))
        self.subtitle = _placeholder(_first_text(node, ("h4",)))
        self.subtitles = [_clean(n.text()) for n in node.css("h4")]
        self.dates = _dates(node)
        self.description = _placeholder(
            _first_text(
                node,
                (
                    ".show-more-less-text__text--less",
                    ".show-more-less-text__text",
                    ".experience-item__description",
                ),
            )
        )
        self.url = _attr(node, ("a[href]",), "href")
        self.logo = _attr(node, ("img",), "data-delayed-url") or _attr(node, ("img",), "src")

    @property
    def date_text(self) -> str | None:
        return self.dates.raw if self.dates else None

    def paragraph_other_than(self, *known: str | None) -> str | None:
        """Sections like volunteering put an unlabelled extra fact (the cause)
        in a bare <p> alongside the dates and description."""
        skip = {k for k in known if k}
        for p in self.node.css("p"):
            text = _clean(p.text())
            if text and text not in skip and not _is_dateish(text):
                return text
        return None

    def as_entry(self) -> Entry:
        # The subtitle often trails the date ("Authors Equity January 28, 2025").
        issuer = self.subtitle
        if issuer and self.date_text and issuer.endswith(self.date_text):
            issuer = _clean(issuer[: -len(self.date_text)])
        return Entry(
            name=self.title,
            issuer=issuer,
            date=self.date_text,
            description=self.description,
            url=self.url,
        )


def _sections(tree: HTMLParser) -> dict[str, list[Node]]:
    """Group every `[data-section]` node by what kind of data it holds."""
    found: dict[str, list[Node]] = {}
    for node in tree.css("[data-section]"):
        name = node.attributes.get("data-section") or ""
        kind = SECTION_KINDS.get(name)
        if kind:
            found.setdefault(kind, []).append(node)
    return found


def _cards(nodes: list[Node]) -> list[Card]:
    cards: list[Card] = []
    for node in nodes:
        items = node.css("li.profile-section-card") or node.css("li")
        cards.extend(Card(item) for item in items)
    return cards


def _legacy_cards(tree: HTMLParser, kind: str) -> list[Card]:
    for selector in LEGACY_ITEMS.get(kind, ()):
        items = tree.css(selector)
        if items:
            return [Card(item) for item in items]
    return []


def _location(tree: HTMLParser) -> str | None:
    """Pick the subline item that is a place.

    The top card puts location, follower count and connection count in sibling
    spans with the same class, so position-based selection picks up "41M
    followers" on profiles that lead with the count.
    """
    for selector in (
        ".top-card__subline-item--bullet",
        ".top-card-layout__first-subline .top-card__subline-item",
        ".top-card__subline-item",
        ".profile-info-subheader .not-first-middot span",
    ):
        for node in tree.css(selector):
            text = _clean(node.text())
            if text and not _is_dateish(text):
                return text
    return None


def parse(html: str) -> Profile:
    tree = HTMLParser(html)
    profile = Profile()

    # Deliberately no bare "h1" fallback: the auth wall's own <h1> is "Join
    # LinkedIn", which would masquerade as a successfully parsed profile.
    profile.full_name = _first_text(
        tree,
        (
            "h1.top-card-layout__title",
            ".top-card-layout__title",
            ".top-card__title",
            "section.top-card-layout h1",
            "h1.text-heading-xlarge",
        ),
    )
    headline = _first_text(
        tree,
        (
            "h2.top-card-layout__headline",
            ".top-card-layout__headline",
            ".top-card__headline",
            "section.top-card-layout h2",
        ),
    )
    profile.headline = None if _is_signin_nag(headline) else headline
    profile.location = _location(tree)
    profile.avatar_url = _attr(
        tree,
        ("img.top-card__profile-image", ".top-card-layout__entity-image", "img.profile-photo-edit__preview"),
        "src",
    )
    profile.banner_url = _attr(tree, ("img.cover-img__image", ".profile-background-image img"), "src")

    # Counts live in sibling subline items; read each element separately so
    # "18K followers" can't be misread as the connection count.
    for item in tree.css(".top-card__subline-item, .top-card-layout__second-subline span"):
        text = _clean(item.text()) or ""
        lowered = text.lower()
        if "follower" in lowered and profile.follower_count is None:
            profile.follower_count = _parse_count(text)
        elif "connection" in lowered and profile.connection_count_text is None:
            profile.connection_count_text = text
            if "+" not in text:
                profile.connection_count = _parse_count(text)

    sections = _sections(tree)

    profile.about = _first_text(
        tree,
        (
            'section[data-section="summary"] .show-more-less-text__text--less',
            'section[data-section="summary"] p',
            "section.summary .core-section-container__content p",
            "section.summary p",
            ".core-section-container__content .show-more-less-text__text--less",
        ),
    )

    def cards_for(kind: str) -> list[Card]:
        return _cards(sections[kind]) if kind in sections else _legacy_cards(tree, kind)

    for card in cards_for("experience"):
        if not (card.title or card.subtitle):
            continue
        location = _first_text(card.node, (".experience-item__location",))
        location = None if _is_dateish(location) else _placeholder(location)
        profile.experience.append(
            Experience(
                title=card.title,
                company=card.subtitle,
                company_url=card.url,
                company_logo=card.logo,
                location=location,
                description=card.description,
                dates=card.dates,
            )
        )

    for card in cards_for("education"):
        if not card.title:
            continue
        degree, _, field = (card.subtitle or "").partition(",")
        profile.education.append(
            Education(
                school=card.title,
                school_url=card.url,
                school_logo=card.logo,
                degree=_placeholder(_clean(degree)),
                field_of_study=_placeholder(_clean(field)),
                description=card.description,
                dates=card.dates,
            )
        )

    for card in cards_for("volunteering"):
        if not (card.title or card.subtitle):
            continue
        profile.volunteering.append(
            Volunteering(
                role=card.title,
                organization=card.subtitle,
                cause=card.paragraph_other_than(card.date_text, card.description),
                description=card.description,
                dates=card.dates,
            )
        )

    for card in cards_for("certifications"):
        if not card.title:
            continue
        profile.certifications.append(
            Certification(
                name=card.title,
                issuer=card.subtitle,
                issued=card.date_text,
                credential_url=card.url,
            )
        )

    for card in cards_for("languages"):
        if not card.title:
            continue
        profile.languages.append(Language(name=card.title, proficiency=card.subtitle))

    for kind in ("honors", "publications", "courses", "projects", "patents", "organizations", "test_scores"):
        target = getattr(profile, kind)
        for card in cards_for(kind):
            if card.title:
                target.append(card.as_entry())

    # Skills are plain links, not cards.
    skill_nodes = tree.css("li.skills__item") or tree.css("section.skills li.skills__list-item")
    if not skill_nodes and "skills" in sections:
        skill_nodes = [n for s in sections["skills"] for n in s.css("li")]
    for node in skill_nodes:
        skill = _clean(node.text())
        if skill:
            profile.skills.append(skill)

    for node in tree.css(".endorsement-card"):
        author = _first_text(node, (".endorsement-card__entity h3", "h3", "img[alt]"))
        text = _first_text(
            node, (".show-more-less-text__text--less", ".endorsement-card__text", "p")
        )
        if not (author or text):
            continue
        profile.recommendations.append(
            Recommendation(
                author=author,
                author_url=_attr(node, ("a[href*='/in/']",), "href"),
                author_headline=_first_text(node, (".endorsement-card__subtitle", "h4")),
                text=text,
                date=_first_text(node, (".date-range", "time")),
            )
        )

    for link in tree.css("section.aside-section-container a[href*='/in/']"):
        href = link.attributes.get("href") or ""
        slug = href.split("/in/")[-1].split("?")[0].strip("/")
        if slug:
            profile.also_viewed.append(slug)

    # Anything LinkedIn renders that we have no model for is recorded rather
    # than dropped, so a new section shows up as data instead of vanishing.
    for node in tree.css("[data-section]"):
        name = node.attributes.get("data-section") or ""
        if not name or name in SECTION_KINDS or name in IGNORED_SECTIONS:
            continue
        titles = [t for t in (_clean(h.text()) for h in node.css("h3")) if t]
        if titles:
            profile.extra_sections.setdefault(name, []).extend(titles)

    if profile.experience:
        profile.current_title = profile.experience[0].title
        profile.current_company = profile.experience[0].company

    return profile
