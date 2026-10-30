# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection``: install, uninstall and list collections by hand, outside the project's requirements and lock."""

from __future__ import annotations

import argparse

from .._args import (
    HelpFormatter,
    add_global_args,
    add_reinstall_args,
    add_settings_args,
    add_upgrade_args,
    reinstall_from_args,
    settings_from_args,
    upgrade_from_args,
)


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    collection = commands.add_parser(
        "collection",
        help="install, uninstall and list collections outside the project's requirements and alai.lock",
        description="Install, uninstall and list collections in the collections directory by hand, as uv pip does "
        + "for packages. Installing and uninstalling don't use or change the project's requirements or alai.lock, "
        + "though an alai.toml, if the project has one, marks the project's directory and its settings apply, such as "
        + "where collections are installed; the next alai sync puts the collections directory back as the lock has "
        + "it. Listing compares what's installed with alai.lock.",
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
    install.add_argument(
        "requirements",
        metavar="REQUIREMENT",
        nargs="*",
        help="a collection, such as ansible.posix or 'cisco.ios>=11'; a directory holding one, such as ../ns.col, "
        + "which is copied again every time; a built collection's tarball, such as ns-col-1.0.0.tar.gz or an "
        + "https:// URL to one; or a git repository, as git+URL[@REV][#subdirectory=DIR&version=VERSION]",
    )
    install.add_argument(
        "-e",
        "--editable",
        metavar="DIR",
        action="append",
        default=[],
        help="install the collection in DIR, a source or a built one, as a link to it, so edits there are seen; can be "
        + "repeated",
    )
    add_upgrade_args(install)
    add_reinstall_args(install)
    install.add_argument(
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
        description="Uninstall collections from the collections directory. What they depend on stays. A collection is "
        + "given by its name, even one installed from a directory, a tarball or a git repository. Outside a project, "
        + "there's no collections directory to use, so --collections-dir, or the collections_dir setting, has to say "
        + "where.",
        formatter_class=HelpFormatter,
    )
    uninstall.add_argument(
        "names",
        metavar="NAME",
        nargs="+",
        help="a collection name, such as ansible.posix, not the directory, tarball or git URL it was installed from",
    )
    add_global_args(uninstall, suppress=True)
    add_settings_args(uninstall, "install")
    uninstall.set_defaults(parser=uninstall, handler=run_uninstall)

    list_ = collection_commands.add_parser(
        "list",
        help="list the installed collections",
        description="List the collections in the collections directory, whatever installed them, and, when the "
        + "project has an alai.lock, how each compares with it: as locked, not in it, installed differently, or locked "
        + "but not installed. Outside a project, there's no collections directory to use, so --collections-dir, or the "
        + "collections_dir setting, has to say where.",
        formatter_class=HelpFormatter,
    )
    list_.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="text, the default, or json for scripts",
    )
    add_global_args(list_, suppress=True)
    add_settings_args(list_, "collections_dir")
    list_.set_defaults(parser=list_, handler=run_list)

    path = collection_commands.add_parser(
        "path",
        help="show where a collection's files are on disk",
        description="Show where each collection's files are on disk, for tools that read them, such as a "
        + "documentation viewer. alai uses the first copy it finds, checking the collection project's own checkout, "
        + "the collections directory, the version in alai.lock and the newest version in the cache, in that order. "
        + "If none of them has the collection, alai downloads the latest version from the indexes into the cache. "
        + "For ansible.builtin and "
        + "eda.builtin, it's the source of the latest ansible-core or ansible-rulebook release on the Python index. "
        + "With --output-format json, NAME can be left out to show only the project's own collection.",
        formatter_class=HelpFormatter,
    )
    _ = path.add_argument("names", metavar="NAME", nargs="*", help="a collection, such as community.general")
    _ = path.add_argument(
        "--no-download",
        action="store_true",
        help="only use what's already on disk, without downloading anything or checking the indexes for a newer "
        + "version",
    )
    _ = path.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="text, the default, or json for scripts",
    )
    add_global_args(path, suppress=True)
    add_settings_args(path, "resolve", "collections_dir")
    path.set_defaults(parser=path, handler=run_path)


def run_install(args: argparse.Namespace) -> int:
    from .._collection import install

    if not args.requirements and not args.editable:
        args.parser.error("the following arguments are required: REQUIREMENT or --editable")
    reinstall = reinstall_from_args(args)
    if reinstall and args.keep_unversioned:
        # Keeping a collection as it is and reinstalling it ask for opposite things.
        args.parser.error("--keep-unversioned can't be used with --reinstall or --reinstall-collection")
    install(
        settings_from_args(args),
        args.requirements,
        upgrade=upgrade_from_args(args).names(None),
        reinstall=reinstall,
        keep_unversioned=args.keep_unversioned,
        editable=args.editable,
    )
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    from .._collection import uninstall

    uninstall(settings_from_args(args), args.names)
    return 0


def run_list(args: argparse.Namespace) -> int:
    from .._list import list_collections

    list_collections(settings_from_args(args), output_format=args.output_format)
    return 0


def run_path(args: argparse.Namespace) -> int:
    from .._locate import collection_path

    if not args.names and args.output_format == "text":
        args.parser.error("the following arguments are required: NAME")
    return collection_path(
        settings_from_args(args), args.names, download=not args.no_download, output_format=args.output_format
    )
