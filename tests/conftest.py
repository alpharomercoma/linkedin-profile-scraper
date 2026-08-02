from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def public_html() -> str:
    return load("profile_public.html")


@pytest.fixture
def authwall_html() -> str:
    return load("authwall.html")


@pytest.fixture
def challenge_html() -> str:
    return load("challenge_arkose.html")
