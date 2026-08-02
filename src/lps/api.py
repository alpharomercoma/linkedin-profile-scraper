"""FastAPI wrapper over the same core the CLI uses."""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import settings
from .models import Profile, SearchHit
from .scraper import ProfileNotFound, ScrapeError, scrape_profile, scrape_query
from .search import search_people


class LookupRequest(BaseModel):
    q: str = Field(..., description="Username, profile URL, or person's name")
    strategy: str | None = Field(None, pattern="^(auto|http|browser)$")
    refresh: bool = False

log = logging.getLogger(__name__)

api = FastAPI(
    title="LinkedIn Profile Scraper",
    description="Unauthenticated public-profile lookup. Subject to LinkedIn's ToS.",
    version="0.1.0",
)

# The browser path is heavy (a real Chrome per fetch), so cap concurrency.
_semaphore = asyncio.Semaphore(settings.api_max_concurrency)


_INDEX = Path(__file__).parent / "static" / "index.html"


@api.get("/linkedin", response_class=HTMLResponse, include_in_schema=False)
async def linkedin_page() -> str:
    """Browser UI for running and eyeballing a scrape."""
    return _INDEX.read_text()


@api.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse("/linkedin")


@api.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "strategy": settings.strategy,
        "proxies": len(settings.proxies),
        "captcha_solver": settings.captcha_provider if settings.captcha_enabled else None,
    }


def _require_key(request: Request) -> None:
    """No-op unless LPS_API_KEY is set. This endpoint scrapes on the caller's
    behalf, so a public deployment without a key is an open proxy that burns
    your function quota and your IP reputation."""
    expected = settings.api_key
    if not expected:
        return
    supplied = request.headers.get("x-api-key") or request.query_params.get("key")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="missing or invalid API key")


async def _lookup(query: str, strategy: str | None, refresh: bool) -> Profile:
    async with _semaphore:
        try:
            return await asyncio.to_thread(
                scrape_query, query, settings, strategy, not refresh
            )
        except ProfileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ScrapeError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": str(exc), "attempts": [asdict(a) for a in exc.attempts]},
            ) from exc


@api.get("/api/linkedin", response_model=Profile, response_model_by_alias=True)
async def linkedin_get(
    request: Request,
    response: Response,
    q: str = Query(..., description="Username, profile URL, or person's name"),
    strategy: str | None = Query(None, pattern="^(auto|http|browser)$"),
    refresh: bool = Query(False, description="Bypass the raw HTML cache"),
) -> Profile:
    """Look up a profile. GET is the primary verb here: the call is read-only
    and idempotent, so responses are CDN-cacheable, which is what actually
    makes repeat lookups fast."""
    _require_key(request)
    profile = await _lookup(q, strategy, refresh)
    if not refresh:
        # A profile does not change minute to minute. Let the edge serve
        # repeats so identical queries never re-hit LinkedIn.
        response.headers["Cache-Control"] = "public, s-maxage=3600, stale-while-revalidate=86400"
    return profile


@api.post("/api/linkedin", response_model=Profile, response_model_by_alias=True)
async def linkedin_post(request: Request, body: LookupRequest) -> Profile:
    """Same lookup with the query in the body, for callers who would rather
    keep it out of URLs, logs and Referer headers. Never cached."""
    _require_key(request)
    return await _lookup(body.q, body.strategy, body.refresh)


@api.get("/api/linkedin/search", response_model=list[SearchHit])
async def linkedin_search(
    request: Request, q: str = Query(..., description="Person's name")
) -> list[SearchHit]:
    """Resolve a name to candidate profiles without scraping any of them."""
    _require_key(request)
    return await asyncio.to_thread(search_people, q, settings)


@api.get("/profile/{username}", response_model=Profile, response_model_by_alias=True)
async def get_profile(
    username: str,
    strategy: str | None = Query(None, pattern="^(auto|http|browser)$"),
    refresh: bool = Query(False, description="Bypass the raw HTML cache"),
) -> Profile:
    async with _semaphore:
        try:
            return await asyncio.to_thread(
                scrape_profile, username, settings, strategy, not refresh
            )
        except ProfileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ScrapeError as exc:
            # 502: LinkedIn walled us. Report *which* wall, so the caller can
            # tell "bad proxy" apart from "profile is private".
            return JSONResponse(
                status_code=502,
                content={
                    "detail": str(exc),
                    "attempts": [asdict(a) for a in exc.attempts],
                },
            )
