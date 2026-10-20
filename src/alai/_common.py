# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What several modules share: the error type the CLI reports, and what reading ``alai.toml`` needs in both ``_config``
and ``_manifest``."""

from __future__ import annotations

import collections.abc as _c
import re

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
"""Index and group names: lowercase, so each is spelled one way in files and on the command line."""


class Error(Exception):
    """A failure to report to the user as ``error: <message>``, without a traceback."""


class ConfigError(Error):
    """A config file, environment variable or flag holds an invalid value."""


class Record:
    """A value class with equality and repr from its ``__slots__``.

    Used instead of dataclasses, which cost ~3.5 ms to import and apply on every command.
    """

    __slots__: tuple[str, ...] = ()

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and all(getattr(self, n) == getattr(other, n) for n in self.__slots__)

    def __repr__(self) -> str:
        fields = ", ".join(f"{n}={getattr(self, n)!r}" for n in self.__slots__)
        return f"{type(self).__name__}({fields})"


def as_table(value: object) -> dict[str, object] | None:
    # TOML tables always have string keys.
    return value if isinstance(value, dict) else None  # pyright: ignore[reportUnknownVariableType]


TRUE_WORDS = frozenset(("1", "true", "yes", "on"))
FALSE_WORDS = frozenset(("0", "false", "no", "off"))
"""The words a boolean environment variable takes, in any case."""


def env_bool(environ: _c.Mapping[str, str], name: str) -> bool:
    value = (environ.get(name) or "").lower()
    if not value or value in FALSE_WORDS:
        return False
    if value in TRUE_WORDS:
        return True
    raise ConfigError(f"{name}: expected a boolean (1, true, yes, on, 0, false, no, off), got '{value}'")
