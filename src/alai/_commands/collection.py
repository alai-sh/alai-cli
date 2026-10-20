# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection``: install and uninstall collections by hand, outside the project's requirements and lock."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .lock import add_upgrade_args, upgrade_from_args


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    collection = commands.add_parser(
        "collection",
        help="install or uninstall collections outside the project's requirements and alai.lock",
        description="Install or uninstall collections in the collections directory by hand, as uv pip does for "
        + "packages. The project's requirements and alai.lock aren't used or changed, though an alai.toml, if the "
        + "project has one, marks it and its settings apply; the next alai sync puts the directory back as the lock "
        + "has it.",
        formatter_class=HelpFormatter,
    )
    add_global_args(collection, suppress=True)
    collection.set_defaults(parser=collection)
    collection_commands = collection.add_subparsers(title="commands", metavar="<command>", prog="alai collection")

    install = collection_commands.add_parser(
        "install",
        help="install collections and what they need",
        description="Install collections and what they need into the collections directory, keeping installed "
        + "versions that fit. Nothing else is removed. Outside a project, --collections-dir says where.",
        formatter_class=HelpFormatter,
    )
    _ = install.add_argument(
        "requirements", metavar="REQUIREMENT", nargs="+", help="a collection, such as ansible.posix or 'cisco.ios>=11'"
    )
    add_upgrade_args(install)
    add_global_args(install, suppress=True)
    add_settings_args(install, "resolve", "install")
    install.set_defaults(parser=install, handler=run_install)

    uninstall = collection_commands.add_parser(
        "uninstall",
        help="uninstall collections",
        description="Uninstall collections from the collections directory. What they depend on stays.",
        formatter_class=HelpFormatter,
    )
    _ = uninstall.add_argument("names", metavar="NAME", nargs="+", help="a collection name, such as ansible.posix")
    add_global_args(uninstall, suppress=True)
    add_settings_args(uninstall, "install")
    uninstall.set_defaults(parser=uninstall, handler=run_uninstall)


def run_install(args: argparse.Namespace) -> int:
    from .._collection import install

    install(settings_from_args(args), args.requirements, upgrade=upgrade_from_args(args))
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    from .._collection import uninstall

    uninstall(settings_from_args(args), args.names)
    return 0
