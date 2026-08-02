# linkedin-profile-scraper

Give it a LinkedIn username, get back a normalized JSON profile. Unauthenticated:
no login, no `li_at` cookie, nothing that can get an account restricted.

Live: [profilescraper.vercel.app/linkedin](https://profilescraper.vercel.app/linkedin)

```console
$ uv run lps scrape williamhgates
{
  "username": "williamhgates",
  "full_name": "Bill Gates",
  "headline": "Chair, Gates Foundation and Founder, Breakthrough Energy",
  "location": "Seattle, Washington, United States",
  "follower_count": 40554575,
  "experience": [{"title": "Co-chair", "company": "Gates Foundation", ...}],
  "education": [{"school": "Harvard University", ...}],
  "_meta": {"fetched_via": "http", "field_sources": {"full_name": "jsonld", ...}}
}
```

> **Terms of service.** Automated collection from LinkedIn violates their User
> Agreement whether or not you are logged in. You are responsible for how you
> use this. It is built for single-profile, on-demand lookups; it deliberately
> has no bulk enumeration.

## Install

```console
uv sync
uv run patchright install chrome      # only needed for the browser path
cp .env.example .env                  # optional
```

## Use

```console
uv run lps scrape <username|url>      # JSON to stdout
uv run lps scrape ada -o ada.json --strategy browser --proxy http://user:pw@host:port
uv run lps probe <username>           # measure every endpoint -> probe-report.json
uv run lps parse-file .cache/ada/<hash>.html   # re-parse a cached page offline
uv run lps serve --port 8000          # HTTP API
```

### HTTP API

```console
GET  /api/linkedin?q=Alpha%20Romer%20Coma     # name, username, or profile URL
GET  /api/linkedin?q=williamhgates&refresh=true
POST /api/linkedin                            # {"q": "...", "strategy": "...", "refresh": false}
GET  /api/linkedin/search?q=Bill%20Gates      # resolve a name without scraping
GET  /profile/{username}                      # when you already have the slug
GET  /healthz
GET  /linkedin                                # browser UI
```

`q` accepts three things. A username or profile URL is used directly. A name
with a space is resolved through LinkedIn's public people directory, which
answers without a session. Names are rarely unique, so the pick and every other
candidate both land in `_meta`:

```json
"_meta": {
  "query": "Alpha Romer Coma",
  "resolved_from": "name-search",
  "candidates": [
    {"username": "alpharomercoma", "location": "San Francisco Bay Area"},
    {"username": "alpha-romer-coma-470875390", "location": "San Jose del Monte"}
  ]
}
```

**GET or POST.** Use GET. The lookup is read-only and idempotent, so GET
responses carry `Cache-Control: public, s-maxage=3600`, and repeats are served
by the CDN without re-hitting LinkedIn. That caching is the only real speed
difference between the two verbs; a cold request costs the same either way.
POST exists for one reason: a URL query string ends up in server logs, browser
history and `Referer` headers, while a body does not. If the names you look up
are themselves sensitive, POST and accept the loss of caching. For public
profile lookups GET is the better default.

**Protect it if you deploy it.** This endpoint scrapes on the caller's behalf,
so an open deployment is a proxy anyone can point at LinkedIn using your
function quota and your IP reputation. Set `LPS_API_KEY` and requests must
carry `X-API-Key` or `?key=`. Unset, as on the demo, it is open.

Exit codes: `0` ok, `3` every source blocked, `4` profile does not exist,
`5` challenge/CAPTCHA detected.

## How it works

**Fetch, cheapest first.** `curl_cffi` impersonates a real browser's TLS/JA3 and
HTTP/2 fingerprint. Without that, LinkedIn answers `HTTP 999`. It rotates
impersonation profiles, warms up on the homepage to pick up `bcookie`/`lidc`
first, and jitters between requests. If every mapped endpoint comes back walled,
it escalates to `patchright` (patched, undetected Playwright) driving real Chrome
in a persistent context. Successful raw HTML is cached to `.cache/` so parser
work never re-hits LinkedIn.

**Parse in layers, merge with provenance.** Three independent parsers run over
the same page and their results are folded together:

| layer | source | priority | notes |
|---|---|---|---|
| `jsonld` | `<script type="application/ld+json">` Person | highest | most stable; carries awards, languages, memberships |
| `embedded` | `<code id="bpr-guid-*">` Voyager models | 2nd | absent on every guest render measured so far; kept for the variants that have it |
| `meta` | Open Graph / `profile:*` tags | 3rd | survives redaction; only source of the connection count |
| `dom` | `section[data-section]` + `li.profile-section-card` | lowest | skills, honors, publications, recommendations, volunteering |

The DOM layer keys off `data-section` and `profile-section-card`, which are
LinkedIn's own semantic hooks, rather than the Tailwind class names around
them. Those are regenerated on every deploy. Sections we have no model for are
recorded in `extra_sections` rather than dropped, so a new one shows up as data.

Scalars take the first non-empty value by priority; list entries dedupe on a
key that excludes dates (each layer formats them differently) and *enrich* each
other, so one job entry can take its title from JSON-LD, its description from
the DOM and its duration from whichever layer parsed it. `_meta.field_sources`
records which layer supplied each field, so any surprising value is traceable.

**Everything is optional.** Partial extraction is the normal outcome for
unauthenticated scraping, not an error.

## What LinkedIn actually withholds, measured

There are two different reasons a field comes back empty, and the scraper
distinguishes them.

**Redaction.** On many profiles LinkedIn serves the *shape* of every list but
replaces everything past the first entry with literal asterisks, in the HTML
*and* in the JSON-LD:

```json
"worksFor": [{"name": "Trinity Ventures"}, {"name": "***** *************"}, ...]
```

Tested on one such profile with 7 fetches: four curl_cffi fingerprints
(chrome/safari/firefox/chrome131), two country subdomains, and real Chrome under
patchright with JavaScript executing. **All seven were byte-identical**, 652
asterisk runs, 22 of 23 companies masked, every time. The decision is made
server-side before the response is sent, so no fingerprint, proxy, locale, or
rendering trick recovers it. Anything claiming otherwise is using a logged-in
session.

The scraper never emits a masked placeholder as a value. Masked entries are
dropped and counted:

```json
"_meta": {"redacted": true, "redacted_counts": {"experience": 20, "current_title": 1}}
```

So `experience: []` plus `redacted_counts.experience: 20` means *withheld*,
while `experience: []` with no count means *this person has none public*.

**Template variance.** Guest profiles render in at least three variants: a rich
one with per-section `data-section` blocks (up to ~11 sections), a combined
"Experience & Education" one that is usually redacted, and a near-empty one. The
parser handles all three, and Open Graph tags backfill the sparse ones.

**Things that do not help**, all measured rather than assumed: crawler user
agents (Googlebot/Bingbot/LinkedInBot/Slackbot) get pages with **no JSON-LD at
all**; `/mwlite/` and `m.linkedin.com` are login-walled SPAs; country subdomains
serve identical bytes; `?locale=`, `?_l=`, `?trk=` and referer spoofing change
nothing. Six parallel requests earned an IP-wide HTTP 429 within seconds, so the
fetcher stays serial with jitter.

## Success rate, honestly

The single biggest factor is your IP. From a residential connection the plain
HTTP path usually works; from a datacenter IP LinkedIn serves an auth wall
almost immediately. Set `LPS_PROXIES` to residential or mobile proxies and the
pool rotates them, quarantining any that start failing. If proxies are
configured and all of them get quarantined, requests **fail** rather than
silently falling back to your real IP.

Run `lps probe <username>` first. It measures every candidate endpoint from
your network and writes a report you can use to reorder `src/lps/endpoints.py`.
It also probes the authenticated Voyager API to document why it isn't used
(`403` without a session cookie).

## CAPTCHA

LinkedIn's block ladder is auth wall → `/checkpoint/challenge` → Arkose Labs
FunCaptcha. Detection is always on and reported in `_meta.notes`. Solving is
**off unless you set both `LPS_CAPTCHA_PROVIDER` and `LPS_CAPTCHA_API_KEY`**
(CapSolver or 2Captcha), only runs on the browser path, and is capped by
`LPS_CAPTCHA_MAX_PER_RUN`. In practice the auth wall dominates and better
proxies fix it, and a solver cannot get you past a login wall.

## Deploying

The `Dockerfile` runs the API under `xvfb-run`, so the browser path stays
*headed* (much harder to detect than headless) on a host with no display. On
macOS headed Chrome opens on your real display; there is no xvfb equivalent.

## Development

```console
uv run pytest       # parser tests run off committed fixtures, no network
```

Tests never touch LinkedIn, so they stay green even when every request is being
walled. When selectors break, save the page (`lps scrape --no-cache` leaves it
in `.cache/`), add it to `tests/fixtures/`, and iterate with `lps parse-file`.
