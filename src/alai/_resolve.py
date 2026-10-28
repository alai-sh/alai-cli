# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Resolving a project's requirements to one version of each collection.

The rules are in ``design/resolver.md``. This builds the table the search runs over: every collection the project's
requirements can reach, each version with its dependencies and, where a rule rules a version out (yanked, unreadable
metadata, needing a newer ansible-core), why. PubGrub, in the native extension, chooses from the table in one call, or
proves there's no choice and explains why.
"""

from __future__ import annotations

import collections.abc as _c
import difflib
import typing as _t

from alai_core import semver
from alai_core.index import CollectionVersion
from alai_core.pep440 import SpecifierSet, Version
from alai_core.semver import VersionReq

from . import _native
from ._common import Error, Record, selects
from ._index import IndexClient, IndexFile
from ._lock import GitCommitSource, IndexSource, Lock, LockedCollection, locked_source
from ._manifest import GitSource, IndexPin, Manifest, PathSource, Requirement, SourceCollection

if _t.TYPE_CHECKING:
    from ._native import _Clauses, _Entry

_LOCAL = ""
"""The index name of a collection read from a directory, a tarball or a commit: the project's own, when its root is one,
or a path, URL or git source. No configured index can be called this, so it's never fetched, and its one version is what
its galaxy.yml or MANIFEST.json says, or its source gives."""


class ResolutionError(Error):
    """The requirements can't all be met, or name something no configured index has."""


class Resolution(Record):
    __slots__ = ("collections", "reasons", "warnings")

    def __init__(
        self,
        collections: tuple[LockedCollection, ...],
        warnings: tuple[str, ...],
        reasons: _c.Mapping[str, str] | None = None,
    ) -> None:
        self.collections = collections
        """One per collection, sorted by name."""
        self.warnings = warnings
        """Things to tell the user that didn't stop resolution, such as versions skipped for unreadable metadata."""
        self.reasons = dict(reasons or {})
        """By name, why a collection was added or its locked version not kept, where that can be told."""


class _Requirement(Record):
    """A requirement on a collection and what asked for it, for the roots and for explaining the result."""

    __slots__ = ("group", "name", "parent", "parent_name", "req", "text")

    def __init__(
        self,
        name: str,
        req: VersionReq,
        *,
        parent: str,
        text: str,
        group: str | None = None,
        parent_name: str | None = None,
    ) -> None:
        self.name = name
        self.req = req
        self.parent = parent
        """What asked for it, for messages: ``the project``, ``group 'test'`` or ``community.general 10.7.2``."""
        self.parent_name = parent_name
        """The collection that asked for it, or None for the project or a group."""
        self.group = group
        """The group it's listed in, or None for the project's own."""
        self.text = text
        """The requirement as written, with any override applied, for messages."""


class _Chosen(Record):
    __slots__ = ("index", "name", "version")

    def __init__(self, name: str, version: _Candidate, index: str) -> None:
        self.name = name
        self.version = version
        self.index = index

    def __str__(self) -> str:
        return f"{self.name} {self.version.version}"


def resolve(
    manifest: Manifest,
    indexes: _c.Sequence[IndexClient],
    *,
    locked: Lock | None = None,
    installed: _c.Mapping[str, semver.Version] | None = None,
    upgrade: bool | _c.Collection[str] = False,
    ansible_version: Version | None = None,
    report: _c.Callable[[str, int], None] | None = None,
    origin: str = "the project",
) -> Resolution:
    """Choose one version of every collection the project needs, its groups included.

    ``indexes`` are the configured indexes in the order they're searched, the default last. ``locked`` is the current lock,
    whose versions are kept while they still fit, unless ``upgrade`` is true or names the collection. ``installed`` is
    the same by version alone, from whichever index the collection comes from, for what's installed outside a lock.
    ``ansible_version`` is the one ansible-core version to resolve for, when it's known: each version's own
    ``requires_ansible`` must then contain it, upper bounds included, in place of the project's range.
    ``origin`` is what the manifest's own requirements are said to come from, in errors: ``the project``, or ``the
    command line`` for ones given there. ``report``, for progress, is called with each collection as its versions are
    looked at, and how many index files have been read so far.
    """
    project = manifest.requires_ansible
    if ansible_version is not None and project is not None and not project.contains(ansible_version):
        raise ResolutionError(f"ansible-core {ansible_version} isn't in the project's requires_ansible {project}")
    by_name: dict[str, IndexClient] = {}
    for client in indexes:
        if client.index.name in by_name:
            raise ValueError(f"index '{client.index.name}' is given more than once")
        by_name[client.index.name] = client
    table = _Table(
        manifest,
        by_name,
        _preferences(locked, upgrade),
        ansible_version,
        report,
        _kept(installed or {}, upgrade),
        origin,
    )
    roots = table.roots()
    table.load(roots)
    try:
        picked = _native.resolve(*table.arguments(roots))
    except _native.NoSolution as e:
        explanation = "\n".join(f"  {line}" if line else "" for line in str(e).splitlines())
        # A version left out of an index file may be why there's none, so say so, as a warning would have.
        hints = "".join(f"\nhint: {note}" for note in table.file_notes(why=True))
        message = f"no solution found when resolving collections:\n{explanation}"
        raise ResolutionError(message + (f"\n{hints}" if hints else "")) from None
    chosen = table.chosen(picked)

    collections: list[LockedCollection] = []
    for c in sorted(chosen.values(), key=lambda c: c.name):
        dependencies = tuple(sorted({name for name, _ in c.version.deps}))
        if c.index != _LOCAL:
            version = c.version
            assert isinstance(version, CollectionVersion)  # noqa: S101  # Only local collections aren't from an index.
            requires_ansible = version.requires_ansible
            collections.append(
                LockedCollection(
                    c.name,
                    version.version,
                    source=IndexSource(by_name[c.index].config().root),
                    cksum=version.sha256,
                    size=version.size,
                    requires_ansible=requires_ansible.text if requires_ansible is not None else None,
                    dependencies=dependencies,
                )
            )
        elif (local := manifest.local.get(c.name)) is not None and not isinstance(
            source := manifest.sources[c.name], IndexPin
        ):
            collections.append(
                LockedCollection(
                    c.name,
                    local.version,
                    source=locked_source(source, local),
                    cksum=local.cksum,
                    size=local.size,
                    requires_ansible=local.requires_ansible,
                    requirements=local.dependencies,
                    dependencies=dependencies,
                    source_version=isinstance(source, (PathSource, GitSource)) and source.version is not None,
                )
            )
    reasons = table.reasons(chosen, roots, locked, upgrade)
    return Resolution(tuple(collections), tuple(table.warnings(chosen)), reasons)


def _kept(installed: _c.Mapping[str, semver.Version], upgrade: bool | _c.Collection[str]) -> dict[str, semver.Version]:
    """The installed versions to prefer: all of them, unless ``upgrade`` says otherwise."""
    return {name: version for name, version in installed.items() if not selects(upgrade, name)}


def _preferences(locked: Lock | None, upgrade: bool | _c.Collection[str]) -> dict[str, LockedCollection]:
    """The locked versions to prefer: all of them, unless ``upgrade`` says otherwise."""
    if locked is None:
        return {}
    return {c.name: c for c in locked.collections if not selects(upgrade, c.name)}


def _ranks(versions: _c.Iterable[semver.Version]) -> dict[str, tuple[int, bool]]:
    """Each version's place among the distinct precedences of ``versions``, by text, and whether it's a pre-release.
    The resolver orders versions by these, then by text, so it needs no version parsing of its own."""
    precedence = {v.text: v.precedence for v in versions}
    rank = {p: n for n, p in enumerate(sorted(set(precedence.values())))}
    # A release's precedence ends (1,), a pre-release's (0, ...).
    return {text: (rank[p], p[3] == 0) for text, p in precedence.items()}


class _Table:
    """Everything the search needs, gathered from the indexes and the manifest."""

    def __init__(
        self,
        manifest: Manifest,
        indexes: dict[str, IndexClient],
        preferred: dict[str, LockedCollection],
        ansible_version: Version | None,
        report: _c.Callable[[str, int], None] | None = None,
        installed: dict[str, semver.Version] | None = None,
        origin: str = "the project",
    ) -> None:
        self._manifest = manifest
        self._local = {name: _local_version(c) for name, c in manifest.local.items()}
        """Each collection read from a directory as its one candidate: the path sources, and the project's own, when
        the root is one. The project's own has no dependencies here, as its galaxy.yml's are the project's
        requirements."""
        if manifest.root is not None:
            self._local[manifest.root.name] = _local_version(manifest.root, root=True)
        self._origin = origin
        self._report = report
        self._installed = installed or {}
        self._indexes = indexes
        """By name, in the order they're searched: built from a sequence, so the order is the configured one."""
        self._preferred = preferred
        self._ansible_version = ansible_version
        self._files: dict[tuple[str, str], IndexFile | None] = {}
        self._packages: dict[str, tuple[_Entry, ...]] = {}
        """Every collection the requirements can reach, as the search takes it: each version with its dependencies,
        and why it can't be chosen if it can't."""
        self._versions: dict[str, semver.Version] = {}
        """Every version the search compares, by text: each collection's, and each a requirement names."""
        self._missing: dict[str, str] = {}
        """Collections something depends on that have no versions: no configured index has them, or the index's
        listing says it does but the file isn't there."""
        self._sources: dict[str, str] = {}
        """How each collection read from a directory, tarball or commit is described in a failure."""
        self._preferred_texts: dict[str, str] = {}
        """The locked or installed version to try first, by collection, where it's still on the index."""
        self._unusable: dict[str, dict[str, str]] = {}
        """Per collection, versions that met every requirement but were skipped for unreadable metadata."""
        self._pinned_exactly = {
            r.name
            for r in (*manifest.collections, *(r for items in manifest.groups.values() for r in items))
            if any(op == "==" for op, _ in r.req.clauses)
        } | {name for name, req in manifest.overrides.items() if any(op == "==" for op, _ in req.clauses)}
        """Collections the user pins with ``==``, which may choose a yanked version, as uv allows."""
        for name, pin in manifest.sources.items():
            if isinstance(pin, IndexPin) and pin.index not in indexes:
                raise ResolutionError(
                    f"[project.sources] takes {name} from index '{pin.index}', which isn't configured"
                    + _did_you_mean(pin.index, indexes)
                )

    def roots(self) -> list[_Requirement]:
        """The project's requirements and its groups', checked against the indexes' listings."""
        listed: list[tuple[str, str | None]] = [(self._origin, None) for _ in self._manifest.collections]
        for group, items in self._manifest.groups.items():
            listed.extend((f"group '{group}'", group) for _ in items)
        named = [*self._manifest.collections, *(r for items in self._manifest.groups.values() for r in items)]

        # Ask every index's listing at once, then start each root's index file, which is all an index with no listing
        # can say whether it has the root from.
        for client in self._indexes.values():
            _ = client.collections()
        self._prefetch(r.name for r in named)
        roots: list[_Requirement] = []
        for requirement, (parent, group) in zip(named, listed, strict=True):
            if self._index_for(requirement.name) is None:
                known = [n for client in self._indexes.values() for n in client.collections() or ()]
                names = ", ".join(f"'{i}'" for i in self._indexes)
                raise ResolutionError(
                    f"no configured index ({names}) has {requirement.name}" + _did_you_mean(requirement.name, known)
                )
            req, text = self._overridden(requirement.name, requirement.req, requirement.text)
            roots.append(_Requirement(requirement.name, req, parent=parent, text=text, group=group))
        return roots

    def load(self, roots: _c.Iterable[_Requirement]) -> None:
        """Read the index file of every collection the roots can reach, through any version's dependencies, and
        make each a package for the search."""
        queue = [r.name for r in roots]
        while queue:
            name = queue.pop()
            if name in self._packages or name in self._missing:
                continue
            if self._report is not None:
                self._report(name, len(self._files))
            index = self._index_for(name)
            if index == _LOCAL:
                local = self._local[name]
                self._packages[name] = (self._entry(name, local, allow_yanked=True),)
                root = self._manifest.root
                self._sources[name] = (
                    "the project's own collection"
                    if root is not None and name == root.name
                    else f"from {self._manifest.sources[name]}"
                )
                versions: _c.Sequence[_Candidate] = (local,)
            elif index is None:
                self._missing[name] = f"no configured index has {name}"
                continue
            else:
                file = self._file(index, name)
                if file is None:
                    # Its listing says it has it, but the file isn't there: the two are a sync apart.
                    self._missing[name] = f"index '{index}' doesn't have {name}"
                    continue
                keep = self._keep(name, index)
                pinned = name in self._pinned_exactly
                self._packages[name] = tuple(
                    self._entry(name, v, allow_yanked=pinned or keep == v.version) for v in file.versions
                )
                if keep is not None and any(v.version == keep for v in file.versions):
                    self._preferred_texts[name] = keep.text
                versions = file.versions
            queue.extend(dep for version in versions if version.problem is None for dep, _ in version.deps)

    def arguments(
        self, roots: _c.Sequence[_Requirement]
    ) -> tuple[
        dict[str, tuple[_Entry, ...]],
        dict[str, tuple[int, bool]],
        tuple[tuple[str, _Clauses], ...],
        tuple[tuple[str, tuple[tuple[str, _Clauses], ...]], ...],
        dict[str, str],
        dict[str, str],
        dict[str, str],
        frozenset[str],
        str,
        str,
    ]:
        """What ``_native.resolve`` takes: the table ``load`` built, the requirements, and the words a failure is
        explained in."""
        groups: dict[str, list[tuple[str, _Clauses]]] = {}
        for r in roots:
            if r.group is not None:
                groups.setdefault(r.group, []).append((r.name, self._clauses(r.req)))
        top = tuple((r.name, self._clauses(r.req)) for r in roots if r.group is None)
        if self._origin == "the command line":
            # uv's words for `uv pip`, whose requirements are the command line's too.
            depends, unsatisfiable = "you require", "your requirements are unsatisfiable"
        else:
            depends = f"{self._origin} depends on"
            unsatisfiable = f"{self._origin}'s requirements are unsatisfiable"
        return (
            self._packages,
            _ranks(self._versions.values()),
            top,
            tuple((group, tuple(items)) for group, items in groups.items()),
            self._preferred_texts,
            self._missing,
            self._sources,
            frozenset(self._manifest.overrides),
            depends,
            unsatisfiable,
        )

    def _clauses(self, req: VersionReq) -> _Clauses:
        clauses: list[tuple[str, str]] = []
        for op, version in req.clauses:
            self._versions[version.text] = version
            clauses.append((op, version.text))
        return tuple(clauses)

    def _entry(self, name: str, version: _Candidate, *, allow_yanked: bool) -> _Entry:
        unavailable = self._unavailable_because(name, version, allow_yanked=allow_yanked)
        self._versions[version.version.text] = version.version
        dependencies: list[tuple[str, _Clauses]] = []
        # The resolver never reads an unavailable version's dependencies, and an unusable one's can't be read.
        for dep, req in () if unavailable else version.deps:
            req, _ = self._overridden(dep, req, "")
            dependencies.append((dep, self._clauses(req)))
        return (version.version.text, unavailable, tuple(dependencies))

    def _keep(self, name: str, index: str) -> semver.Version | None:
        """The locked version, else the installed one: either is kept while it still fits."""
        preferred = self._preferred.get(name)
        if preferred is not None and preferred.source == IndexSource(self._indexes[index].config().root):
            return preferred.version
        return self._installed.get(name)

    def chosen(self, picked: _c.Mapping[str, str]) -> dict[str, _Chosen]:
        out: dict[str, _Chosen] = {}
        for name, text in picked.items():
            index = self._index_for(name)
            if index == _LOCAL:
                out[name] = _Chosen(name, self._local[name], index)
            elif index is not None and (file := self._files.get((index, name))) is not None:
                out[name] = _Chosen(name, next(v for v in file.versions if v.version.text == text), index)
        return out

    def requirements_on(
        self, chosen: _c.Mapping[str, _Chosen], roots: _c.Iterable[_Requirement]
    ) -> dict[str, list[_Requirement]]:
        """Every requirement on each collection in the resolution: the roots, and the chosen versions'
        dependencies."""
        on: dict[str, list[_Requirement]] = {}
        for r in roots:
            on.setdefault(r.name, []).append(r)
        for c in chosen.values():
            for name, req in c.version.deps:
                req, text = self._overridden(name, req, _dependency_text(name, req))
                on.setdefault(name, []).append(_Requirement(name, req, parent=str(c), text=text, parent_name=c.name))
        return on

    def file_notes(self, *, why: bool = False) -> list[str]:
        """What the index files read left out: versions that need a newer alai, and lines that couldn't be read, with
        the first such line and why, if ``why``."""
        out: list[str] = []
        for (index, name), file in sorted(self._files.items()):
            if file is None:
                continue
            if file.newer:
                out.append(f"{name}: {file.newer} versions in index '{index}' need a newer alai")
            if file.invalid:
                first = f", the first at {file.invalid[0]}" if why else ""
                out.append(f"{name}: skipped {len(file.invalid)} unreadable lines in index '{index}'{first}")
        return out

    def warnings(self, chosen: _c.Mapping[str, _Chosen]) -> list[str]:
        out = self.file_notes()
        project = self._manifest.requires_ansible
        for name in sorted(chosen):
            candidate = chosen[name]
            if self._manifest.root is not None and name == self._manifest.root.name:
                continue
            if candidate.version.yanked:
                out.append(f"{candidate} is yanked")
            # A newer version that would have fitted, skipped because its metadata couldn't be read.
            for text, problem in sorted(self._unusable.get(name, {}).items()):
                file = self._files.get((candidate.index, name))
                version = next((v for v in file.versions if v.version.text == text), None) if file else None
                if version is not None and version.version > candidate.version.version:
                    out.append(f"skipped {name} {text}, which can't be used: {problem}")
            theirs = candidate.version.requires_ansible
            if (
                self._ansible_version is None
                and project is not None
                and theirs is not None
                and name not in self._manifest.ignore_requires_ansible
                and theirs.narrower_than(project)
            ):
                out.append(
                    f"{candidate} supports ansible-core {theirs}, which ends inside the project's requires_ansible "
                    + f"{project}"
                )
        return out

    def reasons(
        self,
        chosen: _c.Mapping[str, _Chosen],
        roots: _c.Iterable[_Requirement],
        locked: Lock | None,
        upgrade: bool | _c.Collection[str],
    ) -> dict[str, str]:
        """Why each collection that's new, or whose locked version wasn't kept, is in the resolution as it is.

        Worked out once the resolution is known, from the requirements on each collection and the locked version's
        own metadata. A locked version passed over without any of these reasons gets none.
        """
        on = self.requirements_on(chosen, roots)
        out: dict[str, str] = {}
        for name, candidate in sorted(chosen.items()):
            if self._manifest.root is not None and name == self._manifest.root.name:
                continue
            reqs = on.get(name, [])
            was = locked.get(name) if locked is not None else None
            if was is None:
                parents = ", ".join(sorted({r.parent for r in reqs}))
                out[name] = f"{name} is needed by {parents}"
                continue
            if candidate.index == _LOCAL:
                source = self._manifest.sources[name]
                local = self._manifest.local[name]
                if isinstance(was.source, GitCommitSource) and isinstance(source, GitSource):
                    if not was.source.same_source(source):
                        out[name] = f"{name} now comes from {source}"
                    elif was.source.commit != local.commit and local.commit is not None:
                        out[name] = f"{source} is now at commit {local.commit[:7]}"
                    elif was.version != candidate.version.version:
                        out[name] = f"{name}'s version is now {candidate.version.version}"
                elif not (was.is_local() or was.is_archive()) or str(was.source) != str(source):
                    out[name] = f"{name} now comes from {source}"
                elif was.version != candidate.version.version:
                    out[name] = f"{name}'s {local.metadata} now says {candidate.version.version}"
                continue
            root = self._indexes[candidate.index].config().root
            if was.version == candidate.version.version and was.source == IndexSource(root):
                continue
            reason = self._not_kept(name, was, candidate, reqs, chosen, on, upgrade)
            if reason is not None:
                out[name] = reason
        for was in locked.collections if locked is not None else ():
            if was.name not in chosen:
                out[was.name] = f"nothing needs {was.name} any more"
        return out

    def _not_kept(
        self,
        name: str,
        was: LockedCollection,
        candidate: _Chosen,
        reqs: _c.Sequence[_Requirement],
        chosen: _c.Mapping[str, _Chosen],
        on: _c.Mapping[str, _c.Sequence[_Requirement]],
        upgrade: bool | _c.Collection[str],
    ) -> str | None:
        locked = f"{name} {was.version}"
        if selects(upgrade, name):
            return f"upgrading {name} was asked for"
        if was.source != IndexSource(self._indexes[candidate.index].config().root):
            return f"{name} now comes from index '{candidate.index}'"
        unmet = [r for r in reqs if not r.req.contains(was.version)]
        if unmet:
            return f"{locked} doesn't meet " + ", ".join(f"{r.text} (from {r.parent})" for r in unmet)
        file = self._files.get((candidate.index, name))
        version = next((v for v in file.versions if v.version == was.version), None) if file is not None else None
        if version is None:
            return f"{locked} is no longer on index '{candidate.index}'"
        unavailable = self._unavailable_because(name, version, allow_yanked=True)
        if unavailable is not None:
            return f"{locked} {unavailable}"
        # A dependency it can't have. Prefer one that conflicts with a requirement from something else, the project
        # or another collection, over one only its replacement asks differently for, which is a consequence, not the
        # cause.
        fallback: str | None = None
        for dep, req in version.deps:
            req, text = self._overridden(dep, req, _dependency_text(dep, req))
            other = chosen.get(dep)
            if other is None:
                if self._index_for(dep) is None:
                    return f"{locked} needs {dep}, which no configured index has"
                continue
            if req.contains(other.version.version):
                continue
            file = self._files.get((other.index, dep))
            versions = file.versions if file is not None else ()
            for theirs in on.get(dep, ()):
                if theirs.parent_name == name:
                    continue
                if not any(req.contains(v.version) and theirs.req.contains(v.version) for v in versions):
                    return f"{locked} needs {text}, which conflicts with {theirs.text} (from {theirs.parent})"
            fallback = fallback or f"{locked} needs {text}, but {other} was chosen"
        return fallback

    def _index_for(self, name: str, *, wait: bool = True) -> str | None:
        """Where a collection comes from, whatever asked for it: its ``[project.sources]`` entry, else the first
        index that has it, as uv's default ``first-index`` strategy does. None if no index has it. An index with no
        listing has it if its index file is there, which this waits for.

        Without ``wait``, the index to fetch its file from next: an index with no listing that hasn't answered yet ends
        the search. Later indexes aren't asked until it has, so they never learn a name an earlier one has, such as a
        private collection's."""
        if name in self._local:
            return _LOCAL
        pin = self._manifest.sources.get(name)
        if isinstance(pin, IndexPin):
            return pin.index
        if wait:
            return next((index for index, client in self._indexes.items() if client.has(name)), None)
        return next((index for index, client in self._indexes.items() if client.known(name) is not False), None)

    def _prefetch(self, names: _c.Iterable[str]) -> None:
        """Start fetching the index files for ``names``, each from the index it comes from, or the first that has
        to be asked."""
        by_index: dict[str, set[str]] = {}
        for name in names:
            index = self._index_for(name, wait=False)
            if index is not None and index != _LOCAL and (index, name) not in self._files:
                by_index.setdefault(index, set()).add(name)
        for index, wanted in by_index.items():
            self._indexes[index].prefetch(sorted(wanted))

    def _file(self, index: str, name: str) -> IndexFile | None:
        key = (index, name)
        if key not in self._files:
            file = self._files[key] = self._indexes[index].versions(name)
            if file is not None:
                # Every dependency any version names, fetched now rather than when the table gets to it.
                self._prefetch({dep for v in file.versions if v.problem is None for dep, _ in v.deps})
        return self._files[key]

    def _overridden(self, name: str, req: VersionReq, text: str) -> tuple[VersionReq, str]:
        override = self._manifest.overrides.get(name)
        if override is None:
            return req, text
        return override, f"{name}{override.text if override.clauses else ''} ([project.overrides], replacing {text})"

    def _unavailable_because(self, name: str, version: _Candidate, *, allow_yanked: bool) -> str | None:
        """Why a version can't be chosen, as a predicate for messages (``is yanked``), or None if it can."""
        if version.problem is not None:
            self._unusable.setdefault(name, {})[version.version.text] = version.problem
            return "has unreadable metadata"
        if version.yanked and not allow_yanked:
            return "is yanked"
        theirs = version.requires_ansible
        if theirs is None or name in self._manifest.ignore_requires_ansible:
            return None
        if self._ansible_version is not None:
            # Resolving for one ansible-core: the version's whole range must contain it, as ansible-galaxy checks the
            # running ansible-core.
            if not theirs.contains(self._ansible_version):
                return f"doesn't support ansible-core {self._ansible_version}"
            return None
        project = self._manifest.requires_ansible
        if project is not None and not theirs.admits_lower_bound(project):
            return f"needs a newer ansible-core than the project's {project}"
        return None


class _LocalVersion(Record):
    """A collection from a directory, tarball or commit, read as an index line would describe it, with no artifact
    to download."""

    __slots__ = ("deps", "name", "problem", "requires_ansible", "version", "yanked")

    def __init__(
        self,
        name: str,
        version: semver.Version,
        *,
        deps: tuple[tuple[str, VersionReq], ...],
        requires_ansible: SpecifierSet | None,
    ) -> None:
        self.name = name
        self.version = version
        self.deps = deps
        self.requires_ansible = requires_ansible
        self.yanked = False
        self.problem: str | None = None


_Candidate = CollectionVersion | _LocalVersion


def _local_version(collection: SourceCollection, *, root: bool = False) -> _LocalVersion:
    """A collection in a directory as an index entry would describe it, with no artifact. The project's own has no
    dependencies or ``requires_ansible`` here: its galaxy.yml's are the project's requirements, and its
    ``meta/runtime.yml``'s the project's range."""
    requirements = [] if root else [Requirement.parse(text) for text in collection.dependencies]
    requires_ansible = (
        None if root or collection.requires_ansible is None else SpecifierSet(collection.requires_ansible)
    )
    return _LocalVersion(
        collection.name,
        collection.version,
        deps=tuple((r.name, r.req) for r in requirements),
        requires_ansible=requires_ansible,
    )


def _dependency_text(name: str, req: VersionReq) -> str:
    """A dependency's requirement as it reads in a manifest: a bare version, which the index allows for an exact one,
    shown with its ``==``."""
    if not req.clauses:
        return name
    return f"{name}=={req.text}" if req.text[:1].isdigit() else f"{name}{req.text}"


def _did_you_mean(name: str, known: _c.Iterable[str]) -> str:
    matches = difflib.get_close_matches(name, list(known), n=1)
    return f"; did you mean '{matches[0]}'?" if matches else ""
