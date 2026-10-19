# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Messages to stderr: warnings unless ``-q``, debug lines with ``-v``, trace lines with ``-vv``.

Not the logging module, which costs ~14 ms to import on every command.
"""

from __future__ import annotations

import sys

verbosity = 0
"""-1 with ``-q``, else how many times ``-v`` was given (at most 2)."""


def warn(message: str) -> None:
    if verbosity >= 0:
        print(f"warning: {message}", file=sys.stderr)


def debug(message: str) -> None:
    """What alai decided and why."""
    if verbosity >= 1:
        print(f"debug: {message}", file=sys.stderr)


def trace(message: str) -> None:
    """Each request and file operation."""
    if verbosity >= 2:
        print(f"trace: {message}", file=sys.stderr)
