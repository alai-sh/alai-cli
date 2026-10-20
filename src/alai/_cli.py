# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The ``alai`` command line: builds the parser from the command modules and runs the chosen command.

Commands import what they need inside their handler, so a command only pays the import cost of what it uses.
"""

from __future__ import annotations

import argparse
import collections.abc as _c
import os
import sys

from . import _output
from ._args import HelpFormatter, add_global_args, verbose_count
from ._commands import config, lock, shell_completion, sync
from ._common import Error

EXIT_ERROR = 2


def main(argv: _c.Sequence[str] | None = None) -> int:
    parser = _build_parser()
    if "_ARGCOMPLETE" in os.environ:
        # The shell runs alai on every Tab to ask for completions; this prints them and exits.
        _complete(parser)
    args = parser.parse_args(argv)
    verbose = verbose_count(args)
    if args.quiet and verbose:
        args.parser.error("-q/--quiet and -v/--verbose can't be used together")
    if verbose > 2:
        args.parser.error("-v/--verbose can be given at most twice (-vv)")
    _output.verbosity = -1 if args.quiet else verbose
    _output.progress = not args.no_progress
    _output.color = args.color

    if args.version:
        # Not argparse's version action, which needs the version up front: looking it up costs ~25 ms.
        from . import __version__

        print(f"alai {__version__}")
        return 0

    handler = getattr(args, "handler", None)
    if handler is None:
        # A command group given without a subcommand, or no command at all.
        args.parser.print_help(sys.stderr)
        return EXIT_ERROR

    try:
        return handler(args)
    except Error as e:
        _output.error(str(e))
        return EXIT_ERROR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alai", description="A fast package manager for Ansible collections.", formatter_class=HelpFormatter
    )
    _ = parser.add_argument("-V", "--version", action="store_true", help="show the alai version and exit")
    add_global_args(parser, suppress=False)
    parser.set_defaults(parser=parser)
    commands = parser.add_subparsers(title="commands", metavar="<command>", prog="alai")

    config.register(commands)
    lock.register(commands)
    shell_completion.register(commands)
    sync.register(commands)

    return parser


def _complete(parser: argparse.ArgumentParser) -> None:
    """Print the completions the shell asked for through argcomplete's environment variables, then exit."""
    from argcomplete import autocomplete
    from argcomplete.completers import DirectoriesCompleter

    def set_completers(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if action.metavar == "DIR":
                action.completer = DirectoriesCompleter()  # pyright: ignore[reportAttributeAccessIssue]
            elif isinstance(action, argparse._SubParsersAction):
                for subparser in action.choices.values():  # pyright: ignore[reportUnknownVariableType]
                    set_completers(subparser)

    set_completers(parser)
    # sys.exit rather than argcomplete's default os._exit, so exit handlers such as coverage's still run.
    autocomplete(parser, exit_method=sys.exit)
