# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Resolving a project's requirements to one version of each collection.

The rules are in ``design/resolver.md``. resolvelib does the search; it's only used here, behind ``resolve``, so another
resolver can replace it without touching anything else.
"""

from __future__ import annotations

import collections.abc as _c
import difflib
import typing as _t

import resolvelib
import resolvelib.resolvers

from . import _semver
from ._common import Error, Record
from ._index import CollectionVersion, IndexClient, IndexFile
from ._lock import IndexSource, Lock, LockedCollection, local_source
from ._manifest import Manifest, PathSource, Requirement, SourceCollection
from ._pep440 import SpecifierSet, Version
from ._semver import VersionReq

_MAX_ROUNDS = 5000
"""How many rounds resolvelib may take, each pinning one collection or backtracking from one."""
_LOCAL = ""
"""The index name of a collection read from a directory: the project's own, when its root is one, or a path source. No
configured index can be called this, so it's never fetched, and its one version is what its galaxy.yml says."""


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
    __slots__ = ("first_party", "index", "name", "parent", "req", "text")

    def __init__(
        self, name: str, req: VersionReq, *, index: str | None, parent: str, text: str, first_party: bool
    ) -> None:
        self.name = name
        self.req = req
        self.first_party = first_party
        """Written by the user: the project's or a group's requirement, or an override. Only these can pin a yanked
        version."""
        self.index = index
        """The name of the index it comes from, or None if no configured index has it."""
        self.parent = parent
        """What asked for it, for messages: ``the project``, ``group 'test'`` or ``community.general 10.7.2``."""
        self.text = text
        """The requirement as written, with any override applied, for messages."""


class _Candidate(Record):
    __slots__ = ("index", "name", "version")

    def __init__(self, name: str, version: CollectionVersion, index: str) -> None:
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
    installed: _c.Mapping[str, _semver.Version] | None = None,
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
    command line`` for ones given there. ``report``, for progress, is called with each collection as its versions are looked at, and how many index files
    have been read so far.
    """
    project = manifest.requires_ansible
    if ansible_version is not None and project is not None and not project.contains(ansible_version):
        raise ResolutionError(f"ansible-core {ansible_version} isn't in the project's requires_ansible {project}")
    by_name: dict[str, IndexClient] = {}
    for client in indexes:
        if client.index.name in by_name:
            raise ValueError(f"index '{client.index.name}' is given more than once")
        by_name[client.index.name] = client
    provider = _Provider(
        manifest,
        by_name,
        _preferences(locked, upgrade),
        ansible_version,
        report,
        _kept(installed or {}, upgrade),
        origin,
    )
    roots = provider.roots()
    resolver: resolvelib.Resolver[_Requirement, _Candidate, str] = resolvelib.Resolver(
        provider, resolvelib.BaseReporter()
    )
    try:
        result = resolver.resolve(roots, max_rounds=_MAX_ROUNDS)
    except resolvelib.ResolutionImpossible as e:  # pyright: ignore[reportUnknownVariableType]
        # resolvelib's exception isn't generic in the type checker's eyes; these are the provider's own types.
        causes = _t.cast(
            "_c.Collection[resolvelib.resolvers.RequirementInformation[_Requirement, _Candidate]]",
            e.causes,
        )
        raise ResolutionError(provider.explain(causes)) from None
    except resolvelib.resolvers.ResolutionTooDeep:
        raise ResolutionError(
            f"gave up after {_MAX_ROUNDS} rounds of backtracking without finding a resolution"
        ) from None

    chosen: dict[str, _Candidate] = dict(result.mapping)
    collections: list[LockedCollection] = []
    for c in sorted(chosen.values(), key=lambda c: c.name):
        dependencies = tuple(sorted({name for name, _ in c.version.deps}))
        if c.index != _LOCAL:
            requires_ansible = c.version.requires_ansible
            collections.append(
                LockedCollection(
                    c.name,
                    c.version.version,
                    source=IndexSource(by_name[c.index].config().root),
                    cksum=c.version.cksum,
                    size=c.version.size,
                    requires_ansible=requires_ansible.text if requires_ansible is not None else None,
                    dependencies=dependencies,
                )
            )
        elif (local := manifest.local.get(c.name)) is not None and isinstance(
            source := manifest.sources[c.name], PathSource
        ):
            collections.append(
                LockedCollection(
                    c.name,
                    local.version,
                    source=local_source(source.path, editable=source.editable),
                    requires_ansible=local.requires_ansible,
                    requirements=local.dependencies,
                    dependencies=dependencies,
                )
            )
    reasons = provider.reasons(chosen, result.criteria, locked, upgrade)
    return Resolution(tuple(collections), tuple(provider.warnings(chosen)), reasons)


def admits_lower_bound(collection: SpecifierSet, project: SpecifierSet) -> bool:
    """Whether a collection version's ``requires_ansible`` admits the project's, by lower bounds alone.

    The collection must not start later than the project: its upper bounds are ignored, as uv ignores dependencies'
    ``requires-python`` upper bounds. A project range with no lower bound filters nothing.
    """
    want = project.lower_bound()
    have = collection.lower_bound()
    if want is None or have is None:
        return True
    if have.version != want.version:
        return have.version < want.version
    return have.inclusive or not want.inclusive


def narrower_than(collection: SpecifierSet, project: SpecifierSet) -> bool:
    """Whether a collection version's upper bound ends inside the project's range, so the lock doesn't cover all of
    it."""
    theirs = collection.upper_bound()
    if theirs is None:
        return False
    ours = project.upper_bound()
    if ours is None or ours.version > theirs.version:
        return True
    return ours.version == theirs.version and ours.inclusive and not theirs.inclusive


def _kept(
    installed: _c.Mapping[str, _semver.Version], upgrade: bool | _c.Collection[str]
) -> dict[str, _semver.Version]:
    """The installed versions to prefer: all of them, unless ``upgrade`` says otherwise."""
    if upgrade is True:
        return {}
    return {name: version for name, version in installed.items() if upgrade is False or name not in upgrade}


def _preferences(locked: Lock | None, upgrade: bool | _c.Collection[str]) -> dict[str, LockedCollection]:
    if locked is None or upgrade is True:
        return {}
    keep_all = upgrade is False
    return {c.name: c for c in locked.collections if keep_all or c.name not in upgrade}  # pyright: ignore[reportOperatorIssue]


class _Provider(resolvelib.AbstractProvider[_Requirement, _Candidate, str]):
    def __init__(
        self,
        manifest: Manifest,
        indexes: dict[str, IndexClient],
        preferred: dict[str, LockedCollection],
        ansible_version: Version | None,
        report: _c.Callable[[str, int], None] | None = None,
        installed: dict[str, _semver.Version] | None = None,
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
        self._left_out: dict[str, dict[str, int]] = {}
        """Per collection, how many versions each filter left out, for explaining a failure."""
        self._unusable: dict[str, dict[str, str]] = {}
        """Per collection, versions that met every requirement but were skipped for unreadable metadata."""
        for name, pin in manifest.sources.items():
            if not isinstance(pin, PathSource) and pin.index not in indexes:
                raise ResolutionError(
                    f"[project.sources] takes {name} from index '{pin.index}', which isn't configured"
                    + _did_you_mean(pin.index, indexes)
                )

    def roots(self) -> list[_Requirement]:
        """The project's requirements and its groups', each from the index it comes from."""
        listed: list[tuple[str, str]] = [(r.text, self._origin) for r in self._manifest.collections]
        for group, items in self._manifest.groups.items():
            listed.extend((r.text, f"group '{group}'") for r in items)
        named = [*self._manifest.collections, *(r for items in self._manifest.groups.values() for r in items)]

        # Ask every index's listing at once, then each root's index file.
        for client in self._indexes.values():
            _ = client.collections()
        roots: list[_Requirement] = []
        for requirement, (_, parent) in zip(named, listed, strict=True):
            index = self._index_for(requirement.name)
            if index is None:
                known = [n for client in self._indexes.values() for n in client.collections()]
                names = ", ".join(f"'{i}'" for i in self._indexes)
                raise ResolutionError(
                    f"no configured index ({names}) has {requirement.name}" + _did_you_mean(requirement.name, known)
                )
            req, text = self._overridden(requirement.name, requirement.req, requirement.text)
            roots.append(_Requirement(requirement.name, req, index=index, parent=parent, text=text, first_party=True))
        self._prefetch(r.name for r in roots)
        return roots

    def identify(self, requirement_or_candidate: _Requirement | _Candidate) -> str:
        return requirement_or_candidate.name

    def get_preference(
        self,
        identifier: str,
        resolutions: _c.Mapping[str, _Candidate],
        candidates: _c.Mapping[str, _c.Iterator[_Candidate]],
        information: _c.Mapping[
            str, _c.Iterator[resolvelib.resolvers.RequirementInformation[_Requirement, _Candidate]]
        ],
        backtrack_causes: _c.Sequence[resolvelib.resolvers.RequirementInformation[_Requirement, _Candidate]],
    ) -> tuple[bool, bool, bool, str]:
        # What caused backtracking first, then exact pins, then what the project asked for, then by name, so the order
        # is the same every run.
        infos = list(information[identifier])
        causes = {c.requirement.name for c in backtrack_causes} | {
            c.parent.name for c in backtrack_causes if c.parent is not None
        }
        pinned = any(op == "==" for info in infos for op, _ in info.requirement.req.clauses)
        root = any(info.parent is None for info in infos)
        return (identifier not in causes, not pinned, not root, identifier)

    def find_matches(
        self,
        identifier: str,
        requirements: _c.Mapping[str, _c.Iterator[_Requirement]],
        incompatibilities: _c.Mapping[str, _c.Iterator[_Candidate]],
    ) -> list[_Candidate]:
        reqs = list(requirements[identifier])
        if self._report is not None:
            self._report(identifier, len(self._files))
        # Every requirement on a name agrees on its index: it's worked out from the name alone.
        index = reqs[0].index
        if index == _LOCAL:
            # The directory is the only version there is, at what its galaxy.yml says, whatever the indexes have.
            local = self._local[identifier]
            if not all(r.req.contains(local.version) for r in reqs):
                return []
            # Its requires_ansible is checked as an index version's is; the project's own has none here.
            if (reason := self._left_out_because(identifier, local, allow_yanked=True)) is not None:
                left_out = self._left_out.setdefault(identifier, {})
                left_out[reason] = left_out.get(reason, 0) + 1
                return []
            return [_Candidate(identifier, local, index)]
        file = None if index is None else self._file(index, identifier)
        if index is None or file is None:
            return []
        excluded = {c.version.version for c in incompatibilities[identifier]}
        preferred = self._preferred.get(identifier)
        if preferred is not None and preferred.source != IndexSource(self._indexes[index].config().root):
            preferred = None
        # The locked version, else the installed one: either is kept while it still fits.
        keep = preferred.version if preferred is not None else self._installed.get(identifier)

        left_out = self._left_out.setdefault(identifier, {})
        matches: list[CollectionVersion] = []
        for version in file.versions:
            if version.version in excluded or not all(r.req.contains(version.version) for r in reqs):
                continue
            # A yanked version is a candidate when it's locked, or pinned with == by the user, as uv allows.
            allow_yanked = keep == version.version or any(
                r.first_party and any(op == "==" for op, _ in r.req.clauses) for r in reqs
            )
            reason = self._left_out_because(identifier, version, allow_yanked=allow_yanked)
            if reason is not None:
                left_out[reason] = left_out.get(reason, 0) + 1
                continue
            matches.append(version)

        # Newest first, but every stable version before any pre-release, so a pre-release is only chosen when no
        # stable version fits: uv's default (if-necessary).
        matches.sort(key=lambda v: v.version, reverse=True)
        matches.sort(key=lambda v: v.version.is_prerelease)
        if keep is not None:
            # The locked or installed version first, so an unchanged manifest gives an unchanged lock.
            matches.sort(key=lambda v: v.version != keep)
        return [_Candidate(identifier, v, index) for v in matches]

    def is_satisfied_by(self, requirement: _Requirement, candidate: _Candidate) -> bool:
        return requirement.req.contains(candidate.version.version)

    def get_dependencies(self, candidate: _Candidate) -> list[_Requirement]:
        # The project's own collection has none here: its galaxy.yml's are the project's requirements.
        dependencies: list[_Requirement] = []
        for name, req in candidate.version.deps:
            text = _dependency_text(name, req)
            overridden = name in self._manifest.overrides
            req, text = self._overridden(name, req, text)
            dependencies.append(
                _Requirement(
                    name, req, index=self._index_for(name), parent=str(candidate), text=text, first_party=overridden
                )
            )
        return dependencies

    def warnings(self, chosen: _c.Mapping[str, _Candidate]) -> list[str]:
        out: list[str] = []
        for (index, name), file in sorted(self._files.items()):
            if file is None:
                continue
            if file.newer:
                out.append(f"{name}: {file.newer} versions in index '{index}' need a newer alai")
            if file.invalid:
                out.append(f"{name}: skipped {len(file.invalid)} unreadable lines in index '{index}'")
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
                and narrower_than(theirs, project)
            ):
                out.append(
                    f"{candidate} supports ansible-core {theirs}, which ends inside the project's requires_ansible "
                    + f"{project}"
                )
        return out

    def reasons(
        self,
        chosen: _c.Mapping[str, _Candidate],
        criteria: _c.Mapping[str, resolvelib.resolvers.Criterion[_Requirement, _Candidate]],
        locked: Lock | None,
        upgrade: bool | _c.Collection[str],
    ) -> dict[str, str]:
        """Why each collection that's new, or whose locked version wasn't kept, is in the resolution as it is.

        Worked out once the resolution is known, from the requirements on each collection and the locked version's
        own metadata. A locked version resolvelib passed over without any of these reasons gets none.
        """
        out: dict[str, str] = {}
        for name, candidate in sorted(chosen.items()):
            if self._manifest.root is not None and name == self._manifest.root.name:
                continue
            reqs = [info.requirement for info in criteria[name].information]
            was = locked.get(name) if locked is not None else None
            if was is None:
                parents = ", ".join(sorted({r.parent for r in reqs}))
                out[name] = f"{name} is needed by {parents}"
                continue
            if candidate.index == _LOCAL:
                source = self._manifest.sources[name]
                if not was.is_local() or str(was.source) != str(source):
                    out[name] = f"{name} now comes from {source}"
                elif was.version != candidate.version.version:
                    out[name] = f"{name}'s galaxy.yml now says {candidate.version.version}"
                continue
            root = self._indexes[candidate.index].config().root
            if was.version == candidate.version.version and was.source == IndexSource(root):
                continue
            reason = self._not_kept(name, was, candidate, reqs, chosen, criteria, upgrade)
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
        candidate: _Candidate,
        reqs: _c.Sequence[_Requirement],
        chosen: _c.Mapping[str, _Candidate],
        criteria: _c.Mapping[str, resolvelib.resolvers.Criterion[_Requirement, _Candidate]],
        upgrade: bool | _c.Collection[str],
    ) -> str | None:
        locked = f"{name} {was.version}"
        if upgrade is True or (upgrade is not False and name in upgrade):
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
        left_out = self._left_out_because(name, version, allow_yanked=True)
        if left_out is not None:
            return f"{locked} is left out ({left_out})"
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
            for info in criteria[dep].information:
                if info.parent is not None and info.parent.name == name:
                    continue
                theirs = info.requirement
                if not any(req.contains(v.version) and theirs.req.contains(v.version) for v in versions):
                    return f"{locked} needs {text}, which conflicts with {theirs.text} (from {theirs.parent})"
            fallback = fallback or f"{locked} needs {text}, but {other} was chosen"
        return fallback

    def explain(
        self, causes: _c.Collection[resolvelib.resolvers.RequirementInformation[_Requirement, _Candidate]]
    ) -> str:
        """Why resolution failed: each collection no version fits, with every requirement on it and where it came
        from, and why versions that would have fitted were left out."""
        by_name: dict[str, list[_Requirement]] = {}
        for cause in causes:
            requirement = cause.requirement
            if requirement not in by_name.setdefault(requirement.name, []):
                by_name[requirement.name].append(requirement)

        lines: list[str] = []
        for name, reqs in sorted(by_name.items()):
            index = reqs[0].index
            parents = ", ".join(sorted({r.parent for r in reqs}))
            if index == _LOCAL:
                local = self._local[name]
                root = self._manifest.root
                own = root is not None and name == root.name
                what = "the project's own collection" if own else f"from {self._manifest.sources[name]}"
                if left_out := self._left_out.get(name):
                    reason = next(iter(left_out))
                    lines.append(
                        f"{name} {local.version}, {what}, is left out ({reason}), which {', '.join(sorted({r.parent for r in reqs}))} needs"
                    )
                    continue
                lines.append(f"{name} {local.version}, {what}, doesn't meet:")
                lines.extend(f"  {r.text} (from {r.parent})" for r in reqs)
                continue
            if index is None:
                lines.append(f"no configured index has {name}, which {parents} needs")
                continue
            if self._files.get((index, name)) is None:
                # Its listing says it has it, but the file isn't there: the two are a sync apart.
                lines.append(f"index '{index}' doesn't have {name}, which {parents} needs")
                continue
            lines.append(f"no version of {name} meets every requirement on it:")
            lines.extend(f"  {r.text} (from {r.parent})" for r in reqs)
            if left_out := self._left_out.get(name):
                reasons = ", ".join(f"{count} {reason}" for reason, count in sorted(left_out.items()))
                lines.append(f"  versions left out: {reasons}")
        return "\n".join(lines) or "the requirements can't all be met"

    def _index_for(self, name: str) -> str | None:
        """Where a collection comes from, whatever asked for it: its ``[project.sources]`` entry, else the first
        index whose listing has it, as uv's default ``first-index`` strategy does. None if no index has it."""
        if name in self._local:
            return _LOCAL
        pin = self._manifest.sources.get(name)
        if pin is not None and not isinstance(pin, PathSource):
            return pin.index
        return next((index for index, client in self._indexes.items() if client.has(name)), None)

    def _prefetch(self, names: _c.Iterable[str]) -> None:
        """Start fetching the index files for ``names``, each from the index it comes from."""
        by_index: dict[str, set[str]] = {}
        for name in names:
            index = self._index_for(name)
            if index is not None and index != _LOCAL and (index, name) not in self._files:
                by_index.setdefault(index, set()).add(name)
        for index, wanted in by_index.items():
            self._indexes[index].prefetch(sorted(wanted))

    def _file(self, index: str, name: str) -> IndexFile | None:
        key = (index, name)
        if key not in self._files:
            file = self._files[key] = self._indexes[index].versions(name)
            if file is not None:
                # Every dependency any version names, fetched now rather than when the resolver gets to it.
                self._prefetch({dep for version in file.versions for dep, _ in version.deps})
        return self._files[key]

    def _overridden(self, name: str, req: VersionReq, text: str) -> tuple[VersionReq, str]:
        override = self._manifest.overrides.get(name)
        if override is None:
            return req, text
        return override, f"{name}{override.text if override.clauses else ''} ([project.overrides], replacing {text})"

    def _left_out_because(self, name: str, version: CollectionVersion, *, allow_yanked: bool) -> str | None:
        """Why a version that meets every requirement isn't a candidate, or None if it is."""
        if version.problem is not None:
            self._unusable.setdefault(name, {})[version.version.text] = version.problem
            return "with unreadable metadata"
        if version.yanked and not allow_yanked:
            return "yanked"
        theirs = version.requires_ansible
        if theirs is None or name in self._manifest.ignore_requires_ansible:
            return None
        if self._ansible_version is not None:
            # Resolving for one ansible-core: the version's whole range must contain it, as ansible-galaxy checks the
            # running ansible-core.
            if not theirs.contains(self._ansible_version):
                return f"not supporting ansible-core {self._ansible_version}"
            return None
        project = self._manifest.requires_ansible
        if project is not None and not admits_lower_bound(theirs, project):
            return f"needing a newer ansible-core than the project's {project}"
        return None


def _local_version(collection: SourceCollection, *, root: bool = False) -> CollectionVersion:
    """A collection in a directory as an index entry would describe it, with no artifact. The project's own has no
    dependencies or ``requires_ansible`` here: its galaxy.yml's are the project's requirements, and its
    ``meta/runtime.yml``'s the project's range."""
    requirements = [] if root else [Requirement.parse(text) for text in collection.dependencies]
    requires_ansible = (
        None if root or collection.requires_ansible is None else SpecifierSet(collection.requires_ansible)
    )
    return CollectionVersion(
        collection.name,
        collection.version,
        deps=tuple((r.name, r.req) for r in requirements),
        cksum="",
        size=0,
        requires_ansible=requires_ansible,
        yanked=False,
        origin="",
        published="",
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
