# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import os
from pathlib import Path

import pytest
from index_data import make_index, v

from alai import _output


@pytest.fixture(autouse=True)
def no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's proxy and TLS settings out, or requests to 127.0.0.1 would go through them."""
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plain output, whatever NO_COLOR or FORCE_COLOR the environment running the tests sets; colour tests ask."""
    monkeypatch.setattr(_output, "color", "never")


COLLECTIONS: dict[str, list[dict[str, object]]] = {
    "a.a": [v("1.0.0"), v("2.0.0", {"b.b": ">=1.0.0"})],
    "b.b": [v("1.0.0"), v("1.1.0")],
    "c.c": [v("1.0.0")],
    "d.d": [v("1.0.0")],
}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def environ(home: Path, tmp_path: Path) -> dict[str, str]:
    """An environment with a home directory and, so the host's /etc/xdg is never read, an empty system config
    directory, for ``load_settings``."""
    xdg_dirs = tmp_path / "xdg"
    xdg_dirs.mkdir()
    return {"HOME": str(home), "XDG_CONFIG_DIRS": str(xdg_dirs)}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project directory as the working directory, with only a local index of real artifacts configured:
    ``COLLECTIONS``. A test module with a ``project`` fixture of its own uses that instead."""
    for name in [n for n in os.environ if n.startswith(("ALAI_", "XDG_"))]:
        monkeypatch.delenv(name)
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "xdg").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg"))
    monkeypatch.setenv("ALAI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "index", COLLECTIONS, artifacts=True))
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(_output, "verbosity", 0)
    return root
