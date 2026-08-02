"""Handle LinkedIn's server-side redaction of guest profiles.

On many profiles LinkedIn serves the *shape* of every list but replaces the
values past the first entry with literal asterisk runs:

    "worksFor": [{"name": "Trinity Ventures"}, {"name": "***** *************"}, ...]

Measured behaviour (same profile, 7 fetches): the redaction is byte-identical
across curl_cffi fingerprints (chrome/safari/firefox), country subdomains, and
real Chrome under patchright with JavaScript running. It is decided server-side
before the bytes are sent, so no client-side technique recovers it.

What matters is that we never emit `***** ******` as if it were a value, and
that callers can tell "LinkedIn withheld this" apart from "this person has
none". Masked values become null, fully-masked entries are dropped, and the
counts land in `_meta.redacted`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..models import Keyed, Profile

#: Three or more asterisks, optionally spaced into fake "words".
_MASK_RE = re.compile(r"\*{3,}")


def is_masked(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if not _MASK_RE.search(value):
        return False
    # Guard against a real value that merely contains asterisks (e.g. a footnote):
    # treat it as masked only when asterisks dominate.
    stars = value.count("*")
    return stars >= 3 and stars >= len(value.replace(" ", "")) * 0.5


def _scrub_model(model: BaseModel) -> int:
    """Null out masked string fields in place; return how many were masked."""
    masked = 0
    for field in type(model).model_fields:
        value = getattr(model, field, None)
        if isinstance(value, BaseModel):
            masked += _scrub_model(value)
        elif is_masked(value):
            setattr(model, field, None)
            masked += 1
        elif isinstance(value, list):
            kept = [v for v in value if not is_masked(v)]
            masked += len(value) - len(kept)
            if len(kept) != len(value):
                setattr(model, field, kept)
    return masked


def scrub(profile: Profile) -> dict[str, int]:
    """Strip redacted values from a parsed profile.

    Returns a map of field name -> how many entries LinkedIn withheld, which
    the caller should surface so an empty list isn't mistaken for "none".
    """
    counts: dict[str, int] = {}

    for field in Profile.SCALARS + ("creator_badge",):
        if is_masked(getattr(profile, field, None)):
            setattr(profile, field, None)
            counts[field] = counts.get(field, 0) + 1

    for field in Profile.KEYED_LISTS:
        items: list[Keyed] = getattr(profile, field)
        kept: list[Keyed] = []
        dropped = 0
        for item in items:
            # An entry whose identifying fields are all masked carries nothing.
            if all(is_masked(getattr(item, k, None)) or not getattr(item, k, None)
                   for k in type(item).KEY_FIELDS):
                dropped += 1
                continue
            _scrub_model(item)
            kept.append(item)
        if dropped:
            counts[field] = dropped
        setattr(profile, field, kept)

    for field in Profile.STRING_LISTS:
        values = getattr(profile, field)
        kept_values = [v for v in values if not is_masked(v)]
        if len(kept_values) != len(values):
            counts[field] = len(values) - len(kept_values)
        setattr(profile, field, kept_values)

    return counts
