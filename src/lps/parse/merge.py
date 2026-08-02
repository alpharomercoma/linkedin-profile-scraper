"""Run every parser layer and fold the results into one Profile.

Priority is jsonld > embedded > dom for scalars (first non-empty wins); list
fields merge across all layers and dedupe on a stable key. Every populated
field records which layer supplied it in `_meta.field_sources`, so a surprising
value is always traceable to a parser.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from ..models import Keyed, Profile
from . import dom, embedded, jsonld, meta
from .redact import scrub

log = logging.getLogger(__name__)

#: Highest priority first. `meta` sits above `dom` because Open Graph tags are
#: LinkedIn's own link-preview payload -- they stay intact on profiles whose
#: body is redacted, and they carry the connection count nothing else exposes.
LAYERS = (jsonld, embedded, meta, dom)


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _compatible(a: Keyed, b: Keyed) -> bool:
    """Same entry seen by two layers?

    Keys match when no non-empty part conflicts and at least one part agrees.
    JSON-LD often knows the school but not the degree while the DOM knows both;
    that's one education entry, not two. Two genuinely different degrees at the
    same school still conflict on the degree part and stay separate.
    """
    ka, kb = a.key(), b.key()
    if len(ka) != len(kb):
        return False
    agreed = False
    for x, y in zip(ka, kb):
        if x and y:
            if x != y:
                return False
            agreed = True
    return agreed


def _find_compatible(items: list, candidate: Keyed) -> Keyed | None:
    return next((item for item in items if _compatible(item, candidate)), None)


def _enrich(target: BaseModel, extra: BaseModel) -> None:
    """Fill fields the winning entry is missing from a lower-priority one.

    Recurses into nested models so e.g. a JSON-LD DateRange that only knows the
    start year picks up the end and duration the DOM layer parsed.
    """
    for field in type(target).model_fields:
        mine, theirs = getattr(target, field, None), getattr(extra, field, None)
        if isinstance(mine, BaseModel) and isinstance(theirs, BaseModel):
            _enrich(mine, theirs)
        elif _empty(mine) and not _empty(theirs):
            setattr(target, field, theirs)


def run_layers(html: str) -> tuple[list[tuple[str, Profile]], list[str]]:
    """Parse with every layer. A failing layer warns instead of raising."""
    results: list[tuple[str, Profile]] = []
    warnings: list[str] = []
    for layer in LAYERS:
        try:
            results.append((layer.LAYER, layer.parse(html)))
        except Exception as exc:  # a broken parser must never fail the scrape
            log.exception("parser %s failed", layer.LAYER)
            warnings.append(f"parser {layer.LAYER} failed: {type(exc).__name__}: {exc}")
    return results, warnings


def merge(html: str) -> Profile:
    parsed, warnings = run_layers(html)
    out = Profile()
    out.meta.warnings.extend(warnings)

    for name, candidate in parsed:
        for field in Profile.SCALARS:
            if _empty(getattr(out, field)) and not _empty(getattr(candidate, field)):
                setattr(out, field, getattr(candidate, field))
                out.meta.field_sources[field] = name

        if _empty(out.url) and not _empty(candidate.url):
            out.url = candidate.url
            out.meta.field_sources["url"] = name

        for field in Profile.KEYED_LISTS:
            target = getattr(out, field)
            added = 0
            for item in getattr(candidate, field):
                if not any(item.key()):
                    continue
                existing = _find_compatible(target, item)
                if existing is None:
                    target.append(item)
                    added += 1
                else:
                    _enrich(existing, item)
            if added and field not in out.meta.field_sources:
                out.meta.field_sources[field] = name

        for field in Profile.STRING_LISTS:
            target = getattr(out, field)
            seen = {v.lower() for v in target}
            added = 0
            for value in getattr(candidate, field):
                if value and value.lower() not in seen:
                    seen.add(value.lower())
                    target.append(value)
                    added += 1
            if added and field not in out.meta.field_sources:
                out.meta.field_sources[field] = name

    if not out.full_name and (out.first_name or out.last_name):
        out.full_name = " ".join(x for x in (out.first_name, out.last_name) if x)

    # Drop LinkedIn's asterisk-redacted placeholders, recording what was
    # withheld so an empty list isn't misread as "this person has none".
    counts = scrub(out)
    if counts:
        out.meta.redacted = True
        out.meta.redacted_counts = counts
        out.meta.warnings.append(
            "LinkedIn redacted part of this profile for logged-out visitors: "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )

    return out
