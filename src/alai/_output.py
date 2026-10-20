# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Messages to stderr: status and warnings unless ``-q``, details and debug lines with ``-v``, trace lines with
``-vv``.

Not the logging module, which costs ~14 ms to import on every command.
"""

from __future__ import annotations

import sys

verbosity = 0
"""-1 with ``-q``, else how many times ``-v`` was given (at most 2)."""


def status(message: str) -> None:
    """What a command did, such as ``Resolved 11 collections in 1.2s``: on stderr, as it isn't the result."""
    if verbosity >= 0:
        print(message, file=sys.stderr)


def warn(message: str) -> None:
    if verbosity >= 0:
        print(f"warning: {message}", file=sys.stderr)


def detail(message: str) -> None:
    """Why the status line above happened, indented under it so it reads as part of it: shown with ``-v``."""
    if verbosity >= 1:
        print(f"  {message}", file=sys.stderr)


def debug(message: str) -> None:
    """What alai decided and why."""
    if verbosity >= 1:
        print(f"debug: {message}", file=sys.stderr)


def trace(message: str) -> None:
    """Each request and file operation."""
    if verbosity >= 2:
        print(f"trace: {message}", file=sys.stderr)
