from __future__ import annotations

from lps.fetch.base import FetchResult, block_reason
from lps.fetch.challenge import detect_challenge, get_solver
from lps.config import Settings


def result(html: str = "", **kw) -> FetchResult:
    defaults = dict(
        url="https://www.linkedin.com/in/adalovelace",
        final_url="https://www.linkedin.com/in/adalovelace",
        status=200,
        html=html,
        via="http",
    )
    return FetchResult(**{**defaults, **kw})


def test_good_page_is_not_blocked(public_html: str) -> None:
    assert block_reason(result(public_html)) is None


def test_authwall_is_blocked(authwall_html: str) -> None:
    reason = block_reason(result(authwall_html))
    assert reason is not None


def test_authwall_redirect_is_blocked(public_html: str) -> None:
    r = result(public_html, final_url="https://www.linkedin.com/authwall?trk=x")
    assert "authwall" in block_reason(r)


def test_status_999_is_blocked() -> None:
    assert block_reason(result("x" * 9000, status=999)) == "status 999"


def test_transport_error_is_blocked() -> None:
    assert "transport error" in block_reason(result(error="proxy refused", status=0))


def test_missing_profile_reports_not_found() -> None:
    assert block_reason(result("x" * 9000, status=404)) == "not found"


def test_public_profile_mentioning_authwall_links_is_still_usable(public_html: str) -> None:
    # The real guest page links to /authwall and says "Sign in to view ...".
    # Those must not be treated as block signals.
    assert "authwall" in public_html and "Sign in to view" in public_html
    assert block_reason(result(public_html)) is None


def test_detects_arkose_challenge(challenge_html: str) -> None:
    challenge = detect_challenge(challenge_html, "https://www.linkedin.com/checkpoint/challenge/")
    assert challenge is not None
    assert challenge.kind == "arkose"
    assert challenge.sitekey == "3117BF26-4762-4F5A-8ED9-A85E69209A46"
    assert challenge.surl == "https://linkedin-api.arkoselabs.com"
    assert challenge.solvable


def test_detects_authwall_challenge(authwall_html: str) -> None:
    challenge = detect_challenge(authwall_html, "https://www.linkedin.com/authwall")
    assert challenge is not None and challenge.kind == "authwall"
    assert not challenge.solvable  # no solver can get past a login wall


def test_clean_page_has_no_challenge(public_html: str) -> None:
    assert detect_challenge(public_html, "https://www.linkedin.com/in/adalovelace") is None


def test_solver_disabled_without_credentials() -> None:
    assert get_solver(Settings(captcha_provider=None, captcha_api_key=None)) is None
    assert get_solver(Settings(captcha_provider="capsolver", captcha_api_key=None)) is None
    assert get_solver(Settings(captcha_provider="nope", captcha_api_key="k")) is None
    assert get_solver(Settings(captcha_provider="capsolver", captcha_api_key="k")) is not None
