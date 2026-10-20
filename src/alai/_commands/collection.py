# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection``: install and uninstall collections by hand, outside the project's requirements and lock."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .._common import ConfigError
from .lock import add_upgrade_args, upgrade_from_args
from .sync import add_reinstall_args, reinstall_from_args


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    collection = commands.add_parser(
        "collection",
        help="install or uninstall collections outside the project's requirements and alai.lock",
        description="Install or uninstall collections in the collections directory by hand, as uv pip does for "
        + "packages. The project's requirements and alai.lock aren't used or changed, though an alai.toml, if the "
        + "project has one, marks the project's directory and its settings apply, such as where collections are "
        + "installed; the next alai sync puts the collections directory back as the lock has it.",
        formatter_class=HelpFormatter,
    )
    add_global_args(collection, suppress=True)
    collection.set_defaults(parser=collection)
    collection_commands = collection.add_subparsers(title="commands", metavar="<command>", prog="alai collection")

    install = collection_commands.add_parser(
        "install",
        help="install collections and what they need",
        description="Install collections and what they need into the collections directory, keeping installed "
        + "versions that fit. Nothing else is removed. Outside a project, there's no collections directory to use, so "
        + "--collections-dir, or the collections_dir setting, has to say where.",
        formatter_class=HelpFormatter,
    )
    _ = install.add_argument(
        "requirements", metavar="REQUIREMENT", nargs="+", help="a collection, such as ansible.posix or 'cisco.ios>=11'"
    )
    add_upgrade_args(install)
    add_reinstall_args(install)
    _ = install.add_argument(
        "--keep-unversioned",
        action="store_true",
        help="leave a collection whose version can't be read, such as a source checkout with no MANIFEST.json, as "
        + "it is, as if it were the version asked for, rather than replace it",
    )
    add_global_args(install, suppress=True)
    add_settings_args(install, "resolve", "install")
    install.set_defaults(parser=install, handler=run_install)

    uninstall = collection_commands.add_parser(
        "uninstall",
        help="uninstall collections",
        description="Uninstall collections from the collections directory. What they depend on stays. Outside a "
        + "project, there's no collections directory to use, so --collections-dir, or the collections_dir setting, "
        + "has to say where.",
        formatter_class=HelpFormatter,
    )
    _ = uninstall.add_argument("names", metavar="NAME", nargs="+", help="a collection name, such as ansible.posix")
    add_global_args(uninstall, suppress=True)
    add_settings_args(uninstall, "install")
    uninstall.set_defaults(parser=uninstall, handler=run_uninstall)


def run_install(args: argparse.Namespace) -> int:
    from .._collection import install

    reinstall = reinstall_from_args(args)
    if reinstall and args.keep_unversioned:
        # Keeping a collection as it is and reinstalling it ask for opposite things.
        raise ConfigError("--keep-unversioned can't be used with --reinstall or --reinstall-collection")
    install(
        settings_from_args(args),
        args.requirements,
        upgrade=upgrade_from_args(args),
        reinstall=reinstall,
        keep_unversioned=args.keep_unversioned,
    )
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    from .._collection import uninstall

    uninstall(settings_from_args(args), args.names)
    return 0
