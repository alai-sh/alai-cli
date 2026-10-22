# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What several modules share: the error type the CLI reports, and what reading ``alai.toml`` needs in both ``_config``
and ``_manifest``."""

from __future__ import annotations

import collections.abc as _c
import os
import re

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
"""Index and group names: lowercase, so each is spelled one way in files and on the command line."""


class Error(Exception):
    """A failure to report to the user as ``error: <message>``, without a traceback."""


MAX_THREADS = 4
"""The most extracting, and the most installing, at once, or one per CPU if there are fewer: both write to the disk,
and more writers only contend there. On a 16-thread machine with btrfs, sivel.acd cold took 6.09 s with 16 of each,
5.15 s with 4 and 5.05 s with 2, and 4 was the best warm and for the largest collections; on a 6-CPU VM with XFS, 2 and
3 beat 6. Only on tmpfs did more win (1.45 s with 16 against 1.65 s with 4). The details are in
design/performance.md."""


def worker_threads() -> int:
    """How many artifacts extract, and how many collections install, at once: see ``MAX_THREADS``."""
    return min(os.cpu_count() or 1, MAX_THREADS)


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
