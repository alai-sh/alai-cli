# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Record the terminal recordings in the docs as asciinema files, ``docs/casts/<name>.cast``.

Each recording in ``scripts/cli_recordings.toml`` runs in a new project directory against a local index, in a
pseudo-terminal, so alai colours its output and draws its progress as it would for a user. The commands are typed
out at a steady pace, then run, keeping the time each one really takes. Run from the repository root, with alai
installed in the environment:

    python scripts/cli_recordings.py [NAME ...]

A ``live`` recording runs against the real indexes, so it's only recorded when named. It's written to
``docs/assets/casts/<name>.cast``, to be committed, with an animated SVG of it in GitHub's light and dark palettes
for the README, ``<name>-light.svg`` and ``<name>-dark.svg``, made by asg (``cargo install asg --locked``).
"""

from __future__ import annotations

import codecs
import concurrent.futures
import fcntl
import json
import os
import pathlib
import pty
import re
import select
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time

import tomllib

sys.path.insert(0, "tests")

from artifact_data import build_artifact
from index_data import make_index, v
from index_server import IndexServer

SCENARIOS = pathlib.Path("scripts/cli_recordings.toml")
OUTPUT = pathlib.Path("docs/casts")
LIVE_OUTPUT = pathlib.Path("docs/assets/casts")
"""Where a live recording is written: it's committed, as it can't be made again on every build."""
COLUMNS = 80
"""The terminal's width while recording, unless a recording sets ``columns``. The player fits the recording to the
page's width, so fewer columns means larger text."""
ROWS = 48
"""The terminal's size while recording; the file's height is cut to the rows the recording uses. A recording that
needs more fails, as its first lines would scroll away before it ends."""

PROMPT = "\x1b[1;34m{cwd}\x1b[0m \x1b[1;32m$\x1b[0m "
"""The shell's prompt: the working directory, with the home directory as ~, as most shells show it."""
TYPING = 0.04
"""Seconds between each character typed."""
BEFORE_ENTER = 0.5
AFTER_OUTPUT = 1.5
"""Seconds to hold on each command's output before typing the next."""
IDLE_TIME_LIMIT = 1
"""The longest pause the player shows in a recording against the local index, in seconds. A live recording has none,
as how long its commands take is what it shows."""
LIVE_HOLD = 5.0
"""Seconds a live recording holds on how it ends, so its SVG can be read before it starts over."""

DOWNLOADS = "https://dl.alai.sh/sha256/{prefix}/{cksum}.tar.gz"
"""Where a version with a ``download`` checksum in ``[index]`` is fetched from: the real artifact, for a recording that
runs Ansible against it."""

# Seconds the index takes to answer, so the progress shows as it would over a network. Downloads take longest.
INDEX_DELAY = 0.15
DOWNLOAD_DELAY = 0.4


def main(names: list[str]) -> int:
    scenarios = tomllib.loads(SCENARIOS.read_text(encoding="utf-8"))
    recordings = scenarios["recording"]
    unknown = set(names) - {r["name"] for r in recordings}
    if unknown:
        _ = sys.stderr.write(f"no recording named {', '.join(sorted(unknown))} in {SCENARIOS}\n")
        return 1
    alai = shutil.which("alai")
    if alai is None:
        _ = sys.stderr.write("alai isn't installed in this environment\n")
        return 1
    # A live recording takes time and the network, and is committed, so it's only made when asked for by name.
    selected = [r for r in recordings if r["name"] in names or (not names and not r.get("live"))]
    if any(r.get("live") for r in selected) and shutil.which("asg") is None:
        _ = sys.stderr.write("a live recording needs asg to draw its SVGs: cargo install asg --locked\n")
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="alai-casts-") as temp:
        root = pathlib.Path(temp)
        collections = {name: [_version(s, root) for s in versions] for name, versions in scenarios["index"].items()}
        plugins = [{"v": 1, **line} for line in scenarios["plugins"]]
        _ = make_index(root / "index", collections, artifacts=True, plugins=plugins)
        with IndexServer(root / "index") as server:
            # The index points at its artifacts as file:// URLs; served over HTTP, they take time as downloads do.
            config = root / "index" / "config.json"
            settings = json.loads(config.read_text())
            settings["dl"] = server.url + "dl/{sha256-checksum}.tar.gz"
            _ = config.write_text(json.dumps(settings))
            server.delay = INDEX_DELAY
            server.delays = {f"/dl/{p.name}": DOWNLOAD_DELAY for p in (root / "index" / "dl").iterdir()}

            # Every recording has its own home and cache, so they run at once: most of each is spent waiting on alai
            # and the index's delays. They're written as they finish.
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as pool:
                jobs = [pool.submit(_write, r, server.url, pathlib.Path(alai).parent) for r in selected]
                for job in concurrent.futures.as_completed(jobs):
                    _ = sys.stderr.write(f"recorded {job.result()}\n")
    return 0


def _version(spec: dict[str, object], root: pathlib.Path) -> dict[str, object]:
    """An ``[index]`` version for ``make_index``: one with a ``download`` checksum gets that real artifact, fetched and
    checked against it, in place of one holding only a README."""
    version = v(str(spec["vers"]), spec.get("deps"))  # pyright: ignore[reportArgumentType]
    cksum = spec.get("download")
    if isinstance(cksum, str):
        import hashlib
        import urllib.request

        url = DOWNLOADS.format(prefix=cksum[:2], cksum=cksum)
        # Named, as the artifact store refuses urllib's default User-Agent.
        request = urllib.request.Request(url, headers={"User-Agent": "alai-docs-recordings"})  # noqa: S310 - a fixed https URL
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - a fixed https URL
            data: bytes = response.read()
        if hashlib.sha256(data).hexdigest() != cksum:
            msg = f"{url} doesn't match its sha256 {cksum}"
            raise RuntimeError(msg)
        tarball = root / f"{cksum}.tar.gz"
        _ = tarball.write_bytes(data)
        version["tarball"] = tarball
    return version


def _write(recording: dict[str, object], index: str, bin_dir: pathlib.Path) -> str:
    """Record one recording into its file, returning its name."""
    live = bool(recording.get("live"))
    home = pathlib.Path(tempfile.mkdtemp(prefix="alai-cast-")).resolve()
    try:
        cast = _record(recording, home, _environment(home, None if live else index, bin_dir))
    finally:
        shutil.rmtree(home)
    path = (LIVE_OUTPUT if live else OUTPUT) / f"{recording['name']}.cast"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(cast, encoding="utf-8")
    if live:
        for theme in ("light", "dark"):
            svg = path.with_name(f"{path.stem}-{theme}.svg")
            args = ["asg", "--theme", f"github-{theme}", "--padding", "16", path, svg]
            _ = subprocess.run(args, check=True)  # noqa: S603 - asg from PATH, checked in main, on our own files
    return str(recording["name"])


def _environment(home: pathlib.Path, index: str | None, bin_dir: pathlib.Path) -> dict[str, str]:
    """A clean environment for alai, with nothing from the user's: an empty home, cache and config. Without an
    ``index``, alai uses its default, the real one."""
    env = {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "XDG_CONFIG_DIRS": str(home / ".xdg"),
        "LANG": "C.UTF-8",
        "TERM": "xterm-256color",
    }
    if index is not None:
        env["ALAI_DEFAULT_INDEX"] = index
    return env


def _record(recording: dict[str, object], home: pathlib.Path, env: dict[str, str]) -> str:
    columns = recording.get("columns", COLUMNS)
    if not isinstance(columns, int):
        msg = f"{recording['name']}: columns must be an integer"
        raise TypeError(msg)
    project = home / str(recording.get("directory", "project"))
    project.mkdir()
    for path, content in _as_dict(recording.get("files")).items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(str(content).lstrip("\n"), encoding="utf-8")
    for path, spec in _as_dict(recording.get("tarballs")).items():
        _tarball(project / path, _as_dict(spec))
    for command in _as_list(recording.get("setup")):
        result = subprocess.run(  # noqa: S602 - the commands are the recording's own
            command, shell=True, cwd=project, env=env, capture_output=True, text=True, check=False
        )
        if result.returncode:
            msg = f"{recording['name']}: setup command {command!r} failed:\n{result.stdout}{result.stderr}"
            raise RuntimeError(msg)

    if "panes" in recording:
        events, rows = _record_panes(recording, project, home, env, columns)
    else:
        events: list[tuple[float, str]] = []
        clock = pause = 0.0
        cwd = project
        for command in _as_list(recording.get("commands")):
            events.append((clock, _prompt(cwd, home)))
            clock += pause
            for char in command:
                clock += TYPING
                events.append((clock, char))
            clock += BEFORE_ENTER
            events.append((clock, "\r\n"))
            cwd, output = _command(recording, command, cwd, home, env, columns)
            for offset, data in output or []:
                events.append((clock + offset, data))
            # The prompt shows as soon as the command finishes, and the next is typed after time to read its output,
            # or at once after a cd, which prints nothing.
            clock = events[-1][0]
            pause = 0.0 if output is None else AFTER_OUTPUT
        events += [(clock, _prompt(cwd, home)), (clock + pause, "")]
        rows = _rows_used("".join(data for _, data in events), columns)

    if rows > ROWS:
        msg = f"{recording['name']}: needs {rows} rows, more than the {ROWS} recorded; shorten it or raise ROWS"
        raise RuntimeError(msg)
    # asciicast v3, which asg needs for a live recording's SVGs: each event's time is from the one before it.
    header: dict[str, object] = {
        "version": 3,
        "term": {"cols": columns, "rows": rows, "type": env["TERM"]},
        "title": recording["title"],
        "env": {"SHELL": "/bin/sh"},
    }
    if recording.get("live"):
        # Held from the last thing drawn, in place of the usual hold on output.
        last = max(t for t, data in events if data)
        events = [(t, data) for t, data in events if data] + [(last + LIVE_HOLD, "")]
    else:
        header["idle_time_limit"] = IDLE_TIME_LIMIT
    lines = [json.dumps(header)]
    # Rounded from the start rather than each interval, so the rounding doesn't add up over the recording.
    previous = 0.0
    for t, data in events:
        lines.append(json.dumps([round(round(t, 3) - previous, 3), "o", data], ensure_ascii=False))
        previous = round(t, 3)
    return "\n".join(lines) + "\n"


def _command(
    recording: dict[str, object], command: str, cwd: pathlib.Path, home: pathlib.Path, env: dict[str, str], columns: int
) -> tuple[pathlib.Path, list[tuple[float, str]] | None]:
    """Run one of a recording's commands in ``cwd``, returning the directory the next runs in and what it wrote and
    when, or None for a ``cd``."""
    args = shlex.split(command)
    if args[0] == "cd":
        # Each command runs in a shell of its own, so a cd is followed here and shown by the next prompt.
        cwd = (cwd / args[1]).resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(home):
            msg = f"{recording['name']}: {command!r} isn't a directory in the recording's home"
            raise RuntimeError(msg)
        return cwd, None
    output, status = _run(command, cwd, env, columns)
    if status == 127:  # the shell couldn't find the command, rather than it failing on purpose
        msg = f"{recording['name']}: {command!r} wasn't found:\n{''.join(data for _, data in output)}"
        raise RuntimeError(msg)
    # The home directory is a temporary one, shown as ~ as in the prompt; ansible-galaxy prints its paths.
    return cwd, [(offset, data.replace(str(home), "~")) for offset, data in output]


def _record_panes(
    recording: dict[str, object], project: pathlib.Path, home: pathlib.Path, env: dict[str, str], columns: int
) -> tuple[list[tuple[float, str]], int]:
    """Record a split screen: each pane's commands in real time, below a rule with its title and a timer of how long its
    commands have been running, played as if the panes ran at once. Returns the recording's events and its rows."""
    panes = _as_dicts(recording["panes"])
    # Every pane's first command is entered at the same moment, however long it takes to type.
    enter = 0.5 + max(len(_as_list(pane.get("commands"))[0]) * TYPING for pane in panes) + BEFORE_ENTER
    # One after the other, each timed from its own start, so one pane's downloads don't slow another's.
    recorded = [_pane(recording, pane, project, home, env, columns, enter) for pane in panes]

    # Each pane is as tall as its output reaches, drawn on a screen too tall to scroll to find out.
    heights: list[int] = []
    for pane_events, _ in recorded:
        screen = _Screen(columns, 1000)
        for _, data in pane_events:
            screen.write(data)
        heights.append(screen.deepest + 1)
    tops = [sum(h + 1 for h in heights[:i]) for i in range(len(panes))]
    screens = [_Screen(columns, h) for h in heights]

    end = max(pane_events[-1][0] for pane_events, _ in recorded)
    timeline: list[tuple[float, int, str]] = [(t, i, data) for i, (pe, _) in enumerate(recorded) for t, data in pe]
    # Ticks redraw the bars: every tenth of a second, and as each command finishes, so a pane's timer stops on its
    # real total.
    ticks = {round(i / 10, 1) for i in range(int(end * 10) + 1)} | {e for _, busy in recorded for _, e in busy}
    timeline += [(t, -1, "") for t in ticks]
    timeline.sort(key=lambda entry: entry[0])

    def bars(t: float) -> list[str]:
        drawn = []
        for i, (pane, (_, busy)) in enumerate(zip(panes, recorded, strict=True)):
            running = sum(min(e, t) - s for s, e in busy if s < t)
            done = bool(busy) and t >= busy[-1][1]
            title = str(pane["title"])
            timer = f"{'✓ ' if done else ''}{running:.1f} s"
            # A dim rule with the title in blue and the timer, green once the pane's commands have all finished:
            # coloured text rather than a background, as each palette picks its colours to read on its own.
            rule = "─" * (columns - len(title) - len(timer) - 8)
            drawn.append(
                f"\x1b[{tops[i] + 1};1H\x1b[0;2m── \x1b[0;1;34m{title}\x1b[0;2m {rule} "
                f"\x1b[0;1{';32' if done else ''}m{timer}\x1b[0;2m ──\x1b[0m"
            )
        return drawn

    shown = bars(0.0)
    events: list[tuple[float, str]] = [(0.0, "\x1b[?25l\x1b[2J" + "".join(shown))]
    for t, i, data in timeline:
        if i < 0:
            now = bars(t)
            changed = "".join(bar for bar, was in zip(now, shown, strict=True) if bar != was)
            shown = now
        else:
            screens[i].write(data)
            changed = "".join(
                f"\x1b[{tops[i] + row + 2};1H\x1b[2K{screens[i].line(row)}" for row in sorted(screens[i].dirty)
            )
            screens[i].dirty.clear()
        if changed:
            events.append((t, changed))
    # Held on the last prompts, as a pane's last event changes nothing on screen.
    events.append((end, ""))
    return events, sum(h + 1 for h in heights)


def _pane(
    recording: dict[str, object],
    pane: dict[str, object],
    project: pathlib.Path,
    home: pathlib.Path,
    env: dict[str, str],
    columns: int,
    enter: float,
) -> tuple[list[tuple[float, str]], list[tuple[float, float]]]:
    """Type and run a pane's commands as the clock goes, returning what it showed and when, and when each of its
    commands, other than a cd, started and finished, all in seconds from when it started, its first command entered
    ``enter`` seconds in."""
    start = time.monotonic()

    def now() -> float:
        return time.monotonic() - start

    events: list[tuple[float, str]] = []
    busy: list[tuple[float, float]] = []
    cwd = (project / str(pane.get("directory", "."))).resolve()
    pause = 0.0
    for i, command in enumerate(_as_list(pane.get("commands"))):
        events.append((now(), _prompt(cwd, home)))
        time.sleep(max(0.0, enter - len(command) * TYPING - BEFORE_ENTER - now()) if i == 0 else pause)
        for char in command:
            time.sleep(TYPING)
            events.append((now(), char))
        time.sleep(BEFORE_ENTER)
        events.append((now(), "\r\n"))
        began = now()
        cwd, output = _command(recording, command, cwd, home, env, columns)
        if output is not None:
            busy.append((began, now()))
            events += [(began + offset, data) for offset, data in output]
        # As in a recording of one terminal: the prompt at once, then time to read the output before the next.
        pause = 0.0 if output is None else AFTER_OUTPUT
    events += [(now(), _prompt(cwd, home)), (now() + pause, "")]
    return events, busy


def _tarball(path: pathlib.Path, spec: dict[str, object]) -> None:
    """A built collection's tarball at ``path``, as ``ansible-galaxy collection build`` lays one out: ``name``,
    ``version``, ``dependencies`` for its ``MANIFEST.json``, and ``files``, its other files by relative path."""
    name, version = str(spec["name"]), str(spec["version"])
    namespace, short = name.split(".")
    files: dict[str, bytes | object] = {p: str(c).lstrip("\n").encode() for p, c in _as_dict(spec.get("files")).items()}
    info = {
        "namespace": namespace,
        "name": short,
        "version": version,
        "dependencies": _as_dict(spec.get("dependencies")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = build_artifact(path, name, version, files, manifest={"collection_info": info})  # pyright: ignore[reportArgumentType]


def _prompt(cwd: pathlib.Path, home: pathlib.Path) -> str:
    relative = cwd.relative_to(home) if cwd.is_relative_to(home) else None
    shown = str(cwd) if relative is None else "~" if relative == pathlib.Path() else f"~/{relative}"
    return PROMPT.format(cwd=shown)


def _run(command: str, cwd: pathlib.Path, env: dict[str, str], columns: int) -> tuple[list[tuple[float, str]], int]:
    """Run a command in a pseudo-terminal, returning what it wrote and when, in seconds from its start, and its exit
    status."""
    controller, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, columns, 0, 0))
    start = time.monotonic()
    process = subprocess.Popen(  # noqa: S602 - the commands are the recording's own
        command,
        shell=True,
        cwd=cwd,
        env=env,
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        start_new_session=True,
    )
    os.close(terminal)
    decoder = codecs.getincrementaldecoder("utf-8")()
    output: list[tuple[float, str]] = []
    try:
        while True:
            ready, _, _ = select.select([controller], [], [], 0.05)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            try:
                data = os.read(controller, 65536)
            except OSError:  # EIO: every process holding the terminal has exited
                break
            if not data:
                break
            if text := decoder.decode(data):
                output.append((time.monotonic() - start, text))
    finally:
        os.close(controller)
        status = process.wait()
    return output, status


_TERMINAL_RE = re.compile(
    r"\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.|\r|\n|\x08|[^\x1b\r\n\x08]"
)


class _Screen:
    """A pane of a split screen, drawn as a terminal draws what's written to it: text, colours, carriage returns,
    newlines, backspaces, and moving the cursor and clearing lines as progress redraws. Enough for alai's and
    ansible-galaxy's output; anything else, links (OSC 8) included, is dropped. Past its last row it scrolls."""

    def __init__(self, columns: int, rows: int) -> None:
        self.columns = columns
        self.rows = rows
        self.cells = [self._blank() for _ in range(rows)]
        self.row = self.column = self.deepest = 0
        self.style = ""
        self.dirty: set[int] = set()

    def _blank(self) -> list[tuple[str, str]]:
        return [(" ", "")] * self.columns

    def write(self, text: str) -> None:
        for match in _TERMINAL_RE.finditer(text):
            char, params, final = match.group(), match.group(1) or "", match.group(2)
            if final and not params.startswith("?"):
                count = int(params) if params.isdigit() else 1
                if final == "m":
                    # Each cell keeps the SGR parameters in effect, in order, from the last reset.
                    reset = params in ("", "0") or params.startswith("0;")
                    params = "" if params in ("", "0") else params.removeprefix("0;")
                    self.style = params if reset or not self.style else f"{self.style};{params}"
                elif final == "A":
                    self.row = max(0, self.row - count)
                elif final == "B":
                    self.row = min(self.rows - 1, self.row + count)
                elif final == "C":
                    self.column = min(self.columns - 1, self.column + count)
                elif final == "D":
                    self.column = max(0, self.column - count)
                elif final == "G":
                    self.column = min(self.columns - 1, count - 1)
                elif final == "K":
                    column = min(self.column, self.columns)
                    line = self.cells[self.row]
                    if params in ("", "0"):
                        line[column:] = [(" ", "")] * (self.columns - column)
                    elif params == "1":
                        line[: column + 1] = [(" ", "")] * (column + 1)
                    else:
                        line[:] = self._blank()
                    self.dirty.add(self.row)
            elif char == "\r":
                self.column = 0
            elif char == "\n":
                self._line_feed()
            elif char == "\b":
                self.column = max(0, min(self.column, self.columns) - 1)
            elif not char.startswith("\x1b") and ord(char) >= 32:
                # As xterm does, a line that fills the width only wraps when another character follows.
                if self.column == self.columns:
                    self.column = 0
                    self._line_feed()
                self.cells[self.row][self.column] = (char, self.style)
                self.column += 1
                self.dirty.add(self.row)

    def _line_feed(self) -> None:
        if self.row == self.rows - 1:
            _ = self.cells.pop(0)
            self.cells.append(self._blank())
            self.dirty.update(range(self.rows))
        else:
            self.row += 1
        self.deepest = max(self.deepest, self.row)

    def line(self, row: int) -> str:
        """The row as text, with SGR sequences where its colours change, and no blank cells at its end."""
        cells = self.cells[row]
        while cells and cells[-1] == (" ", ""):
            cells = cells[:-1]
        drawn: list[str] = []
        style = ""
        for char, cell in cells:
            if cell != style:
                drawn.append("\x1b[0m" + (f"\x1b[{cell}m" if cell else ""))
                style = cell
            drawn.append(char)
        return "".join(drawn) + ("\x1b[0m" if style else "")


def _rows_used(output: str, columns: int) -> int:
    """The rows the output reaches as the terminal draws it: following newlines, lines wider than ``columns`` wrapping
    onto the next row, the cursor moving up as progress redraws, and back over a backspace. Escape sequences, links
    (OSC 8) included, take no room."""
    row = column = deepest = 0
    for match in _TERMINAL_RE.finditer(output):
        text = match.group()
        if match.group(2) == "A":
            row = max(0, row - int(match.group(1) or 1))
        elif text == "\r":
            column = 0
        elif text == "\b":  # as ansible-galaxy's spinner draws each character over the last
            column = max(0, column - 1)
        elif text == "\n":
            row, column = row + 1, 0
        elif not text.startswith("\x1b"):
            # As xterm does, a line that fills the width only wraps when another character follows.
            if column == columns:
                row, column = row + 1, 0
            column += 1
        deepest = max(deepest, row)
    return deepest + 1


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_dicts(value: object) -> list[dict[str, object]]:
    return [_as_dict(item) for item in value] if isinstance(value, list) else []  # pyright: ignore[reportUnknownVariableType]


def _as_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
