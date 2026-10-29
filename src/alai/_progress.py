# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Live progress on stderr, as uv draws it: a spinner or bar for the current phase, with a line under it for each
item in flight, redrawn in place and cleared when the phase ends, for its summary line to take its place.

Only drawn on a terminal (see ``_output.progress_enabled``); everywhere else a ``Task`` just records its state. The
design is in ``design/output.md``.
"""

from __future__ import annotations

import sys
import threading

from . import _output

_INTERVAL = 0.1
"""Seconds between frames; the first is drawn one interval in, so a phase that ends sooner draws nothing."""
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_ASCII = "-\\|/"
_BAR_WIDTH = 20


class Task:
    """One phase's state: a title, how much is done of how much, what it's on, and the items in flight.

    Updated from any thread; ``Live`` reads it to draw.
    """

    def __init__(self, title: str, *, total: int | None = None, bar: bool = False) -> None:
        self.title = title
        self.total = total
        self.bar = bar
        """Drawn as a bar, ``███░░ [3/17] current``, rather than a spinner, ``⠙ title (3/17)``."""
        self.done = 0
        self.current: str | None = None
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}
        self._pad = 0
        """The longest name shown so far, so the state column stays put as names come and go."""

    def advance(self, current: str | None = None) -> None:
        with self._lock:
            self.done += 1
            if current is not None:
                self.current = current

    def show(self, current: str, done: int | None = None) -> None:
        """Say what the phase is on, and optionally how much is done so far."""
        with self._lock:
            self.current = current
            if done is not None:
                self.done = done

    def start(self, name: str, state: str) -> None:
        """Show ``name`` as in flight, in ``state`` such as ``downloading``, on a line of its own."""
        with self._lock:
            self._active[name] = state
            self._pad = max(self._pad, len(name))

    def pause(self, name: str) -> None:
        """Stop showing ``name``, which is waiting its turn rather than doing anything, without counting it done."""
        with self._lock:
            self._active.pop(name, None)

    def end(self, name: str) -> None:
        """``name`` is finished: it leaves the lines in flight and counts as done."""
        with self._lock:
            self._active.pop(name, None)
            self.done += 1

    def lines(self, frame: int, width: int, height: int, *, ascii_only: bool) -> list[str]:
        with self._lock:
            done, total, current, pad = self.done, self.total, self.current, self._pad
            # Extracting first: it's one at a time, so it's what the rest are waiting on.
            active = sorted(self._active.items(), key=lambda item: item[1] != "extracting")
        count = f"({done}/{total})" if total is not None else f"({done})"
        if self.bar and total:
            filled = min(_BAR_WIDTH, _BAR_WIDTH * done // total)
            full, empty = ("#", ".") if ascii_only else ("█", "░")
            mark, codes = f"{full * filled}{empty * (_BAR_WIDTH - filled)} [{done}/{total}]", ()
            text = current or self.title
        else:
            spinner = _SPINNER_ASCII if ascii_only else _SPINNER
            mark, codes = spinner[frame % len(spinner)], (_output.WHITE,)
            text = f"{self.title} {count}" + (f" {current}" if current else "")
        # One column short of the width, so no line wraps: a wrapped line would throw off moving back up over them.
        # Cut before colouring, so the escape codes don't count towards it.
        text = text[: max(0, width - 2 - len(mark))]
        lines = [f"{_output.styled(mark, *codes)} {_output.styled(text, _output.DIM)}"]
        lines.extend(f"{name:<{pad}}  {state}"[: width - 1] for name, state in active[: max(0, height - 2)])
        return lines


class Live:
    """Draws a ``Task`` on stderr until the ``with`` block ends, then clears it.

    Messages printed meanwhile through ``_output`` go above it: the block is cleared, the message written, and the
    block drawn again. When progress isn't enabled, nothing is drawn and messages print as usual.
    """

    def __init__(self, task: Task) -> None:
        self.task = task
        self._enabled = _output.progress_enabled()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drawn = 0
        """How many lines of the block are on screen now."""
        self._frame = 0
        self._ascii = not _encodes(_SPINNER + "█░")
        self._suspended = 0
        """How many ``Suspended`` blocks are running; nothing is drawn while any is."""

    def __enter__(self) -> Task:
        global _current
        if self._enabled:
            _output.live = self.print
            _current = self
            self._thread = threading.Thread(target=self._run, name="alai-progress", daemon=True)
            self._thread.start()
        return self.task

    def __exit__(self, *_: object) -> None:
        global _current
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._write(self._clear() + "\x1b[?25h")
            _output.live = None
            _current = None

    def print(self, text: str) -> None:
        """Write a message above the block."""
        with self._lock:
            self._write(self._clear() + text + "\n" + self._draw())

    def _run(self) -> None:
        with self._lock:
            self._write("\x1b[?25l")
        while not self._stop.wait(_INTERVAL):
            with self._lock:
                self._frame += 1
                self._write(self._clear() + self._draw())

    def suspend(self) -> None:
        with self._lock:
            self._suspended += 1
            # The cursor is shown again, at the start of a cleared line, for whatever writes to the terminal.
            self._write(self._clear() + "\x1b[?25h")

    def resume(self) -> None:
        with self._lock:
            self._suspended -= 1
            if not self._suspended:
                self._write("\x1b[?25l")

    def _draw(self) -> str:
        if not self._frame or self._suspended:
            return ""
        import shutil  # ~2 ms, so only once something is drawn

        size = shutil.get_terminal_size()
        lines = self.task.lines(self._frame, size.columns, size.lines, ascii_only=self._ascii)
        self._drawn = len(lines)
        # The cursor stays at the end of the last line, so nothing below the block is disturbed.
        return "\n".join(lines)

    def _clear(self) -> str:
        if not self._drawn:
            return ""
        drawn, self._drawn = self._drawn, 0
        return "\r\x1b[2K" + "\x1b[1A\x1b[2K" * (drawn - 1)

    @staticmethod
    def _write(data: str) -> None:
        if data:
            sys.stderr.write(data)
            sys.stderr.flush()


_current: Live | None = None
"""The ``Live`` drawing now, if any."""


class Suspended:
    """Stops the progress drawn now, if any, from being drawn until the ``with`` block ends, for something that may
    write to the terminal itself: ssh asking for a passphrase or to trust a host's key, which it writes to the
    terminal rather than to a stream alai could capture. Blocks in several threads at once each hold it off."""

    def __init__(self) -> None:
        self._live = _current

    def __enter__(self) -> None:
        if self._live is not None:
            self._live.suspend()

    def __exit__(self, *_: object) -> None:
        if self._live is not None:
            self._live.resume()


def _encodes(text: str) -> bool:
    try:
        # getattr, as something standing in for stderr, such as a full-screen app's capture of it, may have none.
        text.encode(getattr(sys.stderr, "encoding", None) or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True
