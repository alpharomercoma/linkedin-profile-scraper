"""On-disk raw HTML cache.

Exists so parser iteration never has to re-hit LinkedIn: fetch once, then
re-parse the saved page as many times as needed via `lps parse-file`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .base import FetchResult


def _slug(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


class RawCache:
    def __init__(self, root: Path, ttl: int = 86_400) -> None:
        self.root = Path(root)
        self.ttl = ttl

    def _paths(self, username: str, url: str) -> tuple[Path, Path]:
        base = self.root / username
        return base / f"{_slug(url)}.html", base / f"{_slug(url)}.json"

    def get(self, username: str, url: str) -> FetchResult | None:
        html_path, meta_path = self._paths(username, url)
        if not (html_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if self.ttl and time.time() - meta.get("saved_at", 0) > self.ttl:
            return None
        return FetchResult(
            url=meta.get("url", url),
            final_url=meta.get("final_url", url),
            status=meta.get("status", 200),
            html=html_path.read_text(errors="replace"),
            via="cache",
            notes=[f"cached at {meta.get('saved_at')}"],
        )

    def put(self, username: str, result: FetchResult) -> Path:
        html_path, meta_path = self._paths(username, result.url)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(result.html or "", errors="replace")
        meta_path.write_text(
            json.dumps(
                {
                    "url": result.url,
                    "final_url": result.final_url,
                    "status": result.status,
                    "via": result.via,
                    "size": result.size,
                    "saved_at": time.time(),
                },
                indent=2,
            )
        )
        return html_path
