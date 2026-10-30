# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai find``: find plugins, roles and playbooks by name in the indexes' plugin indexes."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args

PLUGIN_TYPES = (
    "become",
    "cache",
    "callback",
    "cliconf",
    "connection",
    "event_filter",
    "event_source",
    "filter",
    "httpapi",
    "inventory",
    "lookup",
    "module",
    "netconf",
    "playbook",
    "role",
    "rulebook",
    "shell",
    "strategy",
    "test",
    "vars",
)
"""The known types the plugin index spec lists, for Tab completion of ``-t``."""


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    find = commands.add_parser(
        "find",
        help="find which collections have a plugin, role or playbook",
        description="Find plugins, roles and playbooks by name in the indexes' plugin indexes, with the ones in "
        + "installed collections and ansible.builtin first. QUERY is part of a name (user finds win_user too), a glob "
        + "('*_info', 'community.general.*'), or a FQCN, which shows that plugin's details: where its redirects "
        + "lead, and how to install it.",
        formatter_class=HelpFormatter,
    )
    find.add_argument("query", metavar="QUERY", help="part of a name, a glob, or a FQCN")
    types = find.add_argument(
        "-t",
        "--type",
        metavar="TYPE",
        dest="types",
        action="append",
        default=[],
        help="only this type, as ansible-doc -t names them (module, filter, lookup...), or role or playbook; can be "
        + "repeated",
    )
    # Offered by Tab completion but not enforced: the index can add types without a format change.
    types.suggestions = PLUGIN_TYPES  # pyright: ignore[reportAttributeAccessIssue]
    find.add_argument("-x", "--exact", action="store_true", help="match whole names only, not names that contain QUERY")
    find.add_argument(
        "--installed",
        action="store_true",
        help="only plugins in installed collections and ansible.builtin",
    )
    find.add_argument(
        "-a",
        "--all",
        dest="include_all",
        action="store_true",
        help="list removed plugins and aliases to listed plugins that match part of a name too; ones named exactly "
        + "always are",
    )
    find.add_argument(
        "--limit",
        metavar="N",
        type=_count,
        help="list at most N matches, installed ones first, 0 for all; 20 on a terminal, all when "
        + "the output is piped or --output-format is json",
    )
    find.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="text, the default, or json for scripts",
    )
    add_global_args(find, suppress=True)
    add_settings_args(find, "resolve", "collections_dir")
    find.set_defaults(parser=find, handler=run)


def run(args: argparse.Namespace) -> int:
    from .._find import find
    from .._plugins import Query

    query = Query(args.query, exact=args.exact, types=args.types)
    if args.exact and query.glob:
        args.parser.error("-x/--exact can't be used with a glob")
    return find(
        settings_from_args(args),
        query,
        installed_only=args.installed,
        include_all=args.include_all,
        limit=args.limit,
        output_format=args.output_format,
    )


def _count(text: str) -> int:
    try:
        value = int(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"'{text}' isn't a whole number") from e
    if value < 0:
        raise argparse.ArgumentTypeError("can't be negative")
    return value
