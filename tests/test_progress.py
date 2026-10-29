# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import io
import re
import time

import pytest

from alai import _output, _progress
from alai._cli import main
from alai._common import ConfigError
from alai._progress import Live, Task


class TestTask:
    def test_spinner(self) -> None:
        task = Task("Resolving collections...")
        task.show("ns.a", 3)

        assert task.lines(0, 80, 24, ascii_only=False) == ["⠋ Resolving collections... (3) ns.a"]

    def test_spinner_with_total_and_items(self) -> None:
        task = Task("Preparing collections...", total=4)
        task.start("ns.long_name", "downloading")
        task.start("ns.b", "extracting")
        task.end("ns.long_name")
        task.start("ns.c", "downloading")

        assert task.lines(1, 80, 24, ascii_only=False) == [
            "⠙ Preparing collections... (1/4)",
            # Extracting first; names padded to the longest seen, so the column doesn't move.
            "ns.b          extracting",
            "ns.c          downloading",
        ]

    def test_pause_hides_without_counting(self) -> None:
        task = Task("Preparing collections...", total=2)
        task.start("ns.a", "downloading")
        task.pause("ns.a")

        assert task.lines(0, 80, 24, ascii_only=False) == ["⠋ Preparing collections... (0/2)"]

    def test_bar(self) -> None:
        task = Task("Installing collections...", total=4, bar=True)
        task.advance("ns.a==1.0.0")

        assert task.lines(0, 80, 24, ascii_only=False) == ["█████░░░░░░░░░░░░░░░ [1/4] ns.a==1.0.0"]

    def test_ascii(self) -> None:
        task = Task("Installing collections...", total=2, bar=True)
        task.advance()

        assert task.lines(0, 80, 24, ascii_only=True) == ["##########.......... [1/2] Installing collections..."]

    def test_fits_the_terminal(self) -> None:
        task = Task("Preparing collections...", total=9)
        for i in range(9):
            task.start(f"ns.c{i}", "downloading")

        lines = task.lines(0, 20, 5, ascii_only=False)

        # The header and as many items as leave a line spare, each a column short of the width so none wraps.
        assert len(lines) == 4
        assert all(len(line) <= 19 for line in lines)


class FakeTerminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> FakeTerminal:
    """A terminal as stderr. Each test swaps it in itself (``use``): pytest's capturing sets ``sys.stderr`` again at
    the start of the test, after fixtures have run."""
    stream = FakeTerminal()
    monkeypatch.setattr(_output, "verbosity", 0)
    monkeypatch.setattr(_output, "progress", True)
    monkeypatch.delenv("ALAI_NO_PROGRESS", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(_progress, "_INTERVAL", 0.01)
    return stream


def use(terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stderr", terminal)


def visible(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\r", "", text)


def wait_drawn(terminal: FakeTerminal, text: str, *, after: int = 0, timeout: float = 10.0) -> None:
    """Wait until ``text`` has been drawn, after the first ``after`` characters written, rather than sleep a fixed
    time: the first frame is drawn one interval in, by a thread a busy CI runner can hold up for far longer."""
    deadline = time.monotonic() + timeout
    while text not in visible(terminal.getvalue()[after:]):
        if time.monotonic() > deadline:
            pytest.fail(f"{text!r} wasn't drawn within {timeout}s: {terminal.getvalue()!r}")
        time.sleep(0.005)


class TestLive:
    def test_draws_and_clears(self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch) -> None:
        use(terminal, monkeypatch)
        with Live(Task("Working...")) as task:
            task.show("ns.a", 1)
            wait_drawn(terminal, "Working... (1) ns.a")

        out = terminal.getvalue()
        assert out.startswith("\x1b[?25l")
        # The block is cleared and the cursor shown again at the end.
        assert out.endswith("\r\x1b[2K\x1b[?25h")

    def test_messages_go_above(self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch) -> None:
        use(terminal, monkeypatch)
        with Live(Task("Working...")):
            wait_drawn(terminal, "Working...")
            _output.warn("something")
            wait_drawn(terminal, "Working...", after=terminal.getvalue().index("warning: something\n"))

        out = terminal.getvalue()
        warned = out.index("warning: something\n")
        # Cleared just before the message, and drawn again after it.
        assert out[:warned].endswith("\r\x1b[2K")
        assert "Working..." in out[warned:]
        assert _output.live is None

    def test_quick_phase_draws_nothing(self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch) -> None:
        use(terminal, monkeypatch)
        monkeypatch.setattr(_progress, "_INTERVAL", 10)

        with Live(Task("Working...")):
            pass

        assert "Working" not in terminal.getvalue()

    @pytest.mark.parametrize(
        ("setup", "drawn"),
        [
            ({}, True),
            ({"verbosity": -1}, False),
            ({"progress": False}, False),
            ({"env": {"ALAI_NO_PROGRESS": "1"}}, False),
            ({"env": {"TERM": "dumb"}}, False),
        ],
    )
    def test_when_drawn(
        self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch, setup: dict[str, object], drawn: bool
    ) -> None:
        use(terminal, monkeypatch)
        for name in ("verbosity", "progress"):
            if name in setup:
                monkeypatch.setattr(_output, name, setup[name])
        for name, value in _env(setup).items():
            monkeypatch.setenv(name, value)

        with Live(Task("Working...")):
            if drawn:
                wait_drawn(terminal, "Working")
            else:
                time.sleep(0.05)  # long enough for several frames, had any been drawn

        assert ("Working" in terminal.getvalue()) is drawn

    def test_not_a_terminal(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(_progress, "_INTERVAL", 0.01)

        with Live(Task("Working...")):
            time.sleep(0.05)
            _output.status("done")

        assert capsys.readouterr().err == "done\n"

    def test_bad_env(self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch) -> None:
        use(terminal, monkeypatch)
        monkeypatch.setenv("ALAI_NO_PROGRESS", "maybe")

        with pytest.raises(ConfigError, match="ALAI_NO_PROGRESS: expected a boolean"):
            _ = Live(Task("Working..."))


def _env(setup: dict[str, object]) -> dict[str, str]:
    env = setup.get("env")
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def test_no_progress_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_output, "progress", True)

    _ = main(["--no-progress", "--version"])

    assert _output.progress is False


class TestColor:
    @pytest.mark.parametrize(
        ("flag", "env", "tty", "expected"),
        [
            ("always", {}, False, True),
            ("never", {"FORCE_COLOR": "1"}, True, False),
            ("auto", {}, True, True),
            ("auto", {}, False, False),
            ("auto", {"NO_COLOR": "1"}, True, False),
            ("auto", {"FORCE_COLOR": "1"}, False, True),
            ("auto", {"NO_COLOR": "1", "FORCE_COLOR": "1"}, False, False),
            ("auto", {"TERM": "dumb"}, True, False),
        ],
    )
    def test_when(
        self, monkeypatch: pytest.MonkeyPatch, flag: str, env: dict[str, str], tty: bool, expected: bool
    ) -> None:
        for name in ("NO_COLOR", "FORCE_COLOR"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("TERM", "xterm")
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr("sys.stderr", FakeTerminal() if tty else io.StringIO())
        monkeypatch.setattr(_output, "color", flag)

        assert _output.color_enabled() is expected

    def test_lines(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(_output, "color", "always")

        _output.summary("Resolved", 2, 0.01, dim=True)
        _output.summary("Installed", 1, 0.01)
        _output.change("+", "ns.a", "==1.0.0")
        _output.change("-", "ns.b", "==1.0.0")
        _output.change("~", "ns.c", "==1.0.0")
        _output.lock_change("Removed ns.b v1.0.0")
        _output.warn("careful")
        _output.error("broken")

        assert capsys.readouterr().err.splitlines() == [
            "\x1b[2mResolved\x1b[0m \x1b[1m2 collections\x1b[0m \x1b[2min 10ms\x1b[0m",
            "Installed \x1b[1m1 collection\x1b[0m \x1b[2min 10ms\x1b[0m",
            " \x1b[32m+\x1b[0m \x1b[1mns.a\x1b[0m\x1b[2m==1.0.0\x1b[0m",
            " \x1b[31m-\x1b[0m \x1b[1mns.b\x1b[0m\x1b[2m==1.0.0\x1b[0m",
            " \x1b[33m~\x1b[0m \x1b[1mns.c\x1b[0m\x1b[2m==1.0.0\x1b[0m",
            "\x1b[1;31mRemoved\x1b[0m ns.b v1.0.0",
            "\x1b[1;33mwarning\x1b[0m\x1b[1m:\x1b[0m \x1b[1mcareful\x1b[0m",
            "\x1b[1;31merror\x1b[0m\x1b[1m:\x1b[0m broken",
        ]

    def test_progress_cut_before_colouring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_output, "color", "always")
        task = Task("Resolving collections...")

        line = task.lines(0, 20, 24, ascii_only=False)[0]

        assert line == "\x1b[37m⠋\x1b[0m \x1b[2mResolving collect\x1b[0m"
        assert len(visible(line)) <= 19

    def test_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _ = main(["--color", "always", "--version"])

        assert _output.color == "always"
