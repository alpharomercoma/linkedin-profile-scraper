"""Candidate LinkedIn URLs for unauthenticated profile data.

Measured 2026-08-02 from a residential IP, 140 (surface x client) combinations:

* `www.linkedin.com/in/{u}` and every country subdomain return byte-equivalent
  content. Subdomains are failover for rate limiting, not richer data.
* Crawler user agents (Googlebot, Bingbot, LinkedInBot, facebookexternalhit,
  Slackbot) get a page with **no JSON-LD at all** -- strictly worse than a
  browser fingerprint. Do not impersonate crawlers.
* `/mwlite/...` and `m.linkedin.com` redirect to a login-walled SPA: HTTP 200
  with no profile data. Dead end.
* `?locale=`, `?_l=`, `?trk=`, `original_referer` change nothing measurable.
* Concurrency is the real hazard: 6 parallel requests triggered HTTP 429 across
  every subdomain within seconds. Serial requests with delay stay healthy.

`lps probe <username>` re-measures all of this from your own network.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    name: str
    template: str
    #: What we currently believe happens unauthenticated. Probe verifies it.
    expectation: str
    #: Whether the scraper actually parses this one for profile data.
    parseable: bool = True

    def url(self, username: str) -> str:
        return self.template.format(u=username)


PROFILE_ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        name="public",
        template="https://www.linkedin.com/in/{u}",
        expectation="renders the guest profile page with JSON-LD when the IP is trusted",
    ),
    Endpoint(
        name="public_trk",
        template="https://www.linkedin.com/in/{u}?trk=public_profile_browsemap&locale=en_US",
        expectation="same page, referral params sometimes soften the wall",
    ),
    Endpoint(
        name="cc_ph",
        template="https://ph.linkedin.com/in/{u}",
        expectation="country subdomain: identical content, useful as 429 failover",
    ),
    Endpoint(
        name="cc_uk",
        template="https://uk.linkedin.com/in/{u}",
        expectation="country subdomain",
    ),
    Endpoint(
        name="cc_in",
        template="https://in.linkedin.com/in/{u}",
        expectation="country subdomain",
    ),
    Endpoint(
        name="cc_de",
        template="https://de.linkedin.com/in/{u}",
        expectation="country subdomain",
    ),
)

#: Probed for the report only -- never parsed for profile data.
DIAGNOSTIC_ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        name="voyager_profileview",
        template="https://www.linkedin.com/voyager/api/identity/profiles/{u}/profileView",
        expectation="401/403 without a session cookie -- documents why we don't use it",
        parseable=False,
    ),
    Endpoint(
        name="jobs_guest_control",
        template="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=engineer&start=0",
        expectation="200 -- a genuinely public guest API, proves the fetch layer works",
        parseable=False,
    ),
    Endpoint(
        name="pub_dir",
        template="https://www.linkedin.com/pub/dir?firstName=&lastName={u}",
        expectation="people-search directory, used for slug resolution if it renders",
        parseable=False,
    ),
)

ALL_ENDPOINTS = PROFILE_ENDPOINTS + DIAGNOSTIC_ENDPOINTS


def normalize_username(value: str) -> str:
    """Accept a bare slug or any profile URL and return the slug."""
    value = value.strip().rstrip("/")
    if "/in/" in value:
        value = value.partition("/in/")[2] or value.rsplit("/", 1)[-1]
    elif "linkedin.com" in value:
        value = value.rsplit("/", 1)[-1]
    value = value.split("?", 1)[0].split("#", 1)[0]
    return value.strip("/")
