# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Loading the project's manifest and resolving it, for the commands that lock: ``lock`` and ``sync``."""

from __future__ import annotations

import argparse
import collections.abc as _c
import typing as _t

from ._common import ConfigError, Record
from ._config import RawManifest, Settings, raw_manifest, read_toml
from ._lock import Lock, LockedRoot, changes, input_changes
from ._manifest import Manifest, collection_name, parse_manifest
from ._output import detail, home_path, lock_change, status, warn
from ._progress import Live, Task

if _t.TYPE_CHECKING:
    from ._resolve import Resolution
    from ._semver import Version


class Upgrade(Record):
    """What to upgrade while resolving: everything (``-U``), the collections named (``-P``), and every collection the
    groups named list in ``alai.toml`` (``--upgrade-group``), as uv's flags of the same names do."""

    __slots__ = ("collections", "everything", "groups")

    def __init__(
        self, *, everything: bool = False, collections: _c.Iterable[str] = (), groups: _c.Iterable[str] = ()
    ) -> None:
        self.everything = everything
        self.collections = frozenset(collections)
        self.groups = tuple(dict.fromkeys(groups))

    def __bool__(self) -> bool:
        return self.everything or bool(self.collections) or bool(self.groups)

    def names(self, manifest: Manifest | None) -> bool | set[str]:
        """What to upgrade, for the resolver: everything, the collections named, or nothing (False). A group is the
        collections it lists directly in ``manifest``, not what they depend on, as uv's ``--upgrade-group`` is; one
        ``manifest`` doesn't have is an error, as uv's is."""
        known = manifest.groups if manifest is not None else {}
        for group in self.groups:
            if group not in known:
                have = ", ".join(f"'{g}'" for g in sorted(known))
                raise ConfigError(
                    f"--upgrade-group: alai.toml has no group '{group}'" + (f" (it has {have})" if have else "")
                )
        if self.everything:
            return True
        return set(self.collections) | {r.name for group in self.groups for r in known[group]} or False


def upgrade_from_args(args: argparse.Namespace) -> Upgrade:
    """What ``-U``, ``-P`` and ``--upgrade-group`` ask to upgrade."""
    try:
        collections = {collection_name(n) for n in args.upgrade_collection}
    except ValueError as e:
        raise ConfigError(f"--upgrade-collection: {e}") from None
    return Upgrade(everything=args.upgrade, collections=collections, groups=getattr(args, "upgrade_group", []))


def load_project(settings: Settings, why: str) -> tuple[RawManifest, Manifest]:
    """The project's ``alai.toml`` as written and as parsed; ``why`` says, in the error, why a command needs one.

    In a collection project, ``alai.toml`` is optional: see ``with_root``.
    """
    project = settings.project
    if project.config_file is None:
        if project.collection_file is None:
            raise ConfigError(f"there's no alai.toml in {home_path(project.root)}: {why}")
        raw = RawManifest(
            project.collection_file,
            collections=[],
            requires_ansible=None,
            groups={},
            overrides={},
            ignore_requires_ansible=[],
        )
    else:
        raw = raw_manifest(read_toml(project.config_file), project.config_file)
    return with_root(settings, raw)


def with_root(settings: Settings, raw: RawManifest) -> tuple[RawManifest, Manifest]:
    """``raw``, the project's ``alai.toml``, as resolved: as it is, or in a collection project, with
    the requirements and ``requires_ansible`` its ``galaxy.yml`` and ``meta/runtime.yml`` give, which ``alai.toml``
    mustn't repeat. See ``design/collection-development.md``."""
    galaxy_file = settings.project.collection_file
    if galaxy_file is None:
        return raw, parse_manifest(raw)

    from ._galaxy import read_root_collection

    root = read_root_collection(galaxy_file)
    for key, value, instead in (
        ("collections", raw.collections, "galaxy.yml's dependencies"),
        ("requires_ansible", raw.requires_ansible, "meta/runtime.yml's requires_ansible"),
    ):
        if value:
            raise ConfigError(
                f"{raw.path}: 'project.{key}' can't be set beside a collection's galaxy.yml, "
                + f"{instead} say that; a [project.groups.<name>] holds collections only its tests need"
            )
    raw = RawManifest(
        raw.path,
        collections=list(root.dependencies),
        requires_ansible=root.requires_ansible,
        groups=raw.groups,
        overrides=raw.overrides,
        ignore_requires_ansible=raw.ignore_requires_ansible,
        sources=raw.sources,
    )
    manifest = parse_manifest(raw)
    manifest.root = root
    return raw, manifest


def resolve_project(
    settings: Settings,
    manifest: Manifest,
    raw: RawManifest,
    existing: Lock | None,
    *,
    upgrade: Upgrade | None = None,
) -> tuple[Lock, Resolution]:
    """Resolve the project against its indexes, keeping ``existing``'s versions while they fit, except those
    ``upgrade`` names, and warn of anything the resolution reported. The lock isn't written."""
    names = upgrade.names(manifest) if upgrade is not None else False
    resolution = resolve_manifest(settings, manifest, locked=existing, upgrade=names)
    root = None
    if manifest.root is not None:
        root = LockedRoot(
            manifest.root.name,
            manifest.root.version,
            requires_ansible=manifest.root.requires_ansible,
            dependencies=tuple(sorted(r.name for r in manifest.collections)),
        )
    return Lock(raw, resolution.collections, root), resolution


def resolve_manifest(
    settings: Settings,
    manifest: Manifest,
    *,
    locked: Lock | None = None,
    installed: _c.Mapping[str, Version] | None = None,
    upgrade: bool | _c.Collection[str] = False,
    origin: str = "the project",
) -> Resolution:
    """Resolve ``manifest`` against the configured indexes, with progress, and warn of anything the resolution
    reported. ``origin`` names where its requirements came from, in errors."""
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
        resolution = resolve(
            manifest,
            clients,
            locked=locked,
            installed=installed,
            upgrade=upgrade,
            origin=origin,
            report=lambda n, d: task.show(n, d),
        )
    for message in resolution.warnings:
        warn(message)
    return resolution


def lock_unchanged(existing: Lock | None, manifest: Manifest, lock: Lock) -> bool:
    """Whether resolving again left ``existing`` as it was: locked from the same inputs, to the same collections."""
    return (
        existing is not None
        and existing.is_current(manifest)
        and existing.collections == lock.collections
        and existing.root == lock.root
    )


def report_changes(
    existing: Lock | None, manifest: Manifest, lock: Lock, resolution: Resolution, *, inputs: bool = False
) -> None:
    """Say how ``lock`` differs from ``existing``: a line per collection added, updated or removed, each with its
    reason under it with ``-v``, as ``alai lock`` reports them. With ``inputs``, for a check that's failing, also what
    in ``alai.toml`` differs from what ``existing`` was locked from, which is the only sign of a change that leaves
    every version as it was."""
    for name, line in changes(existing, lock):
        lock_change(line)
        if (reason := resolution.reasons.get(name)) is not None:
            detail(reason)
    if inputs and existing is not None and (found := input_changes(existing, manifest)):
        status("alai.toml has changed since alai.lock was made:")
        for line in found:
            status(f"  {line}")
