# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai env``: print the variables ``alai run`` sets, for a shell, direnv, a CI step or an editor's env file."""

from __future__ import annotations

import argparse

from .._args import HelpFormatter, add_global_args, add_settings_args, settings_from_args


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    env = commands.add_parser(
        "env",
        help="print the environment variables alai run sets, for a shell to use",
        description="Print the environment variables that make Ansible load collections from the project's "
        + 'collections directory and nowhere else, as alai run sets them: eval "$(alai env)" in a shell. Nothing is '
        + "locked or installed; a warning says when the collections directory doesn't match alai.lock.",
        formatter_class=HelpFormatter,
    )
    _ = env.add_argument(
        "--output-format",
        choices=("sh", "fish", "pwsh", "dotenv"),
        help="what to print: sh, export lines for POSIX shells; fish or pwsh, the same for fish and PowerShell; or "
        + "dotenv, KEY=VALUE lines for env files. fish when SHELL is fish, else sh",
    )
    add_global_args(env, suppress=True)
    # The indexes only for telling whether alai.lock is out of date, as nothing is fetched.
    add_settings_args(env, "collections_dir", "index", "default_index")
    env.set_defaults(parser=env, handler=run)


def run(args: argparse.Namespace) -> int:
    import os
    import sys

    from .._environ import ansible_environment, default_format, render, warn_unlisted
    from .._install import linked, plan
    from .._lock import lock_path, read_lock, stale
    from .._output import count_collections, shown_path, warn
    from .._project import load_project
    from .._sync import DEFAULT_GROUPS

    settings = settings_from_args(args)
    _, manifest = load_project(settings, "alai env points Ansible at the collections it lists")
    path = lock_path(settings.project.root)
    lock = read_lock(path)
    if lock is None:
        warn(f"there's no {shown_path(path)}, so nothing is installed for Ansible to load; run alai sync")
    elif (why := stale(lock, manifest, {i.url for i in settings.indexes})) is not None:
        warn(f"{shown_path(path)} is out of date ({why}); run alai sync")
    else:
        # What alai run would install with no group options.
        wanted = lock.select([g for g in DEFAULT_GROUPS if g in manifest.groups])
        todo = plan(settings.collections_dir, {c.name: c.version for c in wanted}, exact=False)
        missing = len(todo.install)
        if lock.root is not None and not linked(settings.collections_dir, lock.root.name, settings.project.root):
            missing += 1
        if missing:
            warn(f"{count_collections(missing)} from {shown_path(path)} not installed; run alai sync")
        own = [lock.root.name] if lock.root is not None else []
        warn_unlisted(settings, [*(c.name for c in lock.collections), *own], "alai sync removes them")

    environ = ansible_environment(settings, manifest)
    _ = sys.stdout.write(render(environ, args.output_format or default_format(os.environ)))
    return 0
