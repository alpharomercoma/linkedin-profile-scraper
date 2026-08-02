"""Typer CLI."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from .config import settings
from .endpoints import normalize_username
from .fetch.challenge import detect_challenge
from .models import Profile
from .parse.merge import merge
from .scraper import ProfileNotFound, ScrapeError, probe as run_probe, scrape_profile

app = typer.Typer(
    add_completion=False,
    help="Scrape a public LinkedIn profile into JSON (unauthenticated).",
)
err = typer.style


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _emit(payload: str, out: Path | None) -> None:
    if out:
        out.write_text(payload)
        typer.echo(f"wrote {out}", err=True)
    else:
        typer.echo(payload)


def _dump(profile: Profile, pretty: bool) -> str:
    return profile.model_dump_json(indent=2 if pretty else None, by_alias=True)


@app.command()
def scrape(
    username: str = typer.Argument(..., help="Profile slug or full LinkedIn URL"),
    strategy: str = typer.Option(
        None, "--strategy", "-s", help="auto | http | browser (default: LPS_STRATEGY)"
    ),
    proxy: str = typer.Option(None, "--proxy", help="Single proxy URL, overrides LPS_PROXIES"),
    out: Path = typer.Option(None, "--out", "-o", help="Write JSON here instead of stdout"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the raw HTML cache"),
    solve_captcha: bool = typer.Option(
        True, "--solve-captcha/--no-solve-captcha", help="Allow the configured CAPTCHA solver"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch one profile and print it as JSON."""
    _setup_logging(verbose)
    if proxy:
        settings.proxies = [proxy]
    if not solve_captcha:
        settings.captcha_provider = None
    if not settings.proxies:
        logging.getLogger(__name__).info(
            "no proxies configured; a direct IP is the most common cause of auth walls"
        )

    try:
        profile = scrape_profile(username, settings, strategy=strategy, use_cache=not no_cache)
    except ProfileNotFound as exc:
        typer.echo(err(f"not found: {exc}", fg="yellow"), err=True)
        raise typer.Exit(code=4)
    except ScrapeError as exc:
        typer.echo(err(f"blocked: {exc}", fg="red"), err=True)
        raise typer.Exit(code=3)

    _emit(_dump(profile, pretty), out)


@app.command()
def probe(
    username: str = typer.Argument(..., help="A profile slug known to exist"),
    out: Path = typer.Option(Path("probe-report.json"), "--out", "-o"),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Also probe via patchright"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Measure every mapped endpoint and write probe-report.json.

    Reorder endpoints.py against this report -- the shipped order is a guess
    until it is measured from your IP and proxies.
    """
    _setup_logging(verbose)
    rows = run_probe(username, settings, include_browser=browser)
    payload = json.dumps(
        {"username": normalize_username(username), "results": [asdict(r) for r in rows]}, indent=2
    )
    out.write_text(payload)

    for row in rows:
        verdict = "OK " if not row.blocked else "BLK"
        name = f" name={row.parsed_name!r}" if row.parsed_name else ""
        typer.echo(
            f"{verdict} {row.endpoint:<22} {row.via:<7} {row.status:<4} "
            f"{row.size:>8}b {row.blocked or 'usable'}{name}"
        )
    typer.echo(f"\nwrote {out}", err=True)


@app.command("parse-file")
def parse_file(
    path: Path = typer.Argument(..., help="Saved HTML page (e.g. from .cache/)"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Re-parse a cached page offline. This is how parser work iterates without
    re-hitting LinkedIn."""
    html = path.read_text(errors="replace")
    profile = merge(html)
    profile.meta.source_url = str(path)
    profile.meta.fetched_via = "file"

    if profile.is_empty():
        challenge = detect_challenge(html, str(path))
        if challenge:
            detail = (
                "no solver configured"
                if not settings.captcha_enabled
                else f"solver: {settings.captcha_provider}"
            )
            typer.echo(
                err(f"challenge detected ({challenge.kind}); {detail}", fg="yellow"), err=True
            )
            raise typer.Exit(code=5)

    _emit(_dump(profile, pretty), out)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("lps.api:api", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
