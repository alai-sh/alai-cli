# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai lock``: resolve the project's collections and write ``alai.lock``."""

from __future__ import annotations

import argparse
import time

from .._args import (
    HelpFormatter,
    add_global_args,
    add_settings_args,
    add_upgrade_args,
    settings_from_args,
    upgrade_from_args,
)
from .._common import EXIT_STALE
from .._output import error, shown_path, status, summary


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lock = commands.add_parser(
        "lock",
        help="resolve the project's collections into alai.lock",
        description="Resolve the collections the project's alai.toml asks for, its groups included, to one version "
        + "of each, and write them to alai.lock beside it. Versions already in alai.lock are kept while they still "
        + "fit.",
        formatter_class=HelpFormatter,
    )
    lock.add_argument(
        "--check",
        action="store_true",
        help="resolve, but don't write alai.lock; exit 1 if it's missing or would change",
    )
    add_upgrade_args(lock, groups=True)
    add_global_args(lock, suppress=True)
    add_settings_args(lock, "resolve")
    lock.set_defaults(parser=lock, handler=run)


def run(args: argparse.Namespace) -> int:
    from .._lock import lock_path, read_lock, write_lock
    from .._project import load_project, lock_unchanged, report_changes, resolve_project

    started = time.monotonic()
    settings = settings_from_args(args)
    raw, manifest = load_project(settings, "alai lock resolves the collections it lists")
    path = lock_path(settings.project.root)
    existing = read_lock(path)

    if args.check and existing is None:
        error(f"there's no {shown_path(path)}; run alai lock")
        return EXIT_STALE

    lock, resolution = resolve_project(settings, manifest, raw, existing, upgrade=upgrade_from_args(args))
    summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)
    report_changes(existing, manifest, lock, resolution, inputs=args.check)
    if args.check:
        if not lock_unchanged(existing, manifest, lock):
            error(f"{shown_path(path)} is out of date; run {_relock(args)}")
            return EXIT_STALE
        status(f"{shown_path(path)} is up to date")
        return 0
    write_lock(path, lock)
    return 0


def _relock(args: argparse.Namespace) -> str:
    """The ``alai lock`` that writes what ``--check`` found, with the upgrade options it was given."""
    words = ["alai lock"]
    if args.upgrade:
        words.append("--upgrade")
    words += [f"--upgrade-collection {name}" for name in args.upgrade_collection]
    words += [f"--upgrade-group {name}" for name in args.upgrade_group]
    return " ".join(words)
