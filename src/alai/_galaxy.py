# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A collection directory's own metadata, read as ansible-galaxy and ansible-core read it: ``galaxy.yml`` for a source,
such as a collection project or a path source, or ``MANIFEST.json`` for a built or installed collection, with
``meta/runtime.yml`` for either. The design is in ``design/collection-development.md`` and ``design/install.md``.
"""

from __future__ import annotations

import collections.abc as _c
import os
import typing as _t

from alai_core.collection import Manifest, Metadata, MetadataError, Runtime
from alai_core.semver import Version
from alai_core.yaml import YamlError, YamlWarning

from ._common import ConfigError
from ._manifest import SourceCollection, collection_name
from ._output import warn

GALAXY_FILE_NAME = "galaxy.yml"
MANIFEST_FILE_NAME = "MANIFEST.json"


def read_collection_dir(
    path: str, version: Version | None = None, *, source: str = "", shown: str | None = None
) -> SourceCollection:
    """The collection in the directory ``path``: a source, from its ``galaxy.yml``, or a built or installed
    collection, from its ``MANIFEST.json``. A directory with both is an error, as ansible-galaxy makes it: a
    ``MANIFEST.json`` left in a source is usually stale, and a ``galaxy.yml`` in a built collection isn't what was
    built, so neither can be chosen safely.

    ``version``, from the ``[project.sources]`` entry ``source`` names, replaces a source's version, which may be a
    placeholder its release fills in; a built collection's was checked when it was built, so it can't be replaced.
    ``shown`` is what errors and warnings call ``path``, such as a git commit's ``<url>@<commit>`` in place of where
    its files are in the cache.
    """
    galaxy_file = os.path.join(path, GALAXY_FILE_NAME)
    manifest_file = os.path.join(path, MANIFEST_FILE_NAME)
    where = path if shown is None else shown
    has_galaxy, has_manifest = os.path.isfile(galaxy_file), os.path.isfile(manifest_file)
    if has_galaxy and has_manifest:
        raise ConfigError(
            f"{where} has both a galaxy.yml and a MANIFEST.json, so it's unclear whether it's a collection's source "
            + "or a built collection: remove the MANIFEST.json from a source, or the galaxy.yml from a built collection"
        )
    if has_manifest:
        if version is not None:
            raise ConfigError(
                f"{where} is a built collection, whose MANIFEST.json gives its version, so {source or 'its source'} "
                + "can't give another: remove 'version' from it"
            )
        return read_built_collection(manifest_file, shown=shown)
    return read_source_collection(galaxy_file, version, source=source, shown=shown)


def read_source_collection(
    galaxy_file: str, version: Version | None = None, *, source: str = "", shown: str | None = None
) -> SourceCollection:
    """Read the collection whose ``galaxy.yml`` is ``galaxy_file``, raising ConfigError for anything ansible-galaxy
    would refuse to build or install, its version replaced by ``version``, and its directory called ``shown`` in
    messages, as ``read_collection_dir`` says."""
    info = galaxy_metadata(galaxy_file, shown=shown)
    return _collection(galaxy_file, info, version, source=source, shown=shown)


def galaxy_metadata(galaxy_file: str, *, shown: str | None = None) -> Metadata:
    """The ``galaxy.yml`` at ``galaxy_file``, its YAML warnings shown, unchecked but for being a mapping; its
    directory is called ``shown`` in messages, as ``read_collection_dir`` says."""
    file = _shown_file(galaxy_file, shown)
    data = _t.cast(bytes, _read(galaxy_file, required=True, shown=file))
    try:
        return Metadata.from_galaxy_yml(data, on_warning=_warner(file))
    except YamlError as e:
        raise ConfigError(f"{file} isn't valid YAML: {e}") from e
    except MetadataError as e:
        raise ConfigError(f"{file}: {e}") from e


def read_built_collection(manifest_file: str, *, shown: str | None = None) -> SourceCollection:
    """Read the built or installed collection whose ``MANIFEST.json`` is ``manifest_file``, from its
    ``collection_info``, which holds what ``galaxy.yml`` did when it was built; its directory is called ``shown`` in
    messages, as ``read_collection_dir`` says."""
    file = _shown_file(manifest_file, shown)
    try:
        with open(manifest_file, "rb") as f:
            manifest = Manifest.parse(f.read(), name=file)
    except OSError as e:
        raise ConfigError(f"can't read {file}: {e.strerror or e}") from e
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return _collection(manifest_file, manifest.collection_info, shown=shown)


def _shown_file(metadata_file: str, shown: str | None) -> str:
    """``metadata_file`` as messages name it: in ``shown``, when given, in place of its directory."""
    return metadata_file if shown is None else os.path.join(shown, os.path.basename(metadata_file))


def _collection(
    metadata_file: str,
    info: Metadata,
    override: Version | None = None,
    *,
    source: str = "",
    shown: str | None = None,
) -> SourceCollection:
    """The collection ``metadata_file`` describes, as ``info`` read it, with ``requires_ansible`` from the
    ``meta/runtime.yml`` beside it, which neither file carries. ``override`` replaces its version, with a warning when
    the file has a valid one of its own, as it then probably isn't needed any more; ``source`` names where the
    collection was given, a ``[project.sources]`` entry or the command line, if anywhere, so an invalid version can
    say where to give one. Messages name the files in ``shown``, when given, in place of their directory."""
    path = os.path.dirname(metadata_file)
    file = _shown_file(metadata_file, shown)
    if source.startswith("[project.sources]"):
        hint = f"; if it's a placeholder, give the version in {source}, such as version = '1.0.0'"
    elif source.startswith("git+"):
        hint = (
            "; if it's a placeholder, give the version after the URL's #, such as #version=1.0.0, joined to "
            + "subdirectory with &"
        )
    elif source:
        hint = (
            "; if it's a placeholder, write its [project.sources] entry in alai.toml by hand, with the version, such "
            + "as version = '1.0.0'"
        )
    else:
        hint = ""
    errors: list[tuple[str | None, str]] = []
    fqcn = version = requirements = None
    try:
        fqcn = info.get_fqcn()
    except MetadataError as e:
        errors.extend(e.errors)
    if override is None:
        try:
            version = info.get_version()
        except MetadataError as e:
            errors.extend(e.errors)
    else:
        # The version given is used whatever the file says, so it only has to be right to warn about it.
        version = override
        try:
            own = info.get_version()
        except MetadataError:
            pass
        else:
            if own == override:
                warn(f"{file} has version {own} too, so {source}'s 'version' isn't needed")
            else:
                warn(f"{file} has version {own}, but {source} gives {override}, which is used")
    try:
        requirements = info.get_dependencies()
    except MetadataError as e:
        errors.extend(e.errors)
    if fqcn is None or version is None or requirements is None:
        raise ConfigError("\n".join(_problem(file, f, m, hint) for f, m in errors))
    name = collection_name(fqcn)
    dependencies = [
        f"{dependency}{','.join(f'{op}{v.text}' for op, v in req.clauses)}" for dependency, req in requirements.items()
    ]

    runtime_file = os.path.join(path, "meta", "runtime.yml")
    runtime_shown = os.path.join(path if shown is None else shown, "meta", "runtime.yml")
    requires_ansible = _runtime(runtime_file, runtime_shown)

    return SourceCollection(
        name,
        version,
        path,
        dependencies=tuple(dependencies),
        requires_ansible=requires_ansible,
        metadata=os.path.basename(metadata_file),
    )


def _problem(file: str, field: str | None, message: str, hint: str) -> str:
    """One of core's metadata problems as alai says it: the file, then the field, unless the message names it."""
    named = message if field is None or message.startswith("'") else f"{field}: {message}"
    return f"{file}: {named}{hint if field == 'version' else ''}"


def _warner(file: str) -> _c.Callable[[YamlWarning], None]:
    return lambda w: warn(f"{file}: {w}")


def _runtime(path: str, shown: str) -> str | None:
    """``requires_ansible`` from the ``meta/runtime.yml`` at ``path``, if it has one, checked as ansible-galaxy does."""
    data = _read(path, required=False, shown=shown)
    if data is None:
        return None
    try:
        runtime = Runtime.parse(data, on_warning=_warner(shown))
    except YamlError as e:
        raise ConfigError(f"{shown} isn't valid YAML: {e}") from e
    try:
        requires_ansible = runtime.get_requires_ansible()
    except MetadataError as e:
        # ansible-core only warns about one it can't read, but alai can't choose versions by it.
        hint = "; ansible-core only warns about it, but alai can't choose versions by it"
        lines = (_problem(shown, f, m, "") + (hint if f == "requires_ansible" else "") for f, m in e.errors)
        raise ConfigError("\n".join(lines)) from e
    return None if requires_ansible is None else requires_ansible.text


def _read(path: str, *, required: bool, shown: str) -> bytes | None:
    """The file at ``path``, or None if, unless ``required``, it doesn't exist."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError as e:
        if required:
            raise ConfigError(f"{shown} doesn't exist") from e
        return None
    except OSError as e:
        raise ConfigError(f"can't read {shown}: {e.strerror or e}") from e
