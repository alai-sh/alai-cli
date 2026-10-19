# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai generate-shell-completion``: print the script that enables Tab completion."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter

SHELLS = ("bash", "zsh", "fish", "powershell", "tcsh")


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    completion = commands.add_parser(
        "generate-shell-completion",
        help="print the script that enables Tab completion in a shell",
        description="Print the script that enables Tab completion for alai in SHELL.",
        formatter_class=HelpFormatter,
    )
    _ = completion.add_argument("shell", choices=SHELLS, help="the shell to print the script for")
    completion.set_defaults(parser=completion, handler=generate)


def generate(args: argparse.Namespace) -> int:
    from argcomplete.shell_integration import shellcode

    print(shellcode(["alai"], shell=args.shell), end="")
    return 0
