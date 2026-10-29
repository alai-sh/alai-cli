# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The project manifest: the collections a project needs, from the project's ``alai.toml``.

The format is specified in ``docs/projects.md``. ``_config`` reads the settings in the same file, and checks the
manifest's keys and types on every command (``RawManifest``). This module reads what the values mean, when a command
uses the manifest, so the version parsing it imports only costs those commands.
"""

from __future__ import annotations

import os
import re
import typing as _t

from alai_core.collection import check_name
from alai_core.pep440 import SpecifierSet
from alai_core.semver import Version, VersionReq

from ._common import NAME_RE, ConfigError, Record, check_name_of, file_url_to_path
from ._config import RawManifest

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
            version_req = VersionReq(req) if req else VersionReq("*")
        except ValueError as e:
            raise ValueError(f"invalid requirement '{text}': {e}") from e
        return cls(name, version_req, text)

    def __str__(self) -> str:
        return self.text


class IndexPin(Record):
    """A ``[project.sources]`` entry taking a collection from one index, by its name in ``[[index]]``."""

    __slots__ = ("index",)

    def __init__(self, index: str) -> None:
        self.index = index


class PathSource(Record):
    """A ``[project.sources]`` entry taking a collection from a directory holding its ``galaxy.yml``: copied into the
    collections directory as ansible-galaxy installs one, or with ``editable``, linked to it so edits are seen. A path
    to a file is a built collection's tarball, installed as an index's artifact is; which it is is read from disk, as
    uv tells an archive from a directory."""

    __slots__ = ("editable", "path", "version")

    def __init__(self, path: str, *, editable: bool = False, version: Version | None = None) -> None:
        self.path = path
        """As written: relative to the project root, or absolute."""
        self.editable = editable
        self.version = version
        """The version to take it as, in place of its ``galaxy.yml``'s, which may be a placeholder a release script
        fills in; None to use ``galaxy.yml``'s. Only for a collection's source, not a built one."""

    def __str__(self) -> str:
        return f"{self.path} (editable)" if self.editable else self.path


class GitSource(Record):
    """A ``[project.sources]`` entry taking a collection from a git repository, as uv's ``{ git = ... }``: ``rev``, a
    branch, tag or full commit, is locked to its commit, and the collection is the source at ``subdirectory`` of that
    commit, copied as a path source's is. ``version`` is as for a path source."""

    __slots__ = ("rev", "subdirectory", "url", "version")

    def __init__(
        self, url: str, *, rev: str | None = None, subdirectory: str | None = None, version: Version | None = None
    ) -> None:
        self.url = url
        self.rev = rev
        """The branch, tag or full commit; None for the repository's default branch."""
        self.subdirectory = subdirectory
        """The collection's directory within the repository, ``/``-separated; None for its root."""
        self.version = version

    def __str__(self) -> str:
        """As it's given on the command line: ``git+<url>@<rev>#subdirectory=<dir>&version=<version>``."""
        rev = f"@{self.rev}" if self.rev is not None else ""
        keys = [f"subdirectory={self.subdirectory}"] if self.subdirectory is not None else []
        if self.version is not None:
            keys.append(f"version={self.version}")
        return f"git+{self.url}{rev}" + (f"#{'&'.join(keys)}" if keys else "")


class UrlSource(Record):
    """A ``[project.sources]`` entry taking a collection from a built collection's tarball at an ``http`` or ``https``
    URL, as uv's ``{ url = ... }``: downloaded when it's locked, and pinned by its checksum."""

    __slots__ = ("url",)

    def __init__(self, url: str) -> None:
        self.url = url

    def __str__(self) -> str:
        return self.url


Source = IndexPin | PathSource | UrlSource | GitSource
"""Where ``[project.sources]`` takes a collection from."""


class SourceCollection(Record):
    """A collection in a directory, such as the project's root or a path source, or in a tarball: what its metadata
    names it, and what it needs. A source has a ``galaxy.yml``; a built or installed collection, or a tarball, has a
    ``MANIFEST.json`` instead."""

    __slots__ = ("cksum", "commit", "dependencies", "metadata", "name", "path", "requires_ansible", "size", "version")

    def __init__(
        self,
        name: str,
        version: Version,
        path: str,
        *,
        dependencies: tuple[str, ...] = (),
        requires_ansible: str | None = None,
        metadata: str = "galaxy.yml",
        cksum: str | None = None,
        size: int | None = None,
        commit: str | None = None,
    ) -> None:
        self.name = name
        """``namespace.name``."""
        self.version = version
        self.path = path
        """The directory holding its metadata."""
        self.metadata = metadata
        """The file it was read from: ``galaxy.yml`` for a source, which is built to install it, or ``MANIFEST.json``
        for a built collection, which is copied as it is."""
        self.dependencies = dependencies
        """The metadata's ``dependencies`` as requirement strings in ``alai.toml``'s syntax, such as
        ``ansible.utils>=2.0.0``, in the order written."""
        self.requires_ansible = requires_ansible
        """``meta/runtime.yml``'s ``requires_ansible``, or None when it doesn't set one."""
        self.cksum = cksum
        """For a tarball, its sha256, which pins it as an index's artifact is pinned; None for a directory."""
        self.size = size
        """For a tarball, its size in bytes."""
        self.commit = commit
        """For a git source, the commit it was read from; None otherwise."""

    def is_archive(self) -> bool:
        return self.cksum is not None


class Manifest(Record):
    """What the project's ``alai.toml`` says the project needs."""

    __slots__ = (
        "collections",
        "groups",
        "ignore_requires_ansible",
        "local",
        "overrides",
        "path",
        "requires_ansible",
        "root",
        "sources",
    )

    def __init__(
        self,
        path: str,
        *,
        root: SourceCollection | None = None,
        collections: tuple[Requirement, ...] = (),
        requires_ansible: SpecifierSet | None = None,
        groups: dict[str, tuple[Requirement, ...]] | None = None,
        overrides: dict[str, VersionReq] | None = None,
        ignore_requires_ansible: tuple[str, ...] = (),
        sources: dict[str, Source] | None = None,
        local: dict[str, SourceCollection] | None = None,
    ) -> None:
        self.path = path
        """The ``alai.toml`` it was read from, or a collection project's ``galaxy.yml`` without one."""
        self.root = root
        """A collection project's own collection, whose ``galaxy.yml`` and ``meta/runtime.yml`` give ``collections``
        and ``requires_ansible``, or None in a playbook project."""
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
        self.local = local or {}
        """The collections ``sources`` takes from a directory or a tarball, by name, as their ``galaxy.yml`` or
        ``MANIFEST.json`` describes them now. Read by ``_project``, not from ``alai.toml``, so empty in a manifest a
        lock copied."""

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
            frozenset(
                (name, type(source), *(getattr(source, n) for n in source.__slots__))
                for name, source in self.sources.items()
            ),
        )


def is_git(text: str) -> bool:
    """Whether a requirement given on the command line is a git repository: ``git+<url>``, as uv and pip write one."""
    return text.lower().startswith("git+")


def parse_git(text: str) -> GitSource:
    """The git source ``git+<url>[@<rev>][#subdirectory=<dir>&version=<version>]`` names, as ``uv add`` reads one: the
    revision follows the last ``@`` in the URL's path, so ``git+ssh://git@host/repo`` has none, and the keys after the
    ``#`` are joined with ``&``, as pip and uv join them. ``version`` is the source's, as in ``[project.sources]``. uv
    ignores a key it doesn't know; here it's an error, so a misspelt one isn't dropped without a word. ConfigError if
    it's malformed."""
    import urllib.parse

    url, _, fragment = text[len("git+") :].partition("#")
    keys: dict[str, str] = {}
    for item in fragment.split("&") if fragment else []:
        key, _, value = item.partition("=")
        if key not in ("subdirectory", "version") or not value:
            raise ConfigError(
                f"{text}: after the # a git URL takes subdirectory=<dir> and version=<version>, joined with &, "
                + f"not '{item}'"
            )
        if key in keys:
            raise ConfigError(f"{text}: '{key}' is given twice")
        keys[key] = value
    subdirectory = _subdirectory(keys["subdirectory"], text) if "subdirectory" in keys else None
    version = None
    if "version" in keys:
        try:
            version = Version(keys["version"])
        except ValueError as e:
            raise ConfigError(f"{text}: version: {e}") from e
    _git_url(url, text, prefix="git+")
    parts = urllib.parse.urlsplit(url)
    rev = None
    if "@" in parts.path:
        path, rev = parts.path.rsplit("@", 1)
        if not rev:
            raise ConfigError(f"{text}: nothing follows the '@' that gives the revision")
        url = urllib.parse.urlunsplit(parts._replace(path=path))
    if not parts.scheme or (not parts.netloc and parts.scheme != "file"):
        raise ConfigError(f"{text}: expected git+ and a URL, such as git+https://github.com/org/repo")
    return GitSource(url, rev=rev, subdirectory=subdirectory, version=version)


def _git_url(url: str, where: str, *, prefix: str = "") -> None:
    """Check a git source's URL is one, ``<scheme>://...``, as uv requires: an scp-like ``user@host:path``, as GitHub
    gives one to clone over ssh, is refused with the ``ssh://`` URL that reaches it, as is a path, with the ``file://``
    URL; ``prefix`` is written before a suggested URL, ``git+`` on the command line. ConfigError if it isn't."""
    if "://" in url:
        return
    host, colon, path = url.partition(":")
    # A scheme with its slashes mistyped, such as https:/host, isn't a host.
    if colon and "/" not in host and host.lower() not in ("http", "https", "ssh", "git", "file"):
        # The path is relative to the user's home on the server, which the hosts people use ignore.
        suggested = f"{prefix}ssh://{host}/{path.lstrip('/')}"
        raise ConfigError(
            f"{where}: '{url}' is git's scp-like form, which isn't a URL: write it as {suggested}, or "
            + f"{prefix}ssh://{host}/~/{path} for a repository under the user's home on a server of your own"
        )
    raise ConfigError(
        f"{where}: '{url}' isn't a URL, such as https://github.com/org/repo or ssh://git@github.com/org/repo.git; a "
        + "repository on disk is a file:// URL"
    )


def _subdirectory(value: str, where: str) -> str:
    """A git source's ``subdirectory``, which must stay inside the repository."""
    parts = value.strip("/").split("/")
    if value.startswith("/") or any(p in ("", "..") for p in parts):
        raise ConfigError(f"{where}: subdirectory '{value}' must be a relative path inside the repository")
    normal = "/".join(p for p in parts if p != ".")
    return normal or "."


def given_source(text: str) -> tuple[str, ...] | None:
    """What a source given on the command line, such as to ``alai add``, is, comparable with ``source_key``: a git
    repository's URL and directory, whatever its revision; a tarball's URL; or a directory's or tarball's path,
    resolved from the current directory. None if ``text`` isn't one, or can't be read as one."""
    try:
        if is_git(text):
            git = parse_git(text)
            return ("git", git.url, git.subdirectory or ".")
        if is_url(text) and not text.lower().startswith("file:"):
            return ("url", text)
        if is_url(text) or is_path(text):
            path = file_url_path(text) if is_url(text) else text
            return ("path", os.path.realpath(os.path.expanduser(path)))
    except ConfigError:
        return None
    return None


def source_key(table: dict[str, object], root: str) -> tuple[str, ...] | None:
    """What a ``[project.sources]`` entry, or an install record's ``source``, takes a collection from, as
    ``given_source`` gives it; a relative path is relative to ``root``. None for an index, which no command line names
    this way."""
    git, url = table.get("git"), table.get("url")
    path = next((table[k] for k in ("path", "directory", "editable") if isinstance(table.get(k), str)), None)
    if isinstance(git, str):
        subdirectory = table.get("subdirectory")
        return ("git", git, subdirectory if isinstance(subdirectory, str) else ".")
    if isinstance(url, str):
        return ("url", url)
    if isinstance(path, str):
        return ("path", os.path.realpath(os.path.join(root, os.path.expanduser(path))))
    return None


def is_url(text: str) -> bool:
    """Whether a requirement given on the command line is a tarball's URL: ``http://``, ``https://`` or ``file://``,
    in any case, as ansible-galaxy reads a scheme."""
    return text.lower().startswith(("http://", "https://", "file://"))


def file_url_path(url: str) -> str:
    """The path a ``file://`` URL given on the command line names, as uv writes one as a path source; ConfigError if
    it names another host, or has a ``#`` part, which uv ignores but a path source has no keys for: one that's dropped
    without a word would install something other than what was asked for."""
    import urllib.parse

    if urllib.parse.urlsplit(url).fragment:
        raise ConfigError(
            f"{url}: a file URL takes nothing after a #; a directory's version, if its galaxy.yml has a placeholder, "
            + "goes in its [project.sources] entry"
        )
    try:
        return file_url_to_path(url)
    except ValueError as e:
        raise ConfigError(f"{url}: {e}") from e


def is_path(text: str) -> bool:
    """Whether a requirement given on the command line is a directory or a tarball rather than a collection: it has a
    ``/``, is ``.``, ``..``, starts with ``~``, or ends in ``.tar.gz``, as uv tells ``./lib``, ``lib/`` or
    ``lib-1.0.tar.gz`` from the name ``lib``. A collection name never does, so ``community.general`` is always the
    name, as ansible-galaxy reads it, even beside a directory of that name. A URL has a ``/`` too: ``is_url`` comes
    first."""
    return "/" in text or text in (".", "..") or text.startswith("~") or text.endswith(".tar.gz")


def is_source(text: str) -> bool:
    """Whether a requirement given on the command line is a git repository, a tarball's URL, or a directory or
    tarball's path, rather than a collection name."""
    return is_git(text) or is_url(text) or is_path(text)


def collection_name(text: str) -> str:
    """Check ``text`` is a valid ``namespace.name``, raising ValueError if not.

    Each part is a lowercase Python identifier that isn't a keyword, as alai-core's ``check_name`` says.
    """
    check_name(text)
    return text


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
            raise ConfigError(f"{path}: project.requires_ansible: {e}, expected PEP 440 such as '>=2.17'") from e

    groups: dict[str, tuple[Requirement, ...]] = {}
    for name, items in raw.groups.items():
        check_name_of("group", name, path)
        groups[name] = _requirements(items, path, f"project.groups.{name}.collections")

    overrides: dict[str, VersionReq] = {}
    for key, value in raw.overrides.items():
        where = f'project.overrides."{key}"'
        try:
            overrides[collection_name(key)] = VersionReq(value)
        except ValueError as e:
            raise ConfigError(f"{path}: {where}: {e}") from e

    sources: dict[str, Source] = {}
    for key, table in raw.sources.items():
        where = f'project.sources."{key}"'
        try:
            name = collection_name(key)
        except ValueError as e:
            raise ConfigError(f"{path}: {where}: {e}") from e
        sources[name] = _source(table, f"{path}: {where}")

    return Manifest(
        path,
        collections=collections,
        requires_ansible=requires_ansible,
        groups=groups,
        overrides=overrides,
        ignore_requires_ansible=_names(raw.ignore_requires_ansible, path, "project.overrides.ignore_requires_ansible"),
        sources=sources,
    )


def _source(table: dict[str, object], where: str) -> Source:
    """One ``[project.sources]`` entry: ``{ index = "<name>" }``, ``{ path = "<dir or file>" }`` with an optional
    ``editable = true``, ``{ url = "<tarball>" }``, or ``{ git = "<url>" }`` with an optional ``rev`` and
    ``subdirectory``, as uv's ``[tool.uv.sources]`` writes them; a path or git source may also give a ``version``."""
    if "index" in table:
        index = table["index"]
        if set(table) != {"index"} or not isinstance(index, str):
            raise ConfigError(f'{where}: an index source is {{ index = "<name>" }} alone, got {_kinds(table)}')
        if not NAME_RE.fullmatch(index):
            raise ConfigError(f"{where}: '{index}' isn't a valid index name")
        return IndexPin(index)
    if "path" in table:
        directory, editable = table["path"], table.get("editable", False)
        if set(table) - {"path", "editable", "version"} or not isinstance(directory, str) or not directory:
            raise ConfigError(
                f'{where}: a path source is {{ path = "<dir or file>" }}, with editable = true or false and a '
                + f"version, got {_kinds(table)}"
            )
        if not isinstance(editable, bool):
            raise ConfigError(f"{where}: 'editable' must be true or false")
        return PathSource(directory, editable=editable, version=_version(table, where))
    if "git" in table:
        url, rev, subdirectory = table["git"], table.get("rev"), table.get("subdirectory")
        if set(table) - {"git", "rev", "subdirectory", "version"} or not isinstance(url, str) or not url:
            raise ConfigError(
                f'{where}: a git source is {{ git = "<url>" }}, with a rev, subdirectory and version, got '
                + f"{_kinds(table)}"
            )
        for key, value in (("rev", rev), ("subdirectory", subdirectory)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ConfigError(f"{where}: '{key}' must be a non-empty string")
        _git_url(url, where)
        return GitSource(
            url,
            rev=_t.cast("str | None", rev),
            subdirectory=_subdirectory(subdirectory, where) if isinstance(subdirectory, str) else None,
            version=_version(table, where),
        )
    if "url" in table:
        url = table["url"]
        if set(table) != {"url"} or not isinstance(url, str) or not url:
            raise ConfigError(f'{where}: a URL source is {{ url = "<tarball URL>" }} alone, got {_kinds(table)}')
        # As uv refuses a file:// URL here ("URL scheme is not allowed"): a local tarball is a path.
        if not url.lower().startswith(("http://", "https://")):
            raise ConfigError(
                f"{where}: '{url}' isn't an http or https URL; a tarball on disk is {{ path = \"<file>\" }}"
            )
        return UrlSource(url)
    raise ConfigError(
        f'{where}: expected {{ index = "<name>" }}, {{ path = "<dir or file>" }}, {{ url = "<tarball URL>" }} or '
        + f'{{ git = "<url>" }}, got {_kinds(table)}'
    )


def _version(table: dict[str, object], where: str) -> Version | None:
    """A source's ``version``, which replaces its ``galaxy.yml``'s."""
    text = table.get("version")
    if text is None:
        return None
    if not isinstance(text, str):
        raise ConfigError(f"{where}: 'version' must be a string, such as '1.0.0'")
    try:
        return Version(text)
    except ValueError as e:
        raise ConfigError(f"{where}: 'version': {e}") from e


def _kinds(table: dict[str, object]) -> str:
    return ", ".join(sorted(table)) or "none"


def _requirements(items: list[str], path: str, key: str) -> tuple[Requirement, ...]:
    requirements: list[Requirement] = []
    seen: dict[str, int] = {}
    for i, item in enumerate(items):
        try:
            requirement = Requirement.parse(item)
        except ValueError as e:
            raise ConfigError(f"{path}: {key}[{i}]: {e}") from e
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
            collection_name(name)
    except ValueError as e:
        raise ConfigError(f"{path}: {key}[{i}]: {e}") from e
    return tuple(dict.fromkeys(names))
