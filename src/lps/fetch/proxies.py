"""Round-robin proxy pool with failure quarantine."""

from __future__ import annotations

import itertools
import threading


class NoProxyAvailable(RuntimeError):
    """Proxies were configured but all are quarantined.

    Falling back to a direct connection here would silently leak the real IP of
    someone who deliberately configured proxies, so this is an error instead.
    """


class ProxyPool:
    def __init__(self, proxies: list[str] | None = None, max_failures: int = 3) -> None:
        self._all = list(proxies or [])
        self._max_failures = max_failures
        self._failures: dict[str, int] = {}
        self._lock = threading.Lock()
        self._cycle = itertools.cycle(self._all) if self._all else None

    def __bool__(self) -> bool:
        return bool(self._all)

    @property
    def size(self) -> int:
        return len(self.available)

    @property
    def available(self) -> list[str]:
        return [p for p in self._all if self._failures.get(p, 0) < self._max_failures]

    def next(self) -> str | None:
        """Next usable proxy, or None when no proxies were configured at all."""
        with self._lock:
            if self._cycle is None:
                return None
            usable = self.available
            if not usable:
                raise NoProxyAvailable(
                    f"all {len(self._all)} configured proxies are quarantined after "
                    f"{self._max_failures} consecutive failures each"
                )
            for _ in range(len(self._all) * 2):
                candidate = next(self._cycle)
                if candidate in usable:
                    return candidate
            return usable[0]

    def report_failure(self, proxy: str | None) -> None:
        if not proxy:
            return
        with self._lock:
            self._failures[proxy] = self._failures.get(proxy, 0) + 1

    def report_success(self, proxy: str | None) -> None:
        if not proxy:
            return
        with self._lock:
            self._failures.pop(proxy, None)


def as_curl_proxies(proxy: str | None) -> dict[str, str] | None:
    """curl_cffi wants a {scheme: url} mapping."""
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def as_playwright_proxy(proxy: str | None) -> dict[str, str] | None:
    """Playwright wants {server, username, password} with credentials split out."""
    if not proxy:
        return None
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    if not parts.hostname:
        return {"server": proxy}
    server = f"{parts.scheme or 'http'}://{parts.hostname}"
    if parts.port:
        server += f":{parts.port}"
    out = {"server": server}
    if parts.username:
        out["username"] = parts.username
    if parts.password:
        out["password"] = parts.password
    return out
