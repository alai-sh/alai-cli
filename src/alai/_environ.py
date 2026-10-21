# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The environment ``alai run`` runs a command in and ``alai env`` prints: Ansible pointed at the project's collections
directory alone. Only environment variables, so the two always agree. The design is in ``design/run.md``."""

from __future__ import annotations

import collections.abc as _c
import json
import os
import shlex

from ._config import Settings
from ._install import installed, namespace_links
from ._manifest import Manifest
from ._output import shown_path, warn

_MODULE_DEBUGGERS = ("_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG", "_ANSIBLE_ANSIBALLZ_PYDEVD_CONFIG")
"""ansible-core's internal settings for debugging modules, which ``ansible-test --dev-debug-on-demand`` sets."""


def ansible_environment(
    settings: Settings, manifest: Manifest, current: _c.Mapping[str, str] | None = None
) -> dict[str, str]:
    """The variables that make Ansible load collections from the collections directory and nowhere else: not the
    user's configured paths, ``~/.ansible/collections`` or the ``ansible`` package's ``site-packages``.

    In a collection project, ansible-test is pointed at the project's own collection through its link in the tree,
    which gives it the ``ansible_collections/<namespace>/<name>`` path it needs wherever the checkout is, and debuggers
    are told to resolve that link, so they show the checkout's files. Breakpoints hit either way, as pydevd matches them on real
    paths, except in modules: see ``_map_module_sources``. ``current`` is the environment the command runs in,
    ``os.environ`` unless given.
    """
    environ = {
        "ANSIBLE_COLLECTIONS_PATH": settings.collections_dir,
        "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH": "False",
    }
    if manifest.root is not None:
        namespace, short = manifest.root.name.split(".")
        link = os.path.join(settings.collections_dir, "ansible_collections", namespace, short)
        environ["ANSIBLE_TEST_CONTENT_ROOT"] = link
        environ["PYDEVD_RESOLVE_SYMLINKS"] = "1"
        current = os.environ if current is None else current
        for name in _MODULE_DEBUGGERS:
            if (value := current.get(name)) is not None and (mapped := _map_module_sources(value, link, settings)):
                environ[name] = mapped
    return environ


def _map_module_sources(value: str, link: str, settings: Settings) -> str | None:
    """A module debugger's configuration with the checkout mapped to the collection's link, or None to leave it.

    Ansible records a module's source by the path it loaded it from, the link in the tree, and the debugger
    matches the editor's breakpoints, set in the checkout, against that by prefix, so they never hit. The
    configuration's ``source_mapping``, from paths the editor knows to paths Ansible knows, translates it.
    ansible-test fills it only with paths it maps to themselves. Best effort, as the setting is ansible-core's
    internal one, until ansible-test maps a content root reached through a link itself: see
    ``design/collection-development.md``.
    """
    try:
        config = json.loads(value)
    except ValueError:
        return None
    if not isinstance(config, dict):
        return None
    mapping = config.setdefault("source_mapping", {})  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(mapping, dict):
        return None
    checkout = settings.project.root + os.sep
    if checkout in mapping:
        return None
    mapping[checkout] = link + os.sep
    return json.dumps(config)


def warn_unlisted(settings: Settings, wanted: _c.Iterable[str], hint: str) -> None:
    """Warn of what Ansible can load from the collections directory that isn't in ``wanted``: collections installed
    there, and namespaces that are links to somewhere else, which alai never looks into. ``hint`` says what removes
    them."""
    names = set(wanted)
    cdir = settings.collections_dir
    extra = [name for name in sorted(installed(cdir)) if name not in names]
    extra += [f"{namespace}.* (a link)" for namespace in sorted(namespace_links(cdir))]
    if extra:
        warn(f"Ansible can load collections alai.lock doesn't list here, from {shown_path(cdir)}: {', '.join(extra)}")
        warn(hint)


def default_format(environ: _c.Mapping[str, str]) -> str:
    """fish's syntax when ``SHELL`` is fish, as ``uv venv`` chooses its activation hint, else POSIX shell's."""
    return "fish" if os.path.basename(environ.get("SHELL", "")) == "fish" else "sh"


def render(variables: _c.Mapping[str, str], output_format: str) -> str:
    """``variables`` as ``output_format`` sets them, a line each: ``sh``, POSIX shell ``export`` lines; ``fish``, ``set
    -gx`` lines; ``pwsh``, PowerShell ``$env:`` assignments; or ``dotenv``, ``KEY=VALUE`` lines for editors' env
    files."""
    if output_format == "fish":
        lines = [f"set -gx {name} {_fish_quote(value)}" for name, value in variables.items()]
    elif output_format == "pwsh":
        lines = [f"$env:{name} = '{_powershell_quote(value)}'" for name, value in variables.items()]
    elif output_format == "dotenv":
        lines = [f"{name}={_dotenv_quote(value)}" for name, value in variables.items()]
    else:
        lines = [f"export {name}={shlex.quote(value)}" for name, value in variables.items()]
    return "".join(f"{line}\n" for line in lines)


_POWERSHELL_QUOTES = frozenset("'\u2018\u2019\u201a\u201b")
"""The characters PowerShell takes as a single quote: ``'`` and the Unicode ones, as ``CharExtensions.IsSingleQuote``."""


def _powershell_quote(value: str) -> str:
    # In PowerShell's single quotes nothing is special but a quote, written twice, as
    # CodeGeneration.EscapeSingleQuotedStringContent does.
    return "".join(c * 2 if c in _POWERSHELL_QUOTES else c for c in value)


def _fish_quote(value: str) -> str:
    # In fish's single quotes only a backslash and a single quote are escaped.
    if value and all(c.isalnum() or c in "@%+=:,./-_" for c in value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dotenv_quote(value: str) -> str:
    # Double quotes, as python-dotenv and VS Code's env files read them, only when the value needs them.
    if value and not any(c.isspace() or c in "\"'#\\" for c in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
