# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Record the terminal recordings in the CLI reference as asciinema files, ``docs/casts/<name>.cast``.

Each recording in ``scripts/cli_recordings.toml`` runs in a new project directory against a local index, in a
pseudo-terminal, so alai colours its output and draws its progress as it would for a user. The commands are typed
out at a steady pace, then run, keeping the time each one really takes. Run from the repository root, with alai
installed in the environment:

    python scripts/cli_recordings.py [NAME ...]
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
            selected = [r for r in recordings if not names or r["name"] in names]
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
    home = pathlib.Path(tempfile.mkdtemp(prefix="alai-cast-")).resolve()
    try:
        cast = _record(recording, home, _environment(home, index, bin_dir))
    finally:
        shutil.rmtree(home)
    _ = (OUTPUT / f"{recording['name']}.cast").write_text(cast, encoding="utf-8")
    return str(recording["name"])


def _environment(home: pathlib.Path, index: str, bin_dir: pathlib.Path) -> dict[str, str]:
    """A clean environment for alai, with nothing from the user's: an empty home, cache and config."""
    return {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "XDG_CONFIG_DIRS": str(home / ".xdg"),
        "ALAI_DEFAULT_INDEX": index,
        "LANG": "C.UTF-8",
        "TERM": "xterm-256color",
    }


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

    events: list[tuple[float, str]] = []
    clock = 0.0
    cwd = project
    for command in _as_list(recording.get("commands")):
        events.append((clock, _prompt(cwd, home)))
        for char in command:
            clock += TYPING
            events.append((clock, char))
        clock += BEFORE_ENTER
        events.append((clock, "\r\n"))
        args = shlex.split(command)
        if args[0] == "cd":
            # Each command runs in a shell of its own, so a cd is followed here and shown by the next prompt.
            cwd = (cwd / args[1]).resolve()
            if not cwd.is_dir() or not cwd.is_relative_to(home):
                msg = f"{recording['name']}: {command!r} isn't a directory in the recording's home"
                raise RuntimeError(msg)
        output, status = ([], 0) if args[0] == "cd" else _run(command, cwd, env, columns)
        if status == 127:  # the shell couldn't find the command, rather than it failing on purpose
            msg = f"{recording['name']}: {command!r} wasn't found:\n{''.join(data for _, data in output)}"
            raise RuntimeError(msg)
        for offset, data in output:
            events.append((clock + offset, data))
        clock = events[-1][0] + AFTER_OUTPUT
    events.append((clock, _prompt(cwd, home)))

    rows = _rows_used("".join(data for _, data in events), columns)
    if rows > ROWS:
        msg = f"{recording['name']}: needs {rows} rows, more than the {ROWS} recorded; shorten it or raise ROWS"
        raise RuntimeError(msg)
    header = {
        "version": 2,
        "width": columns,
        "height": rows,
        "title": recording["title"],
        "env": {"TERM": env["TERM"], "SHELL": "/bin/sh"},
    }
    lines = [json.dumps(header)]
    lines += [json.dumps([round(t, 3), "o", data], ensure_ascii=False) for t, data in events]
    return "\n".join(lines) + "\n"


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


_TERMINAL_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.|\r|\n|[^\x1b\r\n]")


def _rows_used(output: str, columns: int) -> int:
    """The rows the output reaches as the terminal draws it: following newlines, lines wider than ``columns`` wrapping
    onto the next row, and the cursor moving up as progress redraws. Escape sequences, links (OSC 8) included, take no
    room."""
    row = column = deepest = 0
    for match in _TERMINAL_RE.finditer(output):
        text = match.group()
        if match.group(2) == "A":
            row = max(0, row - int(match.group(1) or 1))
        elif text == "\r":
            column = 0
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


def _as_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
