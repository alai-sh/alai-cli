# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Installing a collection from its source directory as ``ansible-galaxy collection build`` would build it, straight
into place: the files ``build_ignore`` or the ``manifest`` directives in ``galaxy.yml`` choose, with the
``MANIFEST.json`` and ``FILES.json`` a built artifact has, and symlinks made self-contained. A path source that isn't
editable is installed this way, and so is a git source, from its commit's files. The design is in ``design/install.md``.

It's meant to be shared with a future ``alai build``, so the two stay one file selection; following ansible-galaxy
is where it starts, not a requirement. Checked against ansible-core ``616e7c05e0``
(``lib/ansible/galaxy/collection/__init__.py``): ``install_src``, ``_build_manifest``, ``_build_files_manifest_walk``,
``_build_files_manifest_distlib``, ``_build_collection_dir`` and, for symlinks, ``_build_collection_tar``, and
``_normalize_galaxy_yml_manifest`` in ``concrete_artifact_manager.py``.
"""

from __future__ import annotations

import collections.abc as _c
import fnmatch
import hashlib
import json
import logging
import os
import shutil
import stat
import typing as _t

from alai_core.collection import Manifest
from alai_core.semver import Version

from ._common import Error, as_list, as_table, inside, sha256_file

_FORMAT = 1
"""``MANIFEST_FORMAT``, in both files."""
_REQUIRED = ("namespace", "name", "version", "readme", "authors")
"""``galaxy.yml``'s mandatory keys, from ansible-core's ``collections_galaxy_meta.yml``."""
_LISTS = ("authors", "license", "tags", "build_ignore")
_DICTS = ("dependencies",)
_KEYS = (
    *_REQUIRED,
    "tags",
    "description",
    "license",
    "license_file",
    "dependencies",
    "repository",
    "documentation",
    "homepage",
    "issues",
    "build_ignore",
    "manifest",
)
"""The keys of ``galaxy.yml`` a build uses: those ``_build_manifest`` writes to ``MANIFEST.json``, and the two that
choose its files."""
_MAX_VALUES = 100_000
"""How many values ``galaxy.yml``'s keys may hold, far more than a real one has, so a few aliases can't make
``MANIFEST.json`` enormous."""
_IGNORED_DIRS = frozenset(("CVS", ".bzr", ".hg", ".git", ".svn", "__pycache__", ".tox"))
_DOCUMENTABLE = (
    "become",
    "cache",
    "callback",
    "cliconf",
    "connection",
    "filter",
    "httpapi",
    "inventory",
    "lookup",
    "netconf",
    "shell",
    "strategy",
    "test",
    "vars",
)
"""The plugin directories, other than ``modules`` and ``module_utils``, whose YAML files ansible-galaxy's default
directives include: its plugin loaders' packages that are in ``DOCUMENTABLE_PLUGINS``, worked out from ansible-core
``616e7c05e0``."""


class BuildError(Error):
    """A source directory can't be installed as ansible-galaxy would install it."""


def install_dir(
    source: str, dest: str, name: str, version: Version, *, umask: int, source_version: bool = False
) -> None:
    """Install the collection in the directory ``source`` as the new directory ``dest``: built from its source when it
    has a ``galaxy.yml``, or copied as it is when it's built already, with only a ``MANIFEST.json``. Either way its
    metadata must still name it ``name`` at ``version``, as locked, unless ``source_version`` says the version is
    the one its ``[project.sources]`` entry gives, which a built one can't have."""
    has_galaxy = os.path.isfile(os.path.join(source, "galaxy.yml"))
    has_manifest = os.path.isfile(os.path.join(source, "MANIFEST.json"))
    if has_galaxy == has_manifest:
        what = "both a galaxy.yml and a MANIFEST.json" if has_galaxy else "no galaxy.yml or MANIFEST.json any more"
        raise BuildError(f"{name} is locked from {source}, which has {what}")
    if has_galaxy:
        build(source, dest, name, version, umask=umask, source_version=source_version)
    elif source_version:
        raise BuildError(f"{source} is a built collection now, whose version its source can't replace; run alai lock")
    else:
        copy_built(source, dest, name, version, umask=umask)


def build(source: str, dest: str, name: str, version: Version, *, umask: int, source_version: bool = False) -> None:
    """Install the collection whose source is the directory ``source`` as the new directory ``dest``, checking its
    ``galaxy.yml`` still names it ``name`` at ``version``, as locked. With ``source_version``, ``version`` is the one
    its ``[project.sources]`` entry gives, which ``MANIFEST.json`` gets whatever ``galaxy.yml`` says."""
    galaxy_file = os.path.join(source, "galaxy.yml")
    info = _galaxy_info(galaxy_file)
    found = f"{info['namespace']}.{info['name']}"
    if source_version:
        info["version"] = version.text
    if found != name or info["version"] != version.text:
        raise BuildError(
            f"{galaxy_file} is for {found} {info['version']} now, not {name} {version} as locked; run alai lock"
        )
    files = _files(source, info, galaxy_file)
    files.sort(key=lambda entry: str(entry["name"]))

    files_json = json.dumps({"files": files, "format": _FORMAT}, indent=True, sort_keys=True).encode()
    manifest = _manifest(info, hashlib.sha256(files_json).hexdigest())
    metadata = (("MANIFEST.json", json.dumps(manifest, indent=True).encode()), ("FILES.json", files_json))
    _install(source, dest, metadata, [(str(e["name"]), str(e["ftype"]), None) for e in files], umask=umask)


def copy_built(source: str, dest: str, name: str, version: Version, *, umask: int) -> None:
    """Install the built or installed collection in the directory ``source`` as the new directory ``dest``: its
    ``MANIFEST.json`` and the file it names, ``FILES.json`` as ansible-galaxy builds it, as they are, and what that
    lists, each file checked against its sha256 there, as an artifact is extracted. ``MANIFEST.json`` is the truth
    for what was built, so nothing is chosen or written again, as ansible-galaxy does when it walks such a directory,
    which would let in files the build didn't have."""
    manifest_file = os.path.join(source, "MANIFEST.json")
    manifest_bytes = _read(manifest_file)
    try:
        manifest = Manifest.parse(manifest_bytes, name=manifest_file)
    except ValueError as e:
        raise BuildError(str(e)) from e
    files_name = manifest.files_name
    files_file = os.path.join(source, files_name)
    if not os.path.isfile(files_file):
        raise BuildError(f"{source} has a MANIFEST.json but no {files_name}, so what it holds can't be checked")
    files_bytes = _read(files_file)

    info = manifest.collection_info
    found = f"{info.get('namespace')}.{info.get('name')}"
    if found != name or info.get("version") != version.text:
        raise BuildError(
            f"{manifest_file} is for {found} {info.get('version')} now, not {name} {version} as locked; run alai lock"
        )
    if hashlib.sha256(files_bytes).hexdigest() != manifest.files_sha256:
        raise BuildError(f"{files_file} doesn't match the checksum {manifest_file} gives it")
    files = _json_object(files_bytes, files_file)

    listed = as_list(files.get("files"))
    if listed is None:
        raise BuildError(f"{files_file} has no 'files' list")
    entries: list[tuple[str, str, str | None]] = []
    for item in listed:
        fields = as_table(item) or {}
        entry, ftype, sha256 = fields.get("name"), fields.get("ftype"), fields.get("chksum_sha256")
        if (
            not isinstance(entry, str)
            or ftype not in ("file", "dir")
            or (ftype == "file" and not isinstance(sha256, str))
        ):
            raise BuildError(f"{files_file} has an unreadable entry: {item!r}")
        if entry != "." and (entry.startswith("/") or any(p in ("", ".", "..") for p in entry.split("/"))):
            raise BuildError(f"{files_file} lists a path outside the collection: {entry}")
        if entry in ("MANIFEST.json", files_name):
            raise BuildError(f"{files_file} lists {entry}, which a build never does")
        entries.append((entry, str(ftype), sha256 if isinstance(sha256, str) else None))

    _install(source, dest, (("MANIFEST.json", manifest_bytes), (files_name, files_bytes)), entries, umask=umask)


def _install(
    source: str,
    dest: str,
    metadata: _c.Iterable[tuple[str, bytes]],
    entries: _c.Iterable[tuple[str, str, str | None]],
    *,
    umask: int,
) -> None:
    """Make the directory ``dest`` with each ``(name, data)`` metadata file, then copy ``entries`` into it."""
    os.mkdir(dest, 0o755 & ~umask)
    for file_name, data in metadata:
        path = os.path.join(dest, file_name)
        with open(path, "xb") as f:
            f.write(data)
        os.chmod(path, 0o644 & ~umask)
    _copy(source, dest, entries, umask=umask)


def _copy(source: str, dest: str, entries: _c.Iterable[tuple[str, str, str | None]], *, umask: int) -> None:
    """Copy each ``(name, ftype, sha256)`` entry from ``source`` to ``dest``, checking a file against its sha256 when
    one is given. Symlinks are made as ansible-galaxy's collection build makes them, so the copy is self-contained: a
    link to something in the collection becomes a relative link to it, and one to a file outside is replaced by that
    file."""
    real_source = os.path.realpath(source)
    for relative, ftype, sha256 in entries:
        if relative == ".":
            continue
        src = os.path.join(source, relative)
        dst = os.path.join(dest, relative)
        if not os.path.lexists(src):
            raise BuildError(f"{src} isn't there, though the collection lists it")
        if os.path.isdir(src) and not os.path.islink(src):
            os.makedirs(dst, 0o755 & ~umask, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(dst), 0o755 & ~umask, exist_ok=True)
        if os.path.islink(src):
            target = os.path.realpath(src)
            if not os.path.exists(target):
                raise BuildError(f"{src} is a symlink to {target}, which doesn't exist")
            if inside(target, real_source):
                os.symlink(os.path.relpath(target, os.path.dirname(os.path.join(real_source, relative))), dst)
                if sha256 is not None and _sha256(src) != sha256:
                    raise BuildError(f"{src} doesn't match its checksum in the collection's files manifest")
                continue
            if ftype == "dir":
                raise BuildError(f"{src} is a symlink to a directory outside the collection, {target}")
        # A file, or what a link leading outside points at, with its mode as the build gives it.
        shutil.copyfile(src, dst)
        if sha256 is not None and _sha256(dst) != sha256:
            raise BuildError(f"{src} doesn't match its checksum in the collection's files manifest")
        executable = os.stat(src).st_mode & stat.S_IXUSR
        os.chmod(dst, (0o755 if executable else 0o644) & ~umask)


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise BuildError(f"can't read {path}: {e.strerror or e}") from e


def _json_object(data: bytes, path: str) -> dict[str, object]:
    try:
        obj = json.loads(data)
    except ValueError as e:
        raise BuildError(f"{path}: not valid JSON: {e}") from e
    found = as_table(obj)
    if found is None:
        raise BuildError(f"{path}: expected a JSON object")
    return found


def _galaxy_info(galaxy_file: str) -> dict[str, object]:
    """``galaxy.yml`` with ansible-galaxy's defaults filled in: lists for its list keys (a single value wrapped in
    one), an empty mapping for ``dependencies``, None for any other string, and ``manifest`` only when it's set."""
    from ._galaxy import galaxy_metadata

    metadata = galaxy_metadata(galaxy_file)
    if missing := [key for key in _REQUIRED if key not in metadata]:
        raise BuildError(
            f"{galaxy_file} is missing {', '.join(missing)}, which ansible-galaxy needs to install the collection"
        )
    info = {key: metadata.get(key) for key in _KEYS if key in metadata}
    if _too_large(info):
        raise BuildError(f"{galaxy_file}: its aliases expand to far more than any collection's metadata has")
    for key in _LISTS:
        value = info.get(key)
        info[key] = [] if value is None else value if isinstance(value, list) else [value]
    for key in _DICTS:
        info.setdefault(key, {})
    for key in ("description", "license_file", "repository", "documentation", "homepage", "issues"):
        info.setdefault(key, None)
    if info["build_ignore"] and "manifest" in info:
        raise BuildError(f'{galaxy_file}: "build_ignore" and "manifest" are mutually exclusive')
    return info


def _too_large(value: object) -> bool:
    """Whether ``value`` has more than ``_MAX_VALUES`` values, counting each place an alias puts one, as JSON does."""
    count, pending = 0, [value]
    while pending:
        count += 1
        if count > _MAX_VALUES:
            return True
        item = pending.pop()
        if (table := as_table(item)) is not None:
            pending.extend(table.values())
        elif (items := as_list(item)) is not None:
            pending.extend(items)
    return False


def _manifest(info: dict[str, object], files_sha256: str) -> dict[str, object]:
    """``MANIFEST.json`` for ``info``, naming ``FILES.json`` by its checksum."""
    return {
        "collection_info": {
            "namespace": info["namespace"],
            "name": info["name"],
            "version": info["version"],
            "authors": info["authors"],
            "readme": info["readme"],
            "tags": info["tags"],
            "description": info["description"],
            "license": info["license"],
            "license_file": info["license_file"] or None,
            "dependencies": info["dependencies"],
            "repository": info["repository"],
            "documentation": info["documentation"],
            "homepage": info["homepage"],
            "issues": info["issues"],
        },
        "file_manifest_file": {
            "name": "FILES.json",
            "ftype": "file",
            "chksum_type": "sha256",
            "chksum_sha256": files_sha256,
            "format": _FORMAT,
        },
        "format": _FORMAT,
    }


def _entry(name: str, ftype: str, chksum: str | None = None) -> dict[str, object]:
    return {
        "name": name,
        "ftype": ftype,
        "chksum_type": "sha256" if chksum else None,
        "chksum_sha256": chksum,
        "format": _FORMAT,
    }


def _files(source: str, info: dict[str, object], galaxy_file: str) -> list[dict[str, object]]:
    files = [_entry(".", "dir")]
    if "manifest" in info:
        files.extend(_files_from_directives(source, info, galaxy_file))
    else:
        build_ignore = [str(v) for v in as_list(info["build_ignore"]) or []]
        files.extend(_files_walked(source, f"{info['namespace']}-{info['name']}", build_ignore))
    return files


def _files_walked(source: str, artifact_prefix: str, build_ignore: list[str]) -> _c.Iterator[dict[str, object]]:
    """Every file and directory ``build_ignore`` and the defaults don't leave out, as
    ``_build_files_manifest_walk`` lists them: a directory link inside the collection is listed but not walked, and
    one leading outside it is skipped."""
    from ._output import warn

    patterns = [
        "MANIFEST.json",
        "FILES.json",
        "galaxy.yml",
        "galaxy.yaml",
        ".git",
        "*.pyc",
        "*.retry",
        "tests/output",
        f"{artifact_prefix}-*.tar.gz",
        *build_ignore,
    ]

    def walk(directory: str, relative: str) -> _c.Iterator[dict[str, object]]:
        for item in os.listdir(directory):
            path = os.path.join(directory, item)
            rel = os.path.join(relative, item) if relative else item
            if os.path.isdir(path):
                if item in _IGNORED_DIRS or any(fnmatch.fnmatch(rel, p) for p in patterns):
                    continue
                if os.path.islink(path) and not inside(os.path.realpath(path), os.path.realpath(source)):
                    warn(f"skipping {path}, a symlink to a directory outside the collection, as ansible-galaxy does")
                    continue
                yield _entry(rel, "dir")
                if not os.path.islink(path):
                    yield from walk(path, rel)
            elif not any(fnmatch.fnmatch(rel, p) for p in patterns):
                yield _entry(rel, "file", _sha256(path))

    return walk(source, "")


def _files_from_directives(source: str, info: dict[str, object], galaxy_file: str) -> list[dict[str, object]]:
    """The files the ``manifest`` key's directives choose, through distlib as ``_build_files_manifest_distlib`` uses
    it, with ansible-galaxy's default directives around them unless ``omit_default_directives`` is set."""
    # distlib has no type information; only these two names are used.
    from distlib import DistlibException  # pyright: ignore[reportMissingTypeStubs]
    from distlib.manifest import Manifest  # pyright: ignore[reportMissingTypeStubs]

    control: object = info["manifest"]
    settings = as_table({} if control is None else control)
    if settings is None or set(settings) - {"directives", "omit_default_directives"}:
        raise BuildError(f'{galaxy_file}: "manifest" may only have "directives" and "omit_default_directives"')
    given = as_list(settings.get("directives", []))
    omit = settings.get("omit_default_directives", False)
    if given is None:
        raise BuildError(f'{galaxy_file}: "manifest.directives" must be a list')
    if not isinstance(omit, bool):
        raise BuildError(f'{galaxy_file}: "manifest.omit_default_directives" must be a boolean')
    directives = [str(d) for d in given]
    if omit and not directives:
        raise BuildError(
            f'{galaxy_file}: "manifest.omit_default_directives" is true but "manifest.directives" is empty, which '
            + "would install nothing"
        )

    if not omit:
        defaults = [
            "include meta/*.yml",
            "include *.txt *.md *.rst *.license COPYING LICENSE",
            "recursive-include .reuse **",
            "recursive-include LICENSES **",
            "recursive-include tests **",
            "recursive-include docs **.rst **.yml **.yaml **.json **.j2 **.txt **.license",
            "recursive-include roles **.yml **.yaml **.json **.j2 **.license",
            "recursive-include playbooks **.yml **.yaml **.json **.license",
            "recursive-include changelogs **.yml **.yaml **.license",
            "recursive-include plugins */**.py */**.license",
        ]
        if info["license_file"]:
            defaults.append(f"include {info['license_file']}")
        defaults.extend(f"recursive-include plugins/{plugin} **.yml **.yaml" for plugin in _DOCUMENTABLE)
        defaults.extend(
            [
                "recursive-include plugins/modules **.ps1 **.yml **.yaml **.license",
                "recursive-include plugins/module_utils **.ps1 **.psm1 **.cs **.license",
            ]
        )
        directives = [
            *defaults,
            *directives,
            f"exclude galaxy.yml galaxy.yaml MANIFEST.json FILES.json {info['namespace']}-{info['name']}-*.tar.gz",
            "recursive-exclude tests/output **",
            "global-exclude /.* /__pycache__ *.pyc *.pyo *.bak *~ *.swp",
        ]

    # distlib logs a warning for each default directive that matches nothing, which most collections have plenty of.
    logging.getLogger("distlib.manifest").setLevel(logging.ERROR)
    manifest = Manifest(source)
    directive = ""
    try:
        for directive in directives:
            manifest.process_directive(directive)
    except DistlibException as e:
        raise BuildError(f"{galaxy_file}: invalid manifest directive '{directive}': {e}") from e
    # The collection's own directory is among them, so FILES.json lists "." twice, as ansible-galaxy's does.
    entries: list[dict[str, object]] = []
    paths = _t.cast("list[str]", manifest.sorted(wantdirs=True))
    for path in paths:
        relative = os.path.relpath(path, source)
        entries.append(_entry(relative, "dir") if os.path.isdir(path) else _entry(relative, "file", _sha256(path)))
    return entries


def _sha256(path: str) -> str | None:
    """A file's sha256, following a link to it, or None for a link with nothing behind it, as ansible-core's
    ``secure_hash`` gives."""
    return None if not os.path.exists(path) or os.path.isdir(path) else sha256_file(path)
