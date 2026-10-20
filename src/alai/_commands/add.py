# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai add``: add collections to alai.toml, then lock and sync."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .lock import add_upgrade_args, upgrade_from_args


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    add = commands.add_parser(
        "add",
        help="add collections to alai.toml, then lock and sync",
        description="Add collections to the project's alai.toml, then lock and install them. A collection given by "
        + "name alone is written as >= the version locked for it. Every other line of alai.toml is left as it is.",
        formatter_class=HelpFormatter,
    )
    _ = add.add_argument(
        "requirements", metavar="REQUIREMENT", nargs="+", help="a collection, such as ansible.posix or 'cisco.ios>=11'"
    )
    add_edit_args(add, verb="add")
    add_global_args(add, suppress=True)
    add_settings_args(add, "resolve", "install")
    add.set_defaults(parser=add, handler=run)


def add_edit_args(parser: argparse.ArgumentParser, *, verb: str) -> None:
    """The options ``add`` and ``remove`` share."""
    where = parser.add_mutually_exclusive_group()
    _ = where.add_argument("--group", metavar="NAME", help=f"{verb} them in group NAME rather than the project")
    _ = where.add_argument("--dev", action="store_true", help="the same as --group dev")
    when = parser.add_mutually_exclusive_group()
    _ = when.add_argument("--no-sync", action="store_true", help="lock, but don't install")
    _ = when.add_argument("--frozen", action="store_true", help="only edit alai.toml, without locking or installing")
    add_upgrade_args(parser)


def run(args: argparse.Namespace) -> int:
    from .._edit import add

    settings = settings_from_args(args)
    group = "dev" if args.dev else args.group
    upgrade = upgrade_from_args(args)
    add(settings, args.requirements, group=group, sync=not args.no_sync, frozen=args.frozen, upgrade=upgrade)
    return 0
