# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What every command's parser shares: the help formatter and the global and settings options.

Kept apart from ``_cli`` so the command modules can use it without importing ``_cli``, which imports them.
"""

from __future__ import annotations

import argparse
import os
import sys

from ._config import SETTINGS, Overrides, SettingDefinition, Settings, load_settings


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


def terminal_width() -> int:
    """The terminal width as shutil.get_terminal_size() works it out."""
    try:
        columns = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        columns = 0
    if columns > 0:
        return columns
    try:
        return os.get_terminal_size(sys.__stdout__.fileno()).columns or 80  # pyright: ignore[reportOptionalMemberAccess]
    except (AttributeError, ValueError, OSError):
        return 80


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


def verbose_count(args: argparse.Namespace) -> int:
    """How many times -v was given, before and after any subcommand."""
    return sum(value for name, value in vars(args).items() if name.startswith("verbose:"))


def add_settings_args(parser: argparse.ArgumentParser) -> None:
    """Options for commands that resolve or install collections."""
    group = parser.add_argument_group("settings")
    for setting in SETTINGS:
        if not setting.global_flag:
            _add_setting(group, setting, default=[] if setting.repeat else None)


def _add_setting(group: argparse._ArgumentGroup, setting: SettingDefinition, *, default: object) -> None:
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
