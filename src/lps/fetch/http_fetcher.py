"""curl_cffi fetcher: cheap path, impersonates a real browser's TLS/JA3 + HTTP2
fingerprint so LinkedIn doesn't answer with HTTP 999.
"""

from __future__ import annotations

import logging
import random
import time

from curl_cffi import requests as cffi
from curl_cffi.requests.exceptions import RequestException

from ..config import IMPERSONATE_POOL, Settings
from .base import FetchResult
from .proxies import NoProxyAvailable, ProxyPool, as_curl_proxies

log = logging.getLogger(__name__)


class HttpFetcher:
    via = "http"

    def __init__(self, settings: Settings, pool: ProxyPool | None = None) -> None:
        self.settings = settings
        self.pool = pool or ProxyPool(settings.proxies)

    def _headers(self) -> dict[str, str]:
        # Only headers a browser genuinely varies. curl_cffi's impersonation
        # supplies the sec-ch-ua / user-agent set; overriding those would
        # desynchronize them from the TLS fingerprint.
        return {
            "Accept-Language": self.settings.accept_language,
            "Referer": "https://www.google.com/",
        }

    def fetch(self, url: str, impersonate: str | None = None) -> FetchResult:
        impersonate = impersonate or random.choice(IMPERSONATE_POOL)
        started = time.monotonic()
        try:
            proxy = self.pool.next()
        except NoProxyAvailable as exc:
            return FetchResult(
                url=url, final_url=url, status=0, html="", via=self.via, error=str(exc)
            )

        try:
            with cffi.Session(
                impersonate=impersonate,
                proxies=as_curl_proxies(proxy),
                timeout=self.settings.timeout,
                retry=self.settings.retries,
                allow_redirects=True,
                verify=True,
            ) as session:
                # Warm-up: pick up bcookie/lidc/li_gc like a first-time visitor
                # before asking for the profile.
                try:
                    session.get("https://www.linkedin.com/", headers=self._headers())
                except RequestException as exc:
                    log.debug("warm-up failed (continuing): %s", exc)

                time.sleep(random.uniform(self.settings.min_delay, self.settings.max_delay))
                resp = session.get(url, headers=self._headers())

            result = FetchResult(
                url=url,
                final_url=str(resp.url),
                status=resp.status_code,
                html=resp.text or "",
                via=self.via,
                elapsed=time.monotonic() - started,
                notes=[f"impersonate={impersonate}", f"proxy={proxy or 'direct'}"],
            )
        except RequestException as exc:
            self.pool.report_failure(proxy)
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                html="",
                via=self.via,
                elapsed=time.monotonic() - started,
                error=str(exc),
                notes=[f"impersonate={impersonate}", f"proxy={proxy or 'direct'}"],
            )

        if result.status and result.status < 400:
            self.pool.report_success(proxy)
        elif result.status in (403, 429, 999):
            # The proxy IP is burned for LinkedIn even though the transport worked.
            self.pool.report_failure(proxy)
        return result
