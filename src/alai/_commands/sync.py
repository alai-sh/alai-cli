# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai sync``: make the collections directory hold exactly what ``alai.lock`` lists, locking first if needed."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .._common import ConfigError
from .lock import EXIT_STALE, add_upgrade_args


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


def add_check_args(parser: argparse.ArgumentParser) -> argparse._MutuallyExclusiveGroup:
    """``--locked`` and ``--frozen``, for the commands that sync: ``sync`` and ``run``."""
    check = parser.add_mutually_exclusive_group()
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
    return check


def add_group_args(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    """The options choosing which groups to install, for the commands that sync: ``sync`` and ``run``."""
    groups = parser.add_argument_group("groups")
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
    return groups


def add_reinstall_args(parser: argparse.ArgumentParser) -> None:
    """``--reinstall`` and ``--reinstall-collection``, for every command that installs, as uv has them on ``sync``,
    ``add``, ``remove`` and ``pip install``."""
    _ = parser.add_argument(
        "--reinstall",
        action="store_true",
        help="reinstall every collection, even those already at the right version",
    )
    _ = parser.add_argument(
        "--reinstall-collection",
        metavar="NAME",
        action="append",
        default=[],
        help="reinstall collection NAME, even if it's already at the right version; can be repeated",
    )


def reinstall_from_args(args: argparse.Namespace) -> bool | set[str]:
    """What to reinstall: everything, the collections named, or nothing (False)."""
    from .._manifest import collection_name

    if args.reinstall:
        return True
    try:
        return {collection_name(n) for n in args.reinstall_collection} or False
    except ValueError as e:
        raise ConfigError(f"--reinstall-collection: {e}") from None


def run(args: argparse.Namespace) -> int:
    from .._sync import sync_project

    settings = settings_from_args(args)
    synced = sync_project(
        args,
        settings,
        "alai sync installs what it lists",
        exact=not args.inexact,
        reinstall=reinstall_from_args(args),
    )
    return EXIT_STALE if synced is None else 0
