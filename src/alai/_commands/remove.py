# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai remove``: remove collections from alai.toml, then lock and sync."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .add import add_edit_args, check_locked
from .lock import EXIT_STALE
from .sync import reinstall_from_args


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    remove = commands.add_parser(
        "remove",
        help="remove collections from alai.toml, then lock and sync",
        description="Remove collections from the project's alai.toml, then lock and sync, which uninstalls what "
        + "nothing needs any more. Every other line of alai.toml is left as it is.",
        formatter_class=HelpFormatter,
    )
    _ = remove.add_argument("names", metavar="NAME", nargs="+", help="a collection name, such as ansible.posix")
    add_edit_args(remove, verb="remove")
    add_global_args(remove, suppress=True)
    add_settings_args(remove, "resolve", "install")
    remove.set_defaults(parser=remove, handler=run)


def run(args: argparse.Namespace) -> int:
    from .._edit import remove
    from .._project import upgrade_from_args

    settings = settings_from_args(args)
    group = "dev" if args.dev else args.group
    check_locked(args)
    ok = remove(
        settings,
        args.names,
        group=group,
        sync=not args.no_sync,
        frozen=args.frozen,
        locked=args.locked,
        upgrade=upgrade_from_args(args),
        reinstall=reinstall_from_args(args),
    )
    return 0 if ok else EXIT_STALE
