# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai config``: inspect alai's settings."""

from __future__ import annotations

import argparse

from .. import _output
from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args
from .._common import ConfigError, home_path
from .._config import FILE_SETTINGS, SETTINGS, Level, Settings, suggest_key


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = commands.add_parser(
        "config", help="inspect alai's settings", description="Inspect alai's settings.", formatter_class=HelpFormatter
    )
    add_global_args(config, suppress=True)
    config.set_defaults(parser=config)
    config_commands = config.add_subparsers(title="commands", metavar="<command>", prog="alai config")

    show = config_commands.add_parser(
        "show",
        help="show the resolved settings and where they came from",
        description="Show the project, config files and settings alai resolves from the current directory. With "
        + "KEY, print only that setting's value, for scripts.",
        formatter_class=HelpFormatter,
    )
    _ = show.add_argument(
        "key", nargs="?", help="a setting named as in alai.toml, such as link_mode or project.collections_dir"
    )
    add_global_args(show, suppress=True)
    add_settings_args(show)
    show.set_defaults(parser=show, handler=show_settings)


def show_settings(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    if args.key is None:
        _show_all(settings, verbose=_output.verbosity >= 1)
    else:
        _show_key(settings, args.key)
    return 0


def _show_key(settings: Settings, key: str) -> None:
    """Print one setting's value as is, with no label, source or ``~``, so ``$(alai config show KEY)`` is the value."""
    if key == "tool" or key.startswith("tool."):
        raise ConfigError(f"'{key}' is in [tool], which alai never reads")
    setting = FILE_SETTINGS.get(key)
    if setting is None:
        raise ConfigError(f"unknown setting '{key}'{suggest_key(key)}")

    if setting.target == "indexes":
        # One per line in resolution order, as --index takes them, the default index last.
        for index in settings.indexes:
            print(f"{index.name}={index.url}")
    else:
        _output.debug(f"{key} from {settings.sources[setting.target]}")
        print(_text(getattr(settings, setting.target)))


def _show_all(settings: Settings, *, verbose: bool) -> None:
    project = settings.project
    root = home_path(project.root) + ("" if project.config_file else " (no alai.toml found)")
    _section("Project", [("root", root)])

    if settings.no_config:
        files = [("none", "--no-config given")]
    elif settings.searched and settings.searched[0].level is Level.EXPLICIT:
        files = [("--config-file", home_path(settings.searched[0].path))]
    else:
        files = []
        for level in (Level.PROJECT, Level.USER, Level.SYSTEM):
            read = [f.path for f in settings.searched if f.level is level and f.read]
            missing = [home_path(f.path) for f in settings.searched if f.level is level and not f.read]
            if read:
                value = home_path(read[0])
            elif verbose and missing:
                value = f"none, looked for {', '.join(missing)}"
            else:
                value = "none"
            files.append((level.value, value))
    _section("Config files, highest precedence first", files)

    rows: list[tuple[str, ...]] = []
    for setting in SETTINGS:
        if not setting.key or setting.many:
            continue
        try:
            value = _text(getattr(settings, setting.target), tilde=True)
        except ConfigError as e:
            # A default that can't be worked out here, such as cache_dir without a home directory.
            value = f"unknown: {e}"
        rows.append((setting.key, value, _source(settings, setting.target)))
        if verbose:
            for value, source in settings.overridden.get(setting.target, ()):
                rows.append(("", _text(value, tilde=True), f"{home_path(source)}, overridden"))
    _section("Settings", rows)

    indexes = [
        (index.name, index.url, f"{home_path(index.source)}{', default index' if index.default else ''}")
        for index in settings.indexes
    ]
    _section("Indexes, in resolution order", indexes)


def _section(title: str, rows: list[tuple[str, ...]]) -> None:
    """Print a heading and its rows with each column but the last padded to its widest value."""
    if title != "Project":
        print()
    print(title)
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row[:-1], widths, strict=True)]
        print("  " + "  ".join([*cells, row[-1]]).rstrip())


def _source(settings: Settings, target: str) -> str:
    return home_path(settings.sources[target])


def _text(value: object, *, tilde: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"  # as written in alai.toml
    text = str(getattr(value, "value", value))  # an enum's value, such as LinkMode.COPY's "copy"
    return home_path(text) if tilde else text
