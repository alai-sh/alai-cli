# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Messages to stderr: status and warnings unless ``-q``, details and debug lines with ``-v``, trace lines with
``-vv``. While live progress is drawn (``_progress``), each message goes above it.

Not the logging module, which costs ~14 ms to import on every command.
"""

from __future__ import annotations

import collections.abc as _c
import os
import sys

from ._common import env_bool

verbosity = 0
"""-1 with ``-q``, else how many times ``-v`` was given (at most 2)."""
progress = True
"""False with ``--no-progress``; ``ALAI_NO_PROGRESS`` is read when progress is first asked for."""
color = "auto"
"""``--color``: ``auto``, ``always`` or ``never``."""

BOLD = "1"
DIM = "2"
ITALIC = "3"
UNDERLINE = "4"
RED = "31"
GREEN = "32"
YELLOW = "33"
BLUE = "34"
MAGENTA = "35"
CYAN = "36"
WHITE = "37"
live: _c.Callable[[str], None] | None = None
"""How the ``_progress.Live`` drawing now, if any, writes a message above itself."""


def terminal_width() -> int:
    """The terminal width as shutil.get_terminal_size() works it out, without importing shutil (~2 ms)."""
    try:
        columns = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        columns = 0
    if columns > 0:
        return columns
    try:
        return os.get_terminal_size(sys.__stdout__.fileno()).columns or 80  # pyright: ignore[reportOptionalMemberAccess]
    except (AttributeError, ValueError, OSError):
        return 80


def progress_enabled() -> bool:
    """Whether to draw live progress: on a terminal, unless ``-q``, ``--no-progress`` or ``TERM=dumb``."""
    if verbosity < 0 or not progress or not sys.stderr.isatty():
        return False
    return not env_bool(os.environ, "ALAI_NO_PROGRESS") and os.environ.get("TERM") != "dumb"


def color_enabled(*, stdout: bool = False) -> bool:
    """Whether to colour stderr, or stdout with ``stdout``: ``--color always`` or ``never`` if given; else not with
    ``NO_COLOR``, and always with ``FORCE_COLOR`` (``NO_COLOR`` winning if both are set), and otherwise on a terminal
    that isn't ``TERM=dumb``."""
    if color != "auto":
        return color == "always"
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return (sys.stdout if stdout else sys.stderr).isatty() and os.environ.get("TERM") != "dumb"


def styled(text: str, *codes: str, stdout: bool = False) -> str:
    """``text`` in the given styles (``BOLD``, ``GREEN``…), when colour is on for stderr, or stdout with ``stdout``."""
    if not text or not codes or not color_enabled(stdout=stdout):
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def _write(text: str) -> None:
    if live is not None:
        live(text)
    else:
        print(text, file=sys.stderr)


def status(message: str) -> None:
    """What a command did, such as ``Resolved 11 collections in 1.2s``: on stderr, as it isn't the result."""
    if verbosity >= 0:
        _write(message)


def error(message: str) -> None:
    """A failure, shown even with ``-q``."""
    _write(f"{styled('error', BOLD, RED)}{styled(':', BOLD)} {message}")


def warn(message: str) -> None:
    if verbosity >= 0:
        _write(f"{styled('warning', BOLD, YELLOW)}{styled(':', BOLD)} {styled(message, BOLD)}")


def summary(verb: str, count: int, seconds: float, *, dim: bool = False) -> None:
    """A phase's status line, as uv colours one: ``Installed`` **``5 collections``** ``in 88ms``, all but the count
    dim with ``dim``, for a phase that changed nothing (``Resolved``, ``Checked``)."""
    took = styled(f"in {elapsed(seconds)}", DIM)
    status(f"{styled(verb, DIM) if dim else verb} {styled(count_collections(count), BOLD)} {took}")


def change(sign: str, name: str, rest: str) -> None:
    """An install line, `` + ns.name==1.0.0``: the sign green, red, or yellow for ``~`` (reinstalled), the name bold
    and the rest dim."""
    colour = {"+": GREEN, "~": YELLOW}.get(sign, RED)
    status(f" {styled(sign, colour)} {styled(name, BOLD)}{styled(rest, DIM)}")


def lock_change(line: str) -> None:
    """A lock change line, ``Updated ns.name v1 -> v2``, its verb bold green, or red for ``Removed``."""
    verb, _, rest = line.partition(" ")
    status(f"{styled(verb, BOLD, RED if verb == 'Removed' else GREEN)} {rest}")


def detail(message: str) -> None:
    """Why the status line above happened, indented under it so it reads as part of it: shown with ``-v``."""
    if verbosity >= 1:
        _write(f"  {message}")


def debug(message: str) -> None:
    """What alai decided and why."""
    if verbosity >= 1:
        _write(f"debug: {message}")


def trace(message: str) -> None:
    """Each request and file operation."""
    if verbosity >= 2:
        _write(f"trace: {message}")


def shown_path(path: str) -> str:
    """``path`` relative to the current directory when it's under it, as uv shows paths, else as it is."""
    relative = os.path.relpath(path)
    return path if relative.startswith("..") else relative


def home_path(path: str) -> str:
    """``path`` with a leading home directory shortened to ``~``, for display."""
    home = os.path.expanduser("~")
    if home != "~" and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


def count_collections(n: int) -> str:
    """``1 collection``, ``2 collections``."""
    return f"{n} collection" if n == 1 else f"{n} collections"


def elapsed(seconds: float) -> str:
    """A duration as uv shows one: milliseconds under a second, else seconds to two places."""
    return f"{seconds * 1000:.0f}ms" if seconds < 1 else f"{seconds:.2f}s"
