"""Challenge / CAPTCHA detection and (optional, opt-in) solving.

LinkedIn's unauthenticated block ladder is: auth wall -> /checkpoint/challenge
-> Arkose Labs FunCaptcha. In practice the auth wall dominates and the remedy
is a better proxy, not a solver -- solving is the last resort, is disabled
unless a provider *and* API key are configured, and is capped per run so it
can't quietly spend money.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from curl_cffi import requests as cffi

from ..config import Settings

log = logging.getLogger(__name__)


@dataclass
class Challenge:
    kind: str  # authwall | arkose | recaptcha | checkpoint
    sitekey: str | None = None
    surl: str | None = None
    page_url: str | None = None

    @property
    def solvable(self) -> bool:
        return self.kind in ("arkose", "recaptcha") and bool(self.sitekey)


_ARKOSE_KEY_RE = re.compile(
    r"(?:public_?key|data-pkey|publicKey)[\"'\s:=]+([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})",
    re.I,
)
_ARKOSE_SURL_RE = re.compile(r"https://[\w.-]*arkoselabs\.com", re.I)
_RECAPTCHA_KEY_RE = re.compile(r"(?:data-sitekey|sitekey[\"'\s:=]+)[\"'\s:=]*([\w-]{30,})", re.I)


def detect_challenge(html: str, final_url: str = "") -> Challenge | None:
    """Classify an interstitial. Returns None for an ordinary page."""
    lowered = (html or "").lower()
    url_l = (final_url or "").lower()

    if "arkoselabs" in lowered or "funcaptcha" in lowered or "fc-iframe-wrap" in lowered:
        key = _ARKOSE_KEY_RE.search(html or "")
        surl = _ARKOSE_SURL_RE.search(html or "")
        return Challenge(
            kind="arkose",
            sitekey=key.group(1) if key else None,
            surl=surl.group(0) if surl else None,
            page_url=final_url,
        )

    if "recaptcha" in lowered and "g-recaptcha" in lowered:
        key = _RECAPTCHA_KEY_RE.search(html or "")
        return Challenge(kind="recaptcha", sitekey=key.group(1) if key else None, page_url=final_url)

    if "/checkpoint/" in url_l or "challenge-error-page" in lowered:
        return Challenge(kind="checkpoint", page_url=final_url)

    if "/authwall" in url_l or 'id="join-form"' in lowered:
        return Challenge(kind="authwall", page_url=final_url)

    return None


class SolverError(RuntimeError):
    pass


class Solver:
    """Base for CAPTCHA services. Implementations submit then poll."""

    name = "none"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def solve(self, challenge: Challenge) -> str:  # pragma: no cover - network
        raise NotImplementedError


class CapSolverClient(Solver):
    name = "capsolver"
    base = "https://api.capsolver.com"

    def solve(self, challenge: Challenge) -> str:  # pragma: no cover - network
        if challenge.kind == "arkose":
            task = {
                "type": "FunCaptchaTaskProxyLess",
                "websiteURL": challenge.page_url,
                "websitePublicKey": challenge.sitekey,
            }
            if challenge.surl:
                task["funcaptchaApiJSSubdomain"] = challenge.surl
        else:
            task = {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": challenge.page_url,
                "websiteKey": challenge.sitekey,
            }

        with cffi.Session(timeout=self.settings.timeout) as s:
            created = s.post(
                f"{self.base}/createTask",
                json={"clientKey": self.settings.captcha_api_key, "task": task},
            ).json()
            if created.get("errorId"):
                raise SolverError(created.get("errorDescription", "createTask failed"))
            task_id = created["taskId"]

            deadline = time.monotonic() + self.settings.captcha_timeout
            while time.monotonic() < deadline:
                time.sleep(3)
                res = s.post(
                    f"{self.base}/getTaskResult",
                    json={"clientKey": self.settings.captcha_api_key, "taskId": task_id},
                ).json()
                if res.get("errorId"):
                    raise SolverError(res.get("errorDescription", "getTaskResult failed"))
                if res.get("status") == "ready":
                    sol = res.get("solution", {})
                    token = sol.get("token") or sol.get("gRecaptchaResponse")
                    if not token:
                        raise SolverError("solver returned no token")
                    return token
        raise SolverError("solver timed out")


class TwoCaptchaClient(Solver):
    name = "2captcha"
    base = "https://2captcha.com"

    def solve(self, challenge: Challenge) -> str:  # pragma: no cover - network
        payload = {"key": self.settings.captcha_api_key, "json": 1, "pageurl": challenge.page_url}
        if challenge.kind == "arkose":
            payload |= {"method": "funcaptcha", "publickey": challenge.sitekey}
            if challenge.surl:
                payload["surl"] = challenge.surl
        else:
            payload |= {"method": "userrecaptcha", "googlekey": challenge.sitekey}

        with cffi.Session(timeout=self.settings.timeout) as s:
            created = s.post(f"{self.base}/in.php", data=payload).json()
            if created.get("status") != 1:
                raise SolverError(str(created.get("request", "in.php failed")))
            task_id = created["request"]

            deadline = time.monotonic() + self.settings.captcha_timeout
            while time.monotonic() < deadline:
                time.sleep(5)
                res = s.get(
                    f"{self.base}/res.php",
                    params={
                        "key": self.settings.captcha_api_key,
                        "action": "get",
                        "id": task_id,
                        "json": 1,
                    },
                ).json()
                if res.get("status") == 1:
                    return res["request"]
                if res.get("request") != "CAPCHA_NOT_READY":
                    raise SolverError(str(res.get("request")))
        raise SolverError("solver timed out")


_SOLVERS = {"capsolver": CapSolverClient, "2captcha": TwoCaptchaClient}


def get_solver(settings: Settings) -> Solver | None:
    """None means: detect challenges, report them, but never spend money."""
    if not settings.captcha_enabled:
        return None
    cls = _SOLVERS.get((settings.captcha_provider or "").lower())
    if cls is None:
        log.warning("unknown captcha provider %r; solving disabled", settings.captcha_provider)
        return None
    return cls(settings)
