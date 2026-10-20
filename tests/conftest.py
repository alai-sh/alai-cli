# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import pytest

from alai import _output


@pytest.fixture(autouse=True)
def no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plain output, whatever NO_COLOR or FORCE_COLOR the environment running the tests sets; colour tests ask."""
    monkeypatch.setattr(_output, "color", "never")
