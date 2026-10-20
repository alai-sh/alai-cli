# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai lock``: resolve the project's collections and write ``alai.lock``."""

from __future__ import annotations

import argparse
import sys

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args

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
    _ = lock.add_argument(
        "-U", "--upgrade", action="store_true", help="choose the newest versions that fit, ignoring alai.lock"
    )
    _ = lock.add_argument(
        "-P",
        "--upgrade-package",
        metavar="NAME",
        action="append",
        default=[],
        help="choose the newest version of collection NAME that fits; can be repeated",
    )
    add_global_args(lock, suppress=True)
    add_settings_args(lock, "resolve")
    lock.set_defaults(parser=lock, handler=run)


def run(args: argparse.Namespace) -> int:
    import time

    from .. import __version__
    from .._common import ConfigError
    from .._config import raw_manifest, read_toml
    from .._fetch import Fetcher
    from .._index import IndexClient
    from .._lock import Lock, changes, lock_path, read_lock, write_lock
    from .._manifest import collection_name, parse_manifest
    from .._output import detail, status, warn
    from .._resolve import resolve

    started = time.monotonic()
    settings = settings_from_args(args)
    project = settings.project
    if project.config_file is None:
        raise ConfigError(f"there's no alai.toml in {project.root}: alai lock resolves the collections it lists")
    raw = raw_manifest(read_toml(project.config_file), project.config_file)
    manifest = parse_manifest(raw)
    path = lock_path(project.root)
    existing = read_lock(path)

    if args.check and existing is None:
        print(f"error: there's no {_shown(path)}; run alai lock", file=sys.stderr)
        return EXIT_STALE

    try:
        upgrade: bool | set[str] = True if args.upgrade else {collection_name(n) for n in args.upgrade_package}
    except ValueError as e:
        raise ConfigError(f"--upgrade-package: {e}") from None

    with Fetcher(
        user_agent=f"alai/{__version__}", offline=settings.offline, system_certs=settings.system_certs
    ) as fetcher:
        clients = [IndexClient(index, fetcher, settings.cache_dir) for index in settings.indexes]
        resolution = resolve(manifest, clients, locked=existing, upgrade=upgrade)

    for message in resolution.warnings:
        warn(message)
    lock = Lock(raw, resolution.collections)
    status(f"Resolved {_count(len(lock.collections))} in {_elapsed(time.monotonic() - started)}")
    for name, line in changes(existing, lock):
        status(line)
        if (reason := resolution.reasons.get(name)) is not None:
            detail(reason)
    if args.check:
        if existing is None or not existing.is_current(manifest) or existing.collections != lock.collections:
            print(f"error: {_shown(path)} is out of date; run alai lock", file=sys.stderr)
            return EXIT_STALE
        status(f"{_shown(path)} is up to date")
        return 0
    _ = write_lock(path, lock)
    return 0


def _shown(path: str) -> str:
    """``path`` relative to the current directory when it's under it, as uv shows paths, else as it is."""
    import os

    relative = os.path.relpath(path)
    return path if relative.startswith("..") else relative


def _count(n: int) -> str:
    return f"{n} collection" if n == 1 else f"{n} collections"


def _elapsed(seconds: float) -> str:
    return f"{seconds * 1000:.0f}ms" if seconds < 1 else f"{seconds:.2f}s"
