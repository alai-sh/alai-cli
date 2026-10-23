# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai run``: sync the project, as ``uv run`` does, then replace alai with a command that sees only its collections."""

from __future__ import annotations

import argparse

from .._args import (
    HelpFormatter,
    add_check_args,
    add_global_args,
    add_group_args,
    add_reinstall_args,
    add_settings_args,
    add_upgrade_args,
    reinstall_from_args,
    selected_groups,
    settings_from_args,
    upgrade_from_args,
)
from .._common import EXIT_STALE


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "run",
        help="run a command, such as ansible-playbook, with the project's collections",
        description="Install what alai.lock lists, locking first if needed, as alai sync does, then run COMMAND with "
        + "Ansible loading collections from the project's collections directory and nowhere else. Collections there "
        + "that alai.lock doesn't list are kept, with a warning unless --inexact is given, or removed with --exact. "
        + "Everything after COMMAND is passed to it.",
        formatter_class=HelpFormatter,
    )
    check = add_check_args(parser)
    _ = check.add_argument("--no-sync", action="store_true", help="run without locking or installing anything first")
    exactness = parser.add_mutually_exclusive_group()
    _ = exactness.add_argument(
        "--exact",
        action="store_true",
        help="remove installed collections alai.lock doesn't list, as alai sync does, rather than warn of them",
    )
    _ = exactness.add_argument(
        "--inexact",
        action="store_true",
        help="keep installed collections alai.lock doesn't list without warning of them",
    )
    _ = add_group_args(parser)
    add_upgrade_args(parser, groups=True)
    add_reinstall_args(parser)
    add_global_args(parser, suppress=True)
    add_settings_args(parser, "resolve", "install")
    _ = parser.add_argument("command", metavar="COMMAND", help="the command to run, found on PATH")
    _ = parser.add_argument("args", metavar="ARG", nargs=argparse.REMAINDER, help="the command's arguments")
    parser.set_defaults(parser=parser, handler=run)


def run(args: argparse.Namespace) -> int:
    import os
    import shutil
    import sys

    from .._common import Error
    from .._environ import ansible_environment, warn_unlisted
    from .._project import load_project
    from .._sync import sync_project

    settings = settings_from_args(args)
    raw, manifest = load_project(settings, "alai run points Ansible at the collections it lists")
    if not args.no_sync:
        project, groups = selected_groups(args, manifest.groups)
        wanted = sync_project(
            settings,
            raw,
            manifest,
            project=project,
            groups=groups,
            upgrade=upgrade_from_args(args),
            locked=args.locked,
            frozen=args.frozen,
            exact=args.exact,
            reinstall=reinstall_from_args(args),
            brief=True,
        )
        if wanted is None:
            return EXIT_STALE
        if not args.exact and not args.inexact:
            warn_unlisted(settings, wanted, "alai run --exact or alai sync removes them")

    command: str = args.command
    environ = {**os.environ, **ansible_environment(settings, manifest)}
    path = shutil.which(command, path=environ.get("PATH", os.defpath))
    if path is None:
        where = "there's no such executable file" if os.sep in command else "it isn't on PATH"
        raise Error(f"can't run {command}: {where}")
    # exec replaces alai, so anything still buffered would be lost.
    _ = sys.stdout.flush()
    _ = sys.stderr.flush()
    try:
        os.execve(path, [command, *args.args], environ)  # noqa: S606 # running the user's command is the point
    except OSError as e:
        raise Error(f"can't run {command} ({path}): {e.strerror}") from None
