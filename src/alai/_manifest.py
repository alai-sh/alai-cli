# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The project manifest: the collections a project needs, from the project's ``alai.toml``.

The format is specified in ``docs/projects.md``. ``_config`` reads the settings in the same file, and checks the
manifest's keys and types on every command (``RawManifest``). This module reads what the values mean, when a command
uses the manifest, so the version parsing it imports only costs those commands.
"""

from __future__ import annotations

import keyword
import re

from ._common import NAME_RE, ConfigError, Record
from ._config import Project, RawManifest, raw_manifest, read_toml
from ._pep440 import SpecifierSet
from ._semver import VersionReq

_NAME_PART_RE = re.compile(r"[a-z_][a-z0-9_]*")
# The name, up to the first character that can only start a version requirement.
_REQUIREMENT_RE = re.compile(r"\s*(?P<name>[^\s<>=!,*^~]*)\s*(?P<req>.*)", re.DOTALL)


class Requirement(Record):
    """A collection the project needs, such as ``community.general>=9,<11``: ansible-galaxy's syntax, so it reads the
    same as in ``requirements.yml`` or ``galaxy.yml``. Where it comes from is ``[project.sources]``'s business."""

    __slots__ = ("name", "req", "text")

    def __init__(self, name: str, req: VersionReq, text: str = "") -> None:
        self.name = name
        """``namespace.name``."""
        self.req = req
        self.text = text or name
        """As written, for messages."""

    @classmethod
    def parse(cls, text: str) -> Requirement:
        """Parse ``NAMESPACE.NAME[REQUIREMENT]``, raising ValueError if it's invalid."""
        match = _REQUIREMENT_RE.fullmatch(text)
        if not match or not match.group("name"):
            raise ValueError(f"invalid requirement '{text}', expected a collection name such as community.general")
        name = collection_name(match.group("name"))
        req = match.group("req")
        try:
            version_req = VersionReq.parse(req) if req else VersionReq("*", ())
        except ValueError as e:
            raise ValueError(f"invalid requirement '{text}': {e}") from None
        return cls(name, version_req, text)

    def __str__(self) -> str:
        return self.text


class IndexPin(Record):
    """A ``[project.sources]`` entry taking a collection from one index, by its name in ``[[index]]``."""

    __slots__ = ("index",)

    def __init__(self, index: str) -> None:
        self.index = index


class Manifest(Record):
    """What the project's ``alai.toml`` says the project needs."""

    __slots__ = ("collections", "groups", "ignore_requires_ansible", "overrides", "path", "requires_ansible", "sources")

    def __init__(
        self,
        path: str,
        *,
        collections: tuple[Requirement, ...] = (),
        requires_ansible: SpecifierSet | None = None,
        groups: dict[str, tuple[Requirement, ...]] | None = None,
        overrides: dict[str, VersionReq] | None = None,
        ignore_requires_ansible: tuple[str, ...] = (),
        sources: dict[str, IndexPin] | None = None,
    ) -> None:
        self.path = path
        """The ``alai.toml`` it was read from."""
        self.collections = collections
        self.requires_ansible = requires_ansible
        """The ansible-core versions the project targets, or None for any."""
        self.groups = groups or {}
        """Collections only some workflows need, by group name."""
        self.overrides = overrides or {}
        """Requirements that replace every requirement on a collection, by collection name."""
        self.ignore_requires_ansible = ignore_requires_ansible
        """Collections whose own ``requires_ansible`` is ignored."""
        self.sources = sources or {}
        """Where a collection comes from, by collection name, for any collection in the resolution, not only those
        the project lists. One without an entry comes from its parent's index, or the first index that has it."""

    def resolution_key(self) -> tuple[object, ...]:
        """What resolving this manifest depends on, comparable between two manifests.

        Built from the parsed values, so whitespace (``>= 9`` and ``>=9.0.0``), the order of a list, or which file
        it came from don't count: a lock made from one manifest is current for any other with the same key.
        """

        def requirements(items: tuple[Requirement, ...]) -> frozenset[tuple[object, ...]]:
            return frozenset((r.name, r.req.clauses) for r in items)

        return (
            requirements(self.collections),
            self.requires_ansible,
            frozenset((name, requirements(items)) for name, items in self.groups.items()),
            frozenset((name, req.clauses) for name, req in self.overrides.items()),
            frozenset(self.ignore_requires_ansible),
            frozenset((name, pin.index) for name, pin in self.sources.items()),
        )


def collection_name(text: str) -> str:
    """Check ``text`` is a valid ``namespace.name``, raising ValueError if not.

    Each part is a lowercase Python identifier that isn't a keyword: ansible-core's ``is_valid_collection_name``, plus
    the index spec's rule that indexes only hold lowercase names.
    """
    parts = text.split(".")
    if len(parts) != 2:
        raise ValueError(f"invalid collection name '{text}', expected namespace.name")
    for part in parts:
        if not _NAME_PART_RE.fullmatch(part) or keyword.iskeyword(part):
            hint = ", collection names are lowercase" if part.lower() != part else ""
            raise ValueError(
                f"invalid collection name '{text}': '{part}' must be a Python identifier that isn't a keyword{hint}"
            )
    return text


def load_manifest(project: Project) -> Manifest | None:
    """Read the manifest from the project's ``alai.toml``, or None if it has none.

    The manifest is what the project needs rather than how alai runs, so ``--no-config`` and ``--config-file``
    don't change where it's read from.
    """
    if project.config_file is None:
        return None
    return parse_manifest(raw_manifest(read_toml(project.config_file), project.config_file))


def parse_manifest(raw: RawManifest) -> Manifest:
    path = raw.path
    collections = _requirements(raw.collections, path, "project.collections")

    requires_ansible = None
    if raw.requires_ansible is not None:
        if not raw.requires_ansible.strip(",").strip():
            raise ConfigError(f"{path}: 'project.requires_ansible' is empty")
        try:
            requires_ansible = SpecifierSet(raw.requires_ansible)
        except ValueError as e:
            raise ConfigError(f"{path}: project.requires_ansible: {e}, expected PEP 440 such as '>=2.17'") from None

    groups: dict[str, tuple[Requirement, ...]] = {}
    for name, items in raw.groups.items():
        if not NAME_RE.fullmatch(name):
            raise ConfigError(
                f"{path}: invalid group name '{name}', use lowercase letters, digits, '-' and '_', "
                + "starting with a letter or digit"
            )
        groups[name] = _requirements(items, path, f"project.groups.{name}.collections")

    overrides: dict[str, VersionReq] = {}
    for key, value in raw.overrides.items():
        where = f'project.overrides."{key}"'
        try:
            overrides[collection_name(key)] = VersionReq.parse(value)
        except ValueError as e:
            raise ConfigError(f"{path}: {where}: {e}") from None

    sources: dict[str, IndexPin] = {}
    for key, table in raw.sources.items():
        where = f'project.sources."{key}"'
        try:
            name = collection_name(key)
        except ValueError as e:
            raise ConfigError(f"{path}: {where}: {e}") from None
        index = table.get("index")
        if set(table) != {"index"} or not isinstance(index, str):
            kinds = ", ".join(sorted(table)) or "none"
            raise ConfigError(f'{path}: {where}: only {{ index = "<name>" }} is supported so far, got {kinds}')
        if not NAME_RE.fullmatch(index):
            raise ConfigError(f"{path}: {where}: '{index}' isn't a valid index name")
        sources[name] = IndexPin(index)

    return Manifest(
        path,
        collections=collections,
        requires_ansible=requires_ansible,
        groups=groups,
        overrides=overrides,
        ignore_requires_ansible=_names(raw.ignore_requires_ansible, path, "project.overrides.ignore_requires_ansible"),
        sources=sources,
    )


def _requirements(items: list[str], path: str, key: str) -> tuple[Requirement, ...]:
    requirements: list[Requirement] = []
    seen: dict[str, int] = {}
    for i, item in enumerate(items):
        try:
            requirement = Requirement.parse(item)
        except ValueError as e:
            raise ConfigError(f"{path}: {key}[{i}]: {e}") from None
        if (first := seen.setdefault(requirement.name, i)) != i:
            raise ConfigError(
                f"{path}: {key}[{i}]: '{requirement.name}' is already listed at {key}[{first}], "
                + "combine them into one requirement"
            )
        requirements.append(requirement)
    return tuple(requirements)


def _names(names: list[str], path: str, key: str) -> tuple[str, ...]:
    i = 0
    try:
        for i, name in enumerate(names):  # noqa: B007  # i is read in the except clause
            _ = collection_name(name)
    except ValueError as e:
        raise ConfigError(f"{path}: {key}[{i}]: {e}") from None
    return tuple(dict.fromkeys(names))
