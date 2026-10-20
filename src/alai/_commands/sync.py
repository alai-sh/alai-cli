# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai sync``: make the collections directory hold exactly what ``alai.lock`` lists, locking first if needed."""

from __future__ import annotations

import argparse
import time

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .._output import error, shown_path, summary
from .lock import EXIT_STALE


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sync = commands.add_parser(
        "sync",
        help="install the collections alai.lock lists, locking first if needed",
        description="Install the collections alai.lock lists into the collections directory, and remove those it "
        + "doesn't, so the directory matches the lock. alai.lock is written first if it's missing or alai.toml has "
        + "changed since. The project's collections are installed with the 'dev' group, if there is one.",
        formatter_class=HelpFormatter,
    )
    check = sync.add_mutually_exclusive_group()
    _ = check.add_argument(
        "--locked",
        action="store_true",
        help="resolve, and exit 1 rather than install if alai.lock is missing or would change",
    )
    _ = check.add_argument(
        "--frozen",
        action="store_true",
        help="install from alai.lock as it is, without checking it against alai.toml",
    )
    _ = sync.add_argument(
        "--inexact",
        action="store_true",
        help="keep installed collections alai.lock doesn't list, rather than removing them",
    )
    groups = sync.add_argument_group("groups")
    _ = groups.add_argument(
        "--group", metavar="NAME", action="append", default=[], help="also install group NAME; can be repeated"
    )
    _ = groups.add_argument(
        "--no-group",
        metavar="NAME",
        action="append",
        default=[],
        help="don't install group NAME, even if another option asks for it; can be repeated",
    )
    _ = groups.add_argument(
        "--only-group",
        metavar="NAME",
        action="append",
        default=[],
        help="install only group NAME, without the project's own collections; can be repeated",
    )
    _ = groups.add_argument("--all-groups", action="store_true", help="install every group")
    _ = groups.add_argument("--no-default-groups", action="store_true", help="don't install the 'dev' group")
    _ = groups.add_argument("--no-dev", action="store_true", help="the same as --no-group dev")
    _ = groups.add_argument("--only-dev", action="store_true", help="the same as --only-group dev")
    add_global_args(sync, suppress=True)
    add_settings_args(sync, "resolve", "install")
    sync.set_defaults(parser=sync, handler=run)


def run(args: argparse.Namespace) -> int:
    from .._lock import lock_path, read_lock, stale, write_lock
    from .._project import load_project, lock_unchanged, resolve_project
    from .._sync import install_locked, selected_groups

    started = time.monotonic()
    settings = settings_from_args(args)
    raw, manifest = load_project(settings, "alai sync installs what it lists")
    project, groups = selected_groups(args, manifest)
    path = lock_path(settings.project.root)
    existing = read_lock(path)

    if (args.locked or args.frozen) and existing is None:
        flag = "--locked" if args.locked else "--frozen"
        error(f"there's no {shown_path(path)}, and {flag} was given; run alai lock")
        return EXIT_STALE
    if args.frozen and existing is not None:
        lock = existing
    elif args.locked or existing is None or stale(existing, manifest, {i.url for i in settings.indexes}):
        lock, _ = resolve_project(settings, manifest, raw, existing)
        summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)
        if args.locked:
            if not lock_unchanged(existing, manifest, lock):
                error(f"{shown_path(path)} is out of date, and --locked was given; run alai lock")
                return EXIT_STALE
        else:
            _ = write_lock(path, lock)
    else:
        lock = existing
        summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)

    install_locked(settings, lock.select(groups, project=project), exact=not args.inexact)
    return 0
