"""Fetch result type and block detection.

Block detection is deliberately separate from fetching: the HTTP layer, the
browser layer and the probe command all need the same verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Bodies smaller than this are interstitials, never a rendered profile.
MIN_BODY_BYTES = 4_000

_BLOCKED_STATUSES = {401, 403, 429, 999}
_WALL_PATHS = ("/authwall", "/uas/login", "/checkpoint/", "/signup/", "/login")
# Only markers unique to the interstitial itself. Note that a *good* public
# profile page also links to /authwall and says "Sign in to view ...", so those
# strings must never be treated as block signals.
_WALL_MARKERS = (
    'id="join-form"',
    "join-form-container",
    "challenge-error-page",
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_WALL_TITLES = ("sign up | linkedin", "linkedin login, sign in", "join linkedin")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    html: str
    via: str  # cache | http | browser
    elapsed: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.html or "")


def title_of(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def block_reason(result: FetchResult, min_size: int = MIN_BODY_BYTES) -> str | None:
    """Return a human-readable reason the response is unusable, or None.

    `min_size` is caller-scoped: the floor that identifies an interstitial in
    place of a profile page is far too high for smaller responses such as a
    directory search result.
    """
    if result.error:
        return f"transport error: {result.error}"
    if result.status in _BLOCKED_STATUSES:
        return f"status {result.status}"
    if result.status >= 500:
        return f"status {result.status}"
    if result.status == 404:
        return "not found"

    lowered_url = (result.final_url or "").lower()
    for path in _WALL_PATHS:
        if path in lowered_url:
            return f"redirected to {path}"

    if result.size < min_size:
        return f"body too small ({result.size} bytes)"

    lowered = result.html.lower()
    title = title_of(result.html).lower()
    if any(t in title for t in _WALL_TITLES):
        return f"auth-wall title: {title!r}"
    for marker in _WALL_MARKERS:
        if marker in lowered:
            return f"auth-wall marker: {marker!r}"
    return None


def is_blocked(result: FetchResult) -> bool:
    return block_reason(result) is not None
