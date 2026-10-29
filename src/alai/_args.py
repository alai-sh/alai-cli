# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What every command's parser shares: the help formatter, the global and settings options, the options several
commands take, and reading each of those into what the library takes.

Kept apart from ``_cli`` so the command modules can use it without importing ``_cli``, which imports them. The
library modules (``_sync``, ``_project``...) never see an argparse namespace: a command reads its options here and
passes values on.
"""

from __future__ import annotations

import argparse
import collections.abc as _c
import sys

from ._common import ConfigError, no_group
from ._config import SETTINGS, Overrides, SettingDefinition, Settings, load_settings
from ._output import terminal_width

# typing.TYPE_CHECKING without importing typing, which this module, loaded by every command, otherwise doesn't need.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from ._project import Upgrade


class HelpFormatter(argparse.HelpFormatter):
    """Gives argparse the terminal width, which it otherwise imports shutil (~2 ms) for on every add_argument."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, width=terminal_width() - 2)

    if sys.version_info[:2] == (3, 14):
        # 3.14 imports _colorize (~12 ms, with the dataclasses and inspect it pulls in) whenever a formatter is made,
        # including the one add_argument makes to check help strings. Defer it until help is actually formatted and
        # the theme is read. 3.15 imports it lazily itself.
        _pending_color: bool = True

        def _set_color(self, color: bool) -> None:
            self.__dict__.pop("_theme", None)
            self.__dict__.pop("_decolor", None)
            self._pending_color = color

        def __getattr__(self, name: str) -> object:
            if name in ("_theme", "_decolor"):
                getattr(argparse.HelpFormatter, "_set_color")(self, self._pending_color)  # noqa: B009
                return self.__dict__[name]
            raise AttributeError(name)


def add_global_args(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    """Options accepted before or after any subcommand.

    Each subcommand adds them again with suppressed defaults, so a value given before the subcommand isn't reset by
    the subcommand's own default.
    """

    def default(value: object) -> object:
        return argparse.SUPPRESS if suppress else value

    group = parser.add_argument_group("global options")
    _ = group.add_argument("--project", metavar="DIR", default=default(None), help="find the project from DIR")
    _ = group.add_argument("--config-file", metavar="PATH", default=default(None), help="read only this config file")
    _ = group.add_argument(
        "--no-config", action="store_true", default=default(False), help="don't read any config files"
    )
    for setting in SETTINGS:
        if setting.global_flag:
            _add_setting(group, setting, default=default(None))
    # A subcommand's options overwrite the parent's in the namespace, so each parser counts -v under its own dest and
    # verbose_count() adds them up: alai -v config show -v is -vv.
    _ = group.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        dest=f"verbose:{parser.prog}",
        help="show debug detail; -vv also traces each step",
    )
    _ = group.add_argument("-q", "--quiet", action="store_true", default=default(False), help="only show errors")
    _ = group.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=default("auto"),
        help="color the output: auto, the default, colors it on a terminal (env: NO_COLOR, FORCE_COLOR)",
    )
    _ = group.add_argument(
        "--no-progress",
        action="store_true",
        default=default(False),
        help="don't draw progress while working (env: ALAI_NO_PROGRESS)",
    )


def verbose_count(args: argparse.Namespace) -> int:
    """How many times -v was given, before and after any subcommand."""
    return sum(value for name, value in vars(args).items() if name.startswith("verbose:"))


def add_settings_args(parser: argparse.ArgumentParser, *groups: str) -> None:
    """The settings' options for a command: those in ``groups`` (``resolve``, ``install``) or named there (such as
    ``collections_dir``), or every one with none.

    A command only takes the flags it uses, so one it doesn't is an argparse error rather than silently ignored.
    """
    group = parser.add_argument_group("settings")
    for setting in SETTINGS:
        if not setting.global_flag and (not groups or setting.group in groups or setting.name in groups):
            _add_setting(group, setting, default=[] if setting.repeat else None)


def _add_setting(group: argparse._ArgumentGroup, setting: SettingDefinition, *, default: object) -> None:
    if setting.boolean:
        # --offline and --no-offline; unset (None) unless one is given.
        _ = group.add_argument(
            setting.flag, dest=setting.name, action=argparse.BooleanOptionalAction, default=default, help=setting.help
        )
        return
    _ = group.add_argument(
        setting.flag,
        dest=setting.name,
        metavar=setting.metavar,
        choices=setting.choices,
        action="append" if setting.repeat else "store",
        default=default,
        help=setting.help,
    )


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Load the settings, with the global and settings options as the command-line level."""
    settings = {s.name: getattr(args, s.name, None) for s in SETTINGS}
    return load_settings(
        Overrides(project=args.project, config_file=args.config_file, no_config=args.no_config, **settings)
    )


def add_upgrade_args(parser: argparse.ArgumentParser, *, groups: bool = False) -> None:
    """``-U`` and ``-P``, for every command that resolves, as uv has them on ``lock``, ``sync``, ``add`` and ``remove``;
    with ``groups``, for the commands that resolve the project, ``--upgrade-group`` too."""
    _ = parser.add_argument(
        "-U", "--upgrade", action="store_true", help="choose the newest versions that fit, ignoring alai.lock"
    )
    _ = parser.add_argument(
        "-P",
        "--upgrade-collection",
        metavar="NAME",
        action="append",
        default=[],
        help="choose the newest version of collection NAME that fits; can be repeated",
    )
    if groups:
        _ = parser.add_argument(
            "--upgrade-group",
            metavar="NAME",
            action="append",
            default=[],
            help="choose the newest version of every collection group NAME lists that fits; can be repeated",
        )


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
        usage_error(args, "argument --locked: not allowed with argument --frozen")


def usage_error(args: argparse.Namespace, message: str) -> None:
    """Exit as argparse does for options that can't go together, printing the usage and ``message``, so every such
    conflict reads the same, whether argparse or alai found it."""
    parser: argparse.ArgumentParser = args.parser
    parser.error(message)


def upgrade_from_args(args: argparse.Namespace) -> Upgrade:
    """What ``-U``, ``-P`` and ``--upgrade-group`` ask to upgrade; a usage error with ``--frozen``, which resolves
    nothing, on the commands that have it."""
    from ._manifest import collection_name
    from ._project import Upgrade

    try:
        collections = {collection_name(n) for n in args.upgrade_collection}
    except ValueError as e:
        raise ConfigError(f"--upgrade-collection: {e}") from e
    upgrade = Upgrade(everything=args.upgrade, collections=collections, groups=getattr(args, "upgrade_group", []))
    if upgrade and getattr(args, "frozen", False):
        usage_error(args, "--upgrade, --upgrade-collection and --upgrade-group resolve, which --frozen doesn't")
    return upgrade


def reinstall_from_args(args: argparse.Namespace) -> bool | set[str]:
    """What to reinstall: everything, the collections named, or nothing (False)."""
    from ._manifest import collection_name

    if args.reinstall:
        return True
    try:
        return {collection_name(n) for n in args.reinstall_collection} or False
    except ValueError as e:
        raise ConfigError(f"--reinstall-collection: {e}") from e


def selected_groups(args: argparse.Namespace, known: _c.Collection[str]) -> tuple[bool, list[str]]:
    """Whether the project's own collections are installed, and which of the groups ``known``, as uv chooses them:
    the default groups that exist, plus ``--group``, or every group with ``--all-groups``, or only
    ``--only-group``'s, less ``--no-group``."""
    from ._sync import DEFAULT_GROUPS

    def check(flag: str, names: _c.Iterable[str], *, defaults_ok: bool = False) -> None:
        # --no-group dev and --no-dev are fine without a dev group, so a CI job can always pass them.
        for name in sorted(set(names)):
            if name not in known and not (defaults_ok and name in DEFAULT_GROUPS):
                raise no_group(flag, name, known)

    check("--group", args.group)
    check("--only-group", args.only_group)
    check("--only-dev", ["dev"] if args.only_dev else [])
    check("--no-group", args.no_group, defaults_ok=True)
    excluded = {*args.no_group, *(["dev"] if args.no_dev else [])}
    only = {*args.only_group, *(["dev"] if args.only_dev else [])}
    if only:
        if args.group or args.all_groups:
            usage_error(args, "--only-group and --only-dev can't be used with --group or --all-groups")
        return False, sorted(only - excluded)
    if args.all_groups:
        chosen = set(known)
    else:
        chosen: set[str] = set() if args.no_default_groups else {g for g in DEFAULT_GROUPS if g in known}
        chosen |= set(args.group)
    return True, sorted(chosen - excluded)
