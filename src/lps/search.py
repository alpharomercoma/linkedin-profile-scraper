"""Resolve a free-text name to a profile slug.

LinkedIn's public people directory answers unauthenticated:

    https://www.linkedin.com/pub/dir?firstName=Alpha&lastName=Coma

and returns `.base-search-card` entries carrying the slug, display name,
location and current company. That is enough to turn "Alpha Romer Coma" into
"alpharomercoma" without a session.

Results are ranked by LinkedIn, and this keeps that order. Every candidate is
returned alongside the pick so an ambiguous name is visible to the caller
rather than silently resolved to the wrong person.
"""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

from .config import Settings
from .endpoints import normalize_username
from .fetch.base import block_reason
from .fetch.http_fetcher import HttpFetcher
from .fetch.proxies import ProxyPool
from .models import SearchHit

log = logging.getLogger(__name__)

DIRECTORY_URL = "https://www.linkedin.com/pub/dir?firstName={first}&lastName={last}"
#: Real directory responses run ~60KB; the 999 block page is ~1.5KB.
DIRECTORY_MIN_BYTES = 2_500

#: A slug never contains whitespace; a name almost always does.
_SLUGGISH = re.compile(r"^[\w\-.%]+$")


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value).strip() or None


def looks_like_slug(query: str) -> bool:
    """True when the query is already a username or profile URL."""
    query = query.strip()
    if "linkedin.com" in query or query.startswith("/in/"):
        return True
    return bool(_SLUGGISH.match(query))


def split_name(name: str) -> tuple[str, str]:
    """First and last token. LinkedIn's directory takes exactly two fields, so
    middle names are dropped rather than guessed at."""
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def parse_directory(html: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    tree = HTMLParser(html)
    for card in tree.css(".base-search-card, li.profile-card"):
        link = card if card.attributes.get("href") else card.css_first("a[href*='/in/']")
        href = (link.attributes.get("href") if link is not None else None) or ""
        if "/in/" not in href:
            continue
        username = normalize_username(href)
        if not username:
            continue
        hits.append(
            SearchHit(
                username=username,
                url=f"https://www.linkedin.com/in/{username}",
                name=_clean(
                    (card.css_first(".base-search-card__title") or card.css_first("h3")).text()
                    if (card.css_first(".base-search-card__title") or card.css_first("h3"))
                    else None
                ),
                location=_clean(
                    card.css_first(".people-search-card__location").text()
                    if card.css_first(".people-search-card__location")
                    else None
                ),
                summary=_clean(
                    card.css_first(".base-search-card__metadata").text()
                    if card.css_first(".base-search-card__metadata")
                    else None
                ),
            )
        )
    return hits


def search_people(
    name: str, settings: Settings, fetcher: HttpFetcher | None = None
) -> list[SearchHit]:
    """Look a person up in the public directory. Returns [] when blocked."""
    first, last = split_name(name)
    if not (first or last):
        return []

    fetcher = fetcher or HttpFetcher(settings, ProxyPool(settings.proxies))
    result = fetcher.fetch(DIRECTORY_URL.format(first=first, last=last))
    # A directory page is an order of magnitude smaller than a profile page,
    # so the profile-sized floor would reject a perfectly good result.
    reason = block_reason(result, min_size=DIRECTORY_MIN_BYTES)
    if reason:
        log.info("directory search blocked: %s", reason)
        return []
    return parse_directory(result.html)
