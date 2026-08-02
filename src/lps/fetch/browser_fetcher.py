"""patchright fetcher: escalation path when the HTTP fetcher gets walled.

Configuration follows patchright's own guidance -- real Chrome channel,
persistent context, no viewport, and crucially *no* custom user_agent or
extra_http_headers, since overriding those is what makes patched Playwright
detectable again.
"""

from __future__ import annotations

import logging
import random
import time

from ..config import Settings
from .base import FetchResult
from .challenge import Challenge, Solver, SolverError, detect_challenge
from .proxies import NoProxyAvailable, ProxyPool, as_playwright_proxy

log = logging.getLogger(__name__)


class BrowserFetcher:
    via = "browser"

    def __init__(
        self,
        settings: Settings,
        pool: ProxyPool | None = None,
        solver: Solver | None = None,
    ) -> None:
        self.settings = settings
        self.pool = pool or ProxyPool(settings.proxies)
        self.solver = solver
        self.solve_attempts = 0

    def fetch(self, url: str) -> FetchResult:
        try:
            from patchright.sync_api import Error as PlaywrightError
            from patchright.sync_api import sync_playwright
        except ImportError as exc:
            return FetchResult(
                url=url, final_url=url, status=0, html="", via=self.via,
                error=f"patchright is not installed: {exc}",
            )

        started = time.monotonic()
        try:
            proxy = self.pool.next()
        except NoProxyAvailable as exc:
            return FetchResult(
                url=url, final_url=url, status=0, html="", via=self.via, error=str(exc)
            )
        notes = [f"proxy={proxy or 'direct'}", f"channel={self.settings.browser_channel}"]

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.settings.user_data_dir),
                    channel=self.settings.browser_channel,
                    headless=self.settings.browser_headless,
                    no_viewport=True,
                    locale=self.settings.locale,
                    proxy=as_playwright_proxy(proxy),
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=self.settings.timeout * 1000
                    )
                    status = response.status if response else 0
                    self._settle(page)

                    html = page.content()
                    final_url = page.url

                    challenge = detect_challenge(html, final_url)
                    if challenge and challenge.solvable:
                        solved = self._try_solve(page, challenge, notes)
                        if solved:
                            self._settle(page)
                            html = page.content()
                            final_url = page.url
                            status = 200
                    elif challenge:
                        notes.append(f"challenge={challenge.kind} (not solvable)")

                    return FetchResult(
                        url=url,
                        final_url=final_url,
                        status=status,
                        html=html,
                        via=self.via,
                        elapsed=time.monotonic() - started,
                        notes=notes,
                    )
                finally:
                    context.close()
        except PlaywrightError as exc:
            self.pool.report_failure(proxy)
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                html="",
                via=self.via,
                elapsed=time.monotonic() - started,
                error=str(exc).splitlines()[0],
                notes=notes,
            )

    def _settle(self, page) -> None:
        """Scroll to the bottom so lazily-mounted sections render."""
        try:
            page.wait_for_timeout(random.randint(800, 1600))
            for _ in range(6):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(random.randint(250, 600))
            page.wait_for_timeout(600)
        except Exception as exc:  # never fail a fetch over scrolling
            log.debug("settle failed: %s", exc)

    def _try_solve(self, page, challenge: Challenge, notes: list[str]) -> bool:
        if self.solver is None:
            notes.append(f"challenge={challenge.kind}; no solver configured")
            return False
        if self.solve_attempts >= self.settings.captcha_max_per_run:
            notes.append(f"challenge={challenge.kind}; solve cap reached")
            return False

        self.solve_attempts += 1
        try:
            token = self.solver.solve(challenge)
        except SolverError as exc:
            notes.append(f"challenge={challenge.kind}; solver failed: {exc}")
            return False

        # Inject into the page's main world -- the challenge form lives there.
        page.evaluate(
            """(token) => {
                const names = [
                    'fc-token', 'captcha-response', 'g-recaptcha-response',
                    'verifyToken', 'captchaUserResponseToken',
                ];
                for (const n of names) {
                    for (const el of document.querySelectorAll(
                        `[name="${n}"], #${CSS.escape(n)}`)) {
                        el.value = token;
                    }
                }
                document.querySelector('form')?.submit();
            }""",
            token,
            isolated_context=False,
        )
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception as exc:
            log.debug("post-solve navigation wait: %s", exc)
        notes.append(f"challenge={challenge.kind}; solved via {self.solver.name}")
        return True
