# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection install`` and ``uninstall``: collections installed by hand, outside the project's requirements and
lock, as ``uv pip install`` and ``uv pip uninstall`` are. An ``alai.toml``, if the project has one, still marks it and its
settings apply; without one, an ``ansible.cfg`` or ``collections/ansible_collections`` marks it. The design is in
``design/install.md``."""

from __future__ import annotations

import collections.abc as _c
import os
import time
import typing as _t

from ._common import ConfigError
from ._config import Settings
from ._install import Installed, installed, remove
from ._manifest import (
    Manifest,
    PathSource,
    Requirement,
    Source,
    SourceCollection,
    UrlSource,
    collection_name,
    file_url_path,
    given_source,
    is_git,
    is_path,
    is_url,
    parse_git,
    source_key,
)
from ._output import DIM, change, debug, home_path, shown_path, status, styled, summary, warn
from ._project import resolve_manifest
from ._sync import install_locked


def target(settings: Settings) -> str:
    """The collections directory to work on: the project's, or one set explicitly. With neither, an error, as uv won't
    install without an environment to install into, rather than make a ``collections/`` wherever alai runs."""
    project = settings.project
    if project.marker is None and settings.sources.get("collections_dir", "default") == "default":
        raise ConfigError(
            f"no project found from {home_path(project.root)} (no alai.toml, ansible.cfg or collections/ansible_collections in "
            + "it or a directory above it); pass --collections-dir to say where to install"
        )
    return settings.collections_dir


def using(cdir: str, *, always: bool = False) -> str:
    """Say which collections directory is used, as ``uv pip`` names the environment it uses, unless it's the one a user
    would assume, ``collections`` in the current directory, and not ``always``. Returns it as it's shown."""
    shown = home_path(shown_path(cdir))
    if always or os.path.abspath(cdir) != os.path.abspath("collections"):
        status(styled(f"Using collections directory at: {shown}", DIM))
    return shown


def install(
    settings: Settings,
    requirements: _c.Sequence[str],
    *,
    upgrade: bool | set[str],
    reinstall: bool | set[str] = False,
    keep_unversioned: bool = False,
    editable: _c.Sequence[str] = (),
) -> None:
    """Install ``requirements`` and what they need, preferring what's already installed, and remove nothing else.
    A requirement that's a directory (see ``is_path``) installs the collection whose source it holds, copied, and one
    in ``editable`` links it, as ``uv pip install ./lib`` and ``-e ./lib`` do. A tarball, by path or URL, installs the
    collection it holds, as ``uv pip install ./lib-1.0.tar.gz`` does, and is left as it is when the same tarball is
    already installed. A git repository, ``git+<url>[@<rev>][#subdirectory=<dir>]``, installs the collection at that
    commit, as ``uv pip install git+...`` does, and is left as it is when that commit is already installed.

    If every requirement is already met by what's installed, and nothing is to be upgraded or reinstalled, nothing is
    resolved or fetched, as ``uv pip install`` checks first. ``reinstall``, everything resolved or the names given,
    replaces those already at the version chosen. With ``keep_unversioned``, a collection whose version can't be read,
    such as a source checkout with no ``MANIFEST.json``, counts as installed whatever version is asked for, as
    ansible-galaxy counts one: it's left as it is, and isn't resolved when it's asked for.
    """
    from ._project import fetch_git_source, fetch_url_source, read_path_source

    started = time.monotonic()
    cdir = target(settings)
    _ = using(cdir)
    wanted: list[Requirement] = []
    sources: dict[str, Source] = {}
    local: dict[str, SourceCollection] = {}
    for text, edit in [*((t, False) for t in requirements), *((t, True) for t in editable)]:
        if is_git(text):
            if edit:
                raise ConfigError(f"{text} is a git repository, which can't be editable: give a checkout to link to")
            source = parse_git(text)
            collection = fetch_git_source(settings, source)
        elif is_url(text) and not text.lower().startswith("file:"):
            if edit:
                raise ConfigError(f"{text} is a tarball, which can't be editable: give a directory to link to")
            source = UrlSource(text)
            collection = fetch_url_source(settings, source)
        elif edit or is_url(text) or is_path(text):
            path = file_url_path(text) if is_url(text) else text
            source = PathSource(os.path.abspath(os.path.expanduser(path)), editable=edit)
            collection = read_path_source(settings, None, source)
        else:
            wanted.append(_requirement(text))
            continue
        if collection.name in sources:
            raise ConfigError(f"{collection.name} is given twice, from {sources[collection.name]} and {source}")
        sources[collection.name] = source
        local[collection.name] = collection
        wanted.append(Requirement.parse(collection.name))
    if not wanted:
        raise ConfigError("give a collection, a directory, a tarball or a git repository to install")
    # A directory is copied again every time, as uv pip install builds one again; an editable one only needs linking,
    # and a tarball already installed from the same file or URL, by its checksum, is left, as uv pip install leaves it.
    copies = {
        name
        for name, source in sources.items()
        if isinstance(source, PathSource) and not source.editable and not local[name].is_archive()
    }
    if copies and reinstall is not True:
        reinstall = (reinstall or set()) | copies
    every = installed(cdir)
    present = {name: c.version for name, c in every.items() if c.version is not None}
    unversioned: set[str] = {name for name, c in every.items() if c.version is None} if keep_unversioned else set()
    if (
        not upgrade
        and not reinstall
        and not sources
        and all(r.name in unversioned or (r.name in present and r.req.contains(present[r.name])) for r in wanted)
    ):
        summary("Checked", len({r.name for r in wanted}), time.monotonic() - started, dim=True)
        return
    for name in sorted({r.name for r in wanted} & unversioned):
        debug(f"{name}: keeping {every[name].path}, whose version can't be read, as --keep-unversioned asks")
    resolution = resolve_manifest(
        settings,
        Manifest(
            "",
            collections=tuple(r for r in wanted if r.name not in unversioned or r.name in sources),
            sources=dict(sources),
            local=local,
        ),
        installed=present,
        upgrade=upgrade,
        origin="the command line",
    )
    summary("Resolved", len(resolution.collections), time.monotonic() - started, dim=True)
    install_locked(
        settings,
        list(resolution.collections),
        exact=False,
        keep_unversioned=keep_unversioned,
        reinstall=reinstall,
    )


def uninstall(settings: Settings, names: _c.Sequence[str]) -> None:
    """Remove the collections ``names``, and only them: what they depend on stays, as ``uv pip uninstall`` leaves it.
    One that isn't installed is skipped with a warning."""
    started = time.monotonic()
    cdir = target(settings)
    shown = using(cdir)
    present = installed(cdir)
    chosen: dict[str, None] = {}
    for given in names:
        try:
            name = collection_name(given)
        except ValueError as e:
            raise ConfigError(_by_source(present, given) or str(e)) from None
        if name in present:
            chosen[name] = None
        else:
            warn(f"skipping {name}, which isn't installed in {shown}")
    if not chosen:
        warn("no collections to uninstall")
        return
    for name in chosen:
        remove(cdir, present[name])
    summary("Uninstalled", len(chosen), time.monotonic() - started)
    for name in sorted(chosen):
        version = present[name].version
        change("-", name, f"=={version}" if version is not None else " (unknown version)")


def _by_source(present: _c.Mapping[str, Installed], given: str) -> str | None:
    """What to say when ``collection uninstall`` is given a source, such as ``git+...``, rather than a collection name:
    the installed collections that came from it, by what alai recorded when it installed them, or the directory an
    editable one links to. None when ``given`` isn't a source, for the error about collection names to stand."""
    key = given_source(given)
    if key is None:
        return None
    names: list[str] = []
    for name, collection in sorted(present.items()):
        if os.path.islink(collection.path):
            found: tuple[str, ...] | None = ("path", os.path.realpath(collection.path))
        else:
            source = collection.record.get("source") if collection.record is not None else None
            found = source_key(_t.cast("dict[str, object]", source), "/") if isinstance(source, dict) else None
        if found == key:
            names.append(name)
    if not names:
        return f"collection uninstall takes collection names, such as ns.col, and nothing installed is from {given}"
    return (
        f"collection uninstall takes collection names: {', '.join(names)} came from {given}, so run "
        + f"alai collection uninstall {' '.join(names)}"
    )


def _requirement(text: str) -> Requirement:
    try:
        return Requirement.parse(text.strip())
    except ValueError as e:
        raise ConfigError(str(e)) from None
