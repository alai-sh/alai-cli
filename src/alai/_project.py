# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Loading the project's manifest and resolving it, for the commands that lock: ``lock`` and ``sync``."""

from __future__ import annotations

import collections.abc as _c
import typing as _t

from ._common import ConfigError
from ._config import RawManifest, Settings, raw_manifest, read_toml
from ._lock import Lock
from ._manifest import Manifest, parse_manifest
from ._output import warn
from ._progress import Live, Task

if _t.TYPE_CHECKING:
    from ._resolve import Resolution


def load_project(settings: Settings, why: str) -> tuple[RawManifest, Manifest]:
    """The project's ``alai.toml`` as written and as parsed; ``why`` says, in the error, why a command needs one."""
    project = settings.project
    if project.config_file is None:
        raise ConfigError(f"there's no alai.toml in {project.root}: {why}")
    raw = raw_manifest(read_toml(project.config_file), project.config_file)
    return raw, parse_manifest(raw)


def resolve_project(
    settings: Settings,
    manifest: Manifest,
    raw: RawManifest,
    existing: Lock | None,
    *,
    upgrade: bool | _c.Collection[str] = False,
) -> tuple[Lock, Resolution]:
    """Resolve the project against its indexes, keeping ``existing``'s versions while they fit, and warn of anything
    the resolution reported. The lock isn't written."""
    from . import __version__
    from ._fetch import Fetcher
    from ._index import IndexClient
    from ._resolve import resolve

    with (
        Fetcher(
            user_agent=f"alai/{__version__}", offline=settings.offline, system_certs=settings.system_certs
        ) as fetcher,
        Live(Task("Resolving collections...")) as task,
    ):
        clients = [IndexClient(index, fetcher, settings.cache_dir) for index in settings.indexes]
        resolution = resolve(manifest, clients, locked=existing, upgrade=upgrade, report=lambda n, d: task.show(n, d))
    for message in resolution.warnings:
        warn(message)
    return Lock(raw, resolution.collections), resolution


def lock_unchanged(existing: Lock | None, manifest: Manifest, lock: Lock) -> bool:
    """Whether resolving again left ``existing`` as it was: locked from the same inputs, to the same collections."""
    return existing is not None and existing.is_current(manifest) and existing.collections == lock.collections
