# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Time ``alai sync`` against ``ansible-galaxy collection install`` installing the same collections.

The method and the last results are in ``design/performance.md``. Each run starts with a new, empty home directory, so
neither tool's cache carries over (ansible-galaxy keeps everything under ``~/.ansible``, alai under ``~/.cache/alai``),
and does, per tool:

1. a first install into a new project, ansible-galaxy clearing its response cache;
2. the same command again, with nothing to change;
3. a first install into a second project, keeping what the first cached.

alai runs with a committed ``alai.lock``, made once beforehand, and again without one, which resolves as ansible-galaxy
always does. ansible-core is installed into a temporary virtual environment with uv; alai is the one in the environment
this runs in, so run it from the repository with the working tree's alai installed. It talks to Galaxy and
index.alai.sh, so the results depend on both:

    uv run python design/benchmarks/ansible_galaxy.py community.general
    uv run python design/benchmarks/ansible_galaxy.py cisco.ios --runs 3 --ansible-core 2.20.0
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    _ = parser.add_argument("collections", nargs="+", help="the collections to install, such as community.general")
    _ = parser.add_argument("--runs", type=int, default=5, help="runs per tool, each from an empty home (default 5)")
    _ = parser.add_argument(
        "--ansible-core", default="2.20.0", help="the ansible-core version to time (default 2.20.0)"
    )
    _ = parser.add_argument(
        "--python", default="3.14", help="the Python for ansible-core, one that version supports (default 3.14)"
    )
    _ = parser.add_argument(
        "--no-cache", action="store_true", help="also time ansible-galaxy with its response cache off (--no-cache)"
    )
    args = parser.parse_args()

    alai = shutil.which("alai")
    if alai is None:
        _ = sys.stderr.write("alai isn't installed in this environment: run with uv run\n")
        return 1

    with tempfile.TemporaryDirectory(prefix="alai-bench-") as temp:
        base = pathlib.Path(temp)
        galaxy = _ansible_galaxy(base / "ansible-core", args.ansible_core, args.python)
        lock = _lock(base / "seed", alai, args.collections)
        install = [galaxy, "collection", "install", "-r", "requirements.yml", "-p", "./collections"]
        # Each: the commands for (first install, nothing to change, another project), and whether alai.lock is given.
        tools: dict[str, tuple[list[str], list[str], list[str], bool]] = {
            f"ansible-galaxy {args.ansible_core}": ([*install, "--clear-response-cache"], install, install, False),
            "alai, without a lock": ([alai, "sync"], [alai, "sync"], [alai, "sync"], False),
            "alai, with a lock": ([alai, "sync"], [alai, "sync"], [alai, "sync"], True),
        }
        if args.no_cache:
            off = [*install, "--no-cache"]
            tools[f"ansible-galaxy {args.ansible_core} --no-cache"] = (off, off, off, False)

        results: dict[str, tuple[list[float], list[float], list[float]]] = {}
        for name, (first, again, other, locked) in tools.items():
            times: tuple[list[float], list[float], list[float]] = ([], [], [])
            for run in range(args.runs):
                _ = sys.stderr.write(f"{name}: run {run + 1} of {args.runs}\n")
                home = base / f"home-{len(results)}-{run}"
                home.mkdir()
                project = _project(home / "first", args.collections, lock if locked else None)
                times[0].append(_timed(first, project, home))
                times[1].append(_timed(again, project, home))
                times[2].append(
                    _timed(other, _project(home / "other", args.collections, lock if locked else None), home)
                )
                shutil.rmtree(home)
            results[name] = times

    _ = sys.stdout.write(_table(args.collections, results))
    return 0


def _ansible_galaxy(venv: pathlib.Path, version: str, python: str) -> str:
    for command in (
        ["uv", "venv", "--quiet", "--python", python, str(venv)],
        ["uv", "pip", "install", "--quiet", "--python", str(venv), f"ansible-core=={version}"],
    ):
        _ = subprocess.run(command, check=True)  # noqa: S603 - uv, with the arguments given
    return str(venv / "bin" / "ansible-galaxy")


def _lock(home: pathlib.Path, alai: str, collections: list[str]) -> pathlib.Path:
    """An alai.lock for ``collections``, made once, as a project would have committed it."""
    project = _project(home / "project", collections, None)
    _ = _timed([alai, "lock", "--quiet"], project, home)
    return project / "alai.lock"


def _project(path: pathlib.Path, collections: list[str], lock: pathlib.Path | None) -> pathlib.Path:
    """A project asking for ``collections``, as both a requirements.yml and an alai.toml."""
    path.mkdir(parents=True)
    _ = (path / "requirements.yml").write_text("collections:\n" + "".join(f"  - name: {c}\n" for c in collections))
    listed = ", ".join(f'"{c}"' for c in collections)
    _ = (path / "alai.toml").write_text(f"[project]\ncollections = [{listed}]\n")
    if lock is not None:
        _ = shutil.copy(lock, path / "alai.lock")
    return path


def _timed(command: list[str], cwd: pathlib.Path, home: pathlib.Path) -> float:
    """Run ``command`` with ``home`` as the home directory and nothing else from this environment, timing it."""
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "LANG": "C.UTF-8", "NO_COLOR": "1", "ALAI_NO_PROGRESS": "1"}
    started = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    took = time.perf_counter() - started
    if result.returncode:
        raise SystemExit(f"{' '.join(command)} failed in {cwd}:\n{result.stdout}{result.stderr}")
    return took


def _table(collections: list[str], results: dict[str, tuple[list[float], list[float], list[float]]]) -> str:
    """The medians as a Markdown table, with each one's range, for design/performance.md."""

    def cell(times: list[float]) -> str:
        return f"{statistics.median(times):.2f} s ({min(times):.2f} to {max(times):.2f})"

    rows = [
        f"Installing {', '.join(collections)}: median (range) of {len(next(iter(results.values()))[0])} runs\n",
        "| | First install | Nothing to change | Another project, cached |",
        "|---|---|---|---|",
    ]
    rows += [
        f"| {name} | {cell(first)} | {cell(again)} | {cell(other)} |" for name, (first, again, other) in results.items()
    ]
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    sys.exit(main())
