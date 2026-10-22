# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai add``: add collections to alai.toml, then lock and sync."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .lock import EXIT_STALE, add_upgrade_args
from .sync import add_reinstall_args, reinstall_from_args


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    add = commands.add_parser(
        "add",
        help="add collections to alai.toml, then lock and sync",
        description="Add collections to the project's alai.toml, then lock and install them. A collection given by "
        + "name alone is written as >= the version locked for it. A directory, given with a / such as ./ns.col, is "
        + "the collection it holds, from its galaxy.yml or a built one's MANIFEST.json: its name is added, and "
        + "[project.sources] takes it from there. So is a built collection's tarball, by path, such as "
        + "ns-col-1.0.0.tar.gz, or by https:// URL. Every other line of alai.toml is left as it is.",
        formatter_class=HelpFormatter,
    )
    _ = add.add_argument(
        "requirements",
        metavar="REQUIREMENT",
        nargs="+",
        help="a collection, such as ansible.posix or 'cisco.ios>=11', a directory holding one, such as ../ns.col, "
        + "or a built collection's tarball, by path or URL",
    )
    _ = add.add_argument(
        "--editable",
        action="store_true",
        help="add the directories given as editable, so the collection is linked to them rather than copied",
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
    _ = parser.add_argument(
        "--locked",
        action="store_true",
        help="resolve, and exit 1 without writing anything if alai.lock is missing or would change",
    )
    add_upgrade_args(parser, groups=True)
    add_reinstall_args(parser)


def check_locked(args: argparse.Namespace) -> None:
    """``--locked`` and ``--frozen`` exclude each other, as on ``sync``, but ``--no-sync`` and ``--frozen`` already
    share a group, which argparse can't extend to say so; this is its own error for it."""
    if args.locked and args.frozen:
        parser: argparse.ArgumentParser = args.parser
        parser.error("argument --locked: not allowed with argument --frozen")


def run(args: argparse.Namespace) -> int:
    from .._edit import add
    from .._project import upgrade_from_args

    settings = settings_from_args(args)
    group = "dev" if args.dev else args.group
    check_locked(args)
    ok = add(
        settings,
        args.requirements,
        group=group,
        sync=not args.no_sync,
        frozen=args.frozen,
        locked=args.locked,
        upgrade=upgrade_from_args(args),
        reinstall=reinstall_from_args(args),
        editable=args.editable,
    )
    return 0 if ok else EXIT_STALE
