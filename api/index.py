"""Vercel serverless entrypoint.

Vercel installs requirements.txt but not this project, so `src` goes on the
path directly. The browser path is disabled here: a Vercel Function has no
Chrome and no display, so only the curl_cffi path runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("LPS_BROWSER_ENABLED", "false")
os.environ.setdefault("LPS_STRATEGY", "http")
os.environ.setdefault("LPS_CACHE_DIR", "/tmp/lps-cache")

from lps.api import api as app  # noqa: E402

__all__ = ["app"]
