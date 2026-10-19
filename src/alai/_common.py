# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What reading ``alai.toml`` needs in both ``_config``, for settings, and ``_manifest``, for the manifest."""

from __future__ import annotations

import re

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
"""Index and group names: lowercase, so each is spelled one way in files and on the command line."""


class ConfigError(Exception):
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
