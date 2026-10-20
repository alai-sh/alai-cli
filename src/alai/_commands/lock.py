# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai lock``: resolve the project's collections and write ``alai.lock``."""

from __future__ import annotations

import argparse
import time

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .._common import ConfigError
from .._output import detail, error, lock_change, shown_path, status, summary

EXIT_STALE = 1
"""``--check`` found the lock missing, or resolving would change it."""


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lock = commands.add_parser(
        "lock",
        help="resolve the project's collections into alai.lock",
        description="Resolve the collections the project's alai.toml asks for, its groups included, to one version "
        + "of each, and write them to alai.lock beside it. Versions already in alai.lock are kept while they still "
        + "fit.",
        formatter_class=HelpFormatter,
    )
    _ = lock.add_argument(
        "--check",
        action="store_true",
        help="resolve, but don't write alai.lock; exit 1 if it's missing or would change",
    )
    add_upgrade_args(lock)
    add_global_args(lock, suppress=True)
    add_settings_args(lock, "resolve")
    lock.set_defaults(parser=lock, handler=run)


def add_upgrade_args(parser: argparse.ArgumentParser) -> None:
    """``-U`` and ``-P``, for every command that locks, as uv has them on ``lock``, ``sync``, ``add`` and ``remove``."""
    _ = parser.add_argument(
        "-U", "--upgrade", action="store_true", help="choose the newest versions that fit, ignoring alai.lock"
    )
    _ = parser.add_argument(
        "-P",
        "--upgrade-collection",
        metavar="NAME",
        action="append",
        default=[],
        help="choose the newest version of collection NAME that fits; can be repeated",
    )


def upgrade_from_args(args: argparse.Namespace) -> bool | set[str]:
    """What to upgrade, for the resolver: everything, the collections named, or nothing (False)."""
    from .._manifest import collection_name

    if args.upgrade:
        return True
    try:
        return {collection_name(n) for n in args.upgrade_collection} or False
    except ValueError as e:
        raise ConfigError(f"--upgrade-collection: {e}") from None


def run(args: argparse.Namespace) -> int:
    from .._lock import changes, lock_path, read_lock, write_lock
    from .._project import load_project, lock_unchanged, resolve_project

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
    for name, line in changes(existing, lock):
        lock_change(line)
        if (reason := resolution.reasons.get(name)) is not None:
            detail(reason)
    if args.check:
        if not lock_unchanged(existing, manifest, lock):
            error(f"{shown_path(path)} is out of date; run alai lock")
            return EXIT_STALE
        status(f"{shown_path(path)} is up to date")
        return 0
    _ = write_lock(path, lock)
    return 0
