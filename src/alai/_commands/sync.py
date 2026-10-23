# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai sync``: make the collections directory hold exactly what ``alai.lock`` lists, locking first if needed."""

from __future__ import annotations

import argparse

from .._args import (
    HelpFormatter,
    add_check_args,
    add_global_args,
    add_group_args,
    add_reinstall_args,
    add_settings_args,
    add_upgrade_args,
    reinstall_from_args,
    selected_groups,
    settings_from_args,
    upgrade_from_args,
)
from .._common import EXIT_STALE


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sync = commands.add_parser(
        "sync",
        help="install the collections alai.lock lists, locking first if needed",
        description="Install the collections alai.lock lists into the collections directory, and remove those it "
        + "doesn't, so the directory matches the lock. alai.lock is written first if it's missing or alai.toml has "
        + "changed since. The project's collections are installed with the 'dev' group, if there is one.",
        formatter_class=HelpFormatter,
    )
    _ = add_check_args(sync)
    _ = sync.add_argument(
        "--inexact",
        action="store_true",
        help="keep installed collections alai.lock doesn't list, rather than removing them",
    )
    _ = add_group_args(sync)
    add_upgrade_args(sync, groups=True)
    add_reinstall_args(sync)
    add_global_args(sync, suppress=True)
    add_settings_args(sync, "resolve", "install")
    sync.set_defaults(parser=sync, handler=run)


def run(args: argparse.Namespace) -> int:
    from .._project import load_project
    from .._sync import sync_project

    settings = settings_from_args(args)
    raw, manifest = load_project(settings, "alai sync installs what it lists")
    project, groups = selected_groups(args, manifest.groups)
    synced = sync_project(
        settings,
        raw,
        manifest,
        project=project,
        groups=groups,
        upgrade=upgrade_from_args(args),
        locked=args.locked,
        frozen=args.frozen,
        exact=not args.inexact,
        reinstall=reinstall_from_args(args),
    )
    return EXIT_STALE if synced is None else 0
