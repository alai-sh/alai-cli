# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai add``: add collections to alai.toml, then lock and sync."""

from __future__ import annotations

import argparse

from .._args import (
    HelpFormatter,
    add_edit_args,
    add_global_args,
    add_settings_args,
    check_locked,
    reinstall_from_args,
    settings_from_args,
    upgrade_from_args,
)
from .._common import EXIT_STALE


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    add = commands.add_parser(
        "add",
        help="add collections to alai.toml, then lock and sync",
        description="Add collections to the project's alai.toml, then lock and install them. A collection given by "
        + "name alone is written as >= the version locked for it. A directory, given with a / such as ./ns.col, is "
        + "the collection it holds, from its galaxy.yml or a built one's MANIFEST.json: its name is added, and "
        + "[project.sources] takes it from there. So is a built collection's tarball, by path, such as "
        + "ns-col-1.0.0.tar.gz, or by https:// URL, and a git repository, as "
        + "git+URL[@REV][#subdirectory=DIR&version=VERSION]. A directory given with --editable is linked to rather than "
        + "copied, so edits there are seen. Every other line of alai.toml is left as it is.",
        formatter_class=HelpFormatter,
    )
    add.add_argument(
        "requirements",
        metavar="REQUIREMENT",
        nargs="*",
        help="a collection, such as ansible.posix or 'cisco.ios>=11', a directory holding one, such as ../ns.col, "
        + "a built collection's tarball, by path or URL, or a git repository, such as "
        + "git+https://github.com/org/repo@v1.0.0",
    )
    add.add_argument(
        "--editable",
        metavar="DIR",
        action="append",
        default=[],
        help="add the collection in DIR as editable, linked to it rather than copied, so edits there are seen; can be "
        + "repeated",
    )
    add_edit_args(add, verb="add")
    add_global_args(add, suppress=True)
    add_settings_args(add, "resolve", "install")
    add.set_defaults(parser=add, handler=run)


def run(args: argparse.Namespace) -> int:
    from .._edit import add

    if not args.requirements and not args.editable:
        args.parser.error("the following arguments are required: REQUIREMENT or --editable")
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
