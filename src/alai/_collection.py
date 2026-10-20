# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection install`` and ``uninstall``: collections installed by hand, outside the project's requirements and
lock, as ``uv pip install`` and ``uv pip uninstall`` are. An ``alai.toml``, if the project has one, still marks it and its
settings apply; without one, an ``ansible.cfg`` or ``collections/ansible_collections`` marks it. The design is in
``design/install.md``."""

from __future__ import annotations

import collections.abc as _c
import time

from ._common import ConfigError
from ._config import Settings
from ._install import installed, remove
from ._manifest import Manifest, Requirement, collection_name
from ._output import change, summary, warn
from ._project import resolve_manifest
from ._sync import install_locked


def target(settings: Settings) -> str:
    """The collections directory to work on: the project's, or one set explicitly. With neither, an error, as uv won't
    install without an environment to install into, rather than make a ``collections/`` wherever alai runs."""
    project = settings.project
    if project.marker is None and settings.sources.get("collections_dir", "default") == "default":
        raise ConfigError(
            f"no project found from {project.root} (no alai.toml, ansible.cfg or collections/ansible_collections in "
            + "it or a directory above it); pass --collections-dir to say where to install"
        )
    return settings.collections_dir


def install(settings: Settings, requirements: _c.Sequence[str], *, upgrade: bool | set[str]) -> None:
    """Install ``requirements`` and what they need, preferring what's already installed, and remove nothing else.

    If every requirement is already met by what's installed, and nothing is to be upgraded, nothing is resolved or
    fetched, as ``uv pip install`` checks first.
    """
    started = time.monotonic()
    cdir = target(settings)
    wanted = [_requirement(text) for text in requirements]
    present = {name: c.version for name, c in installed(cdir).items() if c.version is not None}
    if not upgrade and all(r.name in present and r.req.contains(present[r.name]) for r in wanted):
        summary("Checked", len({r.name for r in wanted}), time.monotonic() - started, dim=True)
        return
    resolution = resolve_manifest(
        settings,
        Manifest("", collections=tuple(wanted)),
        installed=present,
        upgrade=upgrade,
        origin="the command line",
    )
    summary("Resolved", len(resolution.collections), time.monotonic() - started, dim=True)
    install_locked(settings, list(resolution.collections), exact=False)


def uninstall(settings: Settings, names: _c.Sequence[str]) -> None:
    """Remove the collections ``names``, and only them: what they depend on stays, as ``uv pip uninstall`` leaves it.
    One that isn't installed is skipped with a warning."""
    started = time.monotonic()
    cdir = target(settings)
    present = installed(cdir)
    chosen: dict[str, None] = {}
    for name in (_name(given) for given in names):
        if name in present:
            chosen[name] = None
        else:
            warn(f"skipping {name}, which isn't installed in {cdir}")
    if not chosen:
        warn("no collections to uninstall")
        return
    for name in chosen:
        remove(cdir, present[name])
    summary("Uninstalled", len(chosen), time.monotonic() - started)
    for name in sorted(chosen):
        version = present[name].version
        change("-", name, f"=={version}" if version is not None else " (unknown version)")


def _requirement(text: str) -> Requirement:
    try:
        return Requirement.parse(text.strip())
    except ValueError as e:
        raise ConfigError(str(e)) from None


def _name(text: str) -> str:
    try:
        return collection_name(text)
    except ValueError as e:
        raise ConfigError(str(e)) from None
