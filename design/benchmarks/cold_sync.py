# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Time a cold ``alai sync`` under different configurations of environment variables.

Each run copies the project's ``alai.toml`` and ``alai.lock`` into a new directory under ``--work``, with an empty
cache there (``ALAI_CACHE_DIR``), flushes the filesystem, and runs ``alai sync``, so it downloads, extracts and installs
everything; with the lock it doesn't resolve, which is the network more than alai. It prints a Markdown table of each
configuration's prepare, install and total times, the median of ``--runs`` and the range. Put ``--work`` on the
filesystem being measured: the cache and the collections directory share it, as clones need. The results so far are
in ``design/performance.md``:

    uv run python design/benchmarks/cold_sync.py scratch/sivel --work /var/tmp/alai-bench
    uv run python design/benchmarks/cold_sync.py scratch/sivel --work ~/bench --runs 5 \\
        --config "hardlink: ALAI_LINK_MODE=hardlink"
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time

DEFAULT_CONFIGS = [
    "auto:",
    "clone: ALAI_LINK_MODE=clone",
    "hardlink: ALAI_LINK_MODE=hardlink",
    "copy: ALAI_LINK_MODE=copy",
]
"""What runs without ``--config``: each way of placing installed files, ``auto`` first."""

_PHASE = re.compile(r"^(Prepared|Installed) \d+ collections? in ([\d.]+)(ms|s)$")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    _ = parser.add_argument("project", help="a directory with alai.toml and alai.lock, such as scratch/sivel")
    _ = parser.add_argument("--work", required=True, help="where each run's project and cache go; it's emptied")
    _ = parser.add_argument("--runs", type=int, default=3, help="runs per configuration (default 3)")
    _ = parser.add_argument(
        "--config",
        action="append",
        help="'name: VAR=value VAR=value', repeatable; the default is a set comparing the switches",
    )
    _ = parser.add_argument(
        "--alai", default=f"{shlex.quote(sys.executable)} -m alai", help="the alai to run (default: this Python's)"
    )
    args = parser.parse_args()

    project, work = pathlib.Path(args.project), pathlib.Path(args.work)
    for name in ("alai.toml", "alai.lock"):
        if not (project / name).is_file():
            parser.error(f"{project} has no {name}")
    alai = shlex.split(args.alai)
    version = subprocess.run([*alai, "--version"], capture_output=True, text=True, check=True)  # noqa: S603 - alai
    _ = sys.stdout.write(f"```\n{version.stdout.strip()}\n```\n\n")
    _ = sys.stdout.write("| Configuration | Prepare | Install | Total |\n|---|---|---|---|\n")
    for config in args.config or DEFAULT_CONFIGS:
        label, _, assignments = config.partition(":")
        env = dict(os.environ)
        for assignment in shlex.split(assignments):
            key, _, value = assignment.partition("=")
            env[key] = value
        _ = sys.stderr.write(f"{label.strip()}: {args.runs} runs\n")
        runs = [_run([*alai, "sync"], project, work, env) for _ in range(args.runs)]
        row = " | ".join(_shown([r[i] for r in runs]) for i in range(3))
        _ = sys.stdout.write(f"| {label.strip()} | {row} |\n")
        _ = sys.stdout.flush()
    shutil.rmtree(work, ignore_errors=True)
    return 0


def _run(
    command: list[str], project: pathlib.Path, work: pathlib.Path, env: dict[str, str]
) -> tuple[float, float, float]:
    """One cold sync: prepare, install and total seconds."""
    shutil.rmtree(work, ignore_errors=True)
    run_dir = work / "project"
    run_dir.mkdir(parents=True)
    for name in ("alai.toml", "alai.lock"):
        _ = shutil.copy(project / name, run_dir)
    env = env | {"ALAI_CACHE_DIR": str(work / "cache")}
    # Nothing left from the last run, or from copying, waits to be written meanwhile.
    os.sync()
    started = time.monotonic()
    done = subprocess.run(command, cwd=run_dir, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    total = time.monotonic() - started
    if done.returncode != 0:
        sys.exit(f"alai sync failed:\n{done.stderr}")
    phases = {"Prepared": 0.0, "Installed": 0.0}
    for line in done.stdout.splitlines() + done.stderr.splitlines():
        if match := _PHASE.match(line.strip()):
            value = float(match[2])
            phases[match[1]] = value / 1000 if match[3] == "ms" else value
    return phases["Prepared"], phases["Installed"], total


def _shown(values: list[float]) -> str:
    median = statistics.median(values)
    return f"{median:.2f} s ({min(values):.2f} to {max(values):.2f})" if len(values) > 1 else f"{median:.2f} s"


if __name__ == "__main__":
    raise SystemExit(main())
