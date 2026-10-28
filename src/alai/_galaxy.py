# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A collection directory's own metadata, read as ansible-galaxy and ansible-core read it: ``galaxy.yml`` for a source,
such as a collection project or a path source, or ``MANIFEST.json`` for a built or installed collection, with
``meta/runtime.yml`` for either. The design is in ``design/collection-development.md`` and ``design/install.md``.

PyYAML is imported here only, so it only costs the commands that read one.
"""

from __future__ import annotations

import os

from alai_core.pep440 import SpecifierSet
from alai_core.semver import Version, VersionReq

from ._common import ConfigError, as_table, json_object
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
    info = yaml_mapping(galaxy_file, required=True, shown=_shown_file(galaxy_file, shown))
    return _collection(galaxy_file, info, version, source=source, shown=shown)


def read_built_collection(manifest_file: str, *, shown: str | None = None) -> SourceCollection:
    """Read the built or installed collection whose ``MANIFEST.json`` is ``manifest_file``, from its
    ``collection_info``, which holds what ``galaxy.yml`` did when it was built; its directory is called ``shown`` in
    messages, as ``read_collection_dir`` says."""
    file = _shown_file(manifest_file, shown)
    try:
        with open(manifest_file, "rb") as f:
            data = json_object(f.read())
    except OSError as e:
        raise ConfigError(f"can't read {file}: {e.strerror or e}") from None
    except ValueError as e:
        raise ConfigError(f"{file}: {e}") from None
    info = as_table(data.get("collection_info"))
    if info is None:
        raise ConfigError(f"{file}: expected a 'collection_info' object")
    return _collection(manifest_file, info, shown=shown)


def _shown_file(metadata_file: str, shown: str | None) -> str:
    """``metadata_file`` as messages name it: in ``shown``, when given, in place of its directory."""
    return metadata_file if shown is None else os.path.join(shown, os.path.basename(metadata_file))


def _collection(
    metadata_file: str,
    info: dict[str, object],
    override: Version | None = None,
    *,
    source: str = "",
    shown: str | None = None,
) -> SourceCollection:
    """The collection ``info``, the mapping read from ``metadata_file``, describes, with ``requires_ansible`` from the
    ``meta/runtime.yml`` beside it, which neither file carries. ``override`` replaces its version, with a warning when
    the file has a valid one of its own, as it then probably isn't needed any more; ``source`` names where the
    collection was given, a ``[project.sources]`` entry or the command line, if anywhere, so an invalid version can say
    where to give one. Messages name the files in ``shown``, when given, in place of their directory."""
    path = os.path.dirname(metadata_file)
    file = _shown_file(metadata_file, shown)
    namespace, short = info.get("namespace"), info.get("name")
    if not isinstance(namespace, str) or not isinstance(short, str):
        raise ConfigError(f"{file}: 'namespace' and 'name' are required strings")
    try:
        name = collection_name(f"{namespace}.{short}")
    except ValueError as e:
        raise ConfigError(f"{file}: {e}") from None

    text = info.get("version")
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
    try:
        if not isinstance(text, str):
            raise ConfigError(f"{file}: 'version' is required and must be a string, such as '1.0.0'{hint}")
        try:
            version = Version(text)
        except ValueError as e:
            raise ConfigError(f"{file}: 'version': {e}{hint}") from None
    except ConfigError:
        if override is None:
            raise
        version = override
    else:
        if override is not None:
            if version == override:
                warn(f"{file} has version {version} too, so {source}'s 'version' isn't needed")
            else:
                warn(f"{file} has version {version}, but {source} gives {override}, which is used")
            version = override

    raw: object = info.get("dependencies") or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{file}: 'dependencies' must be a mapping of collection names to versions")
    dependencies: list[str] = []
    entries: dict[object, object] = raw  # pyright: ignore[reportUnknownVariableType]
    for key, value in entries.items():
        at = f"{file}: dependencies.{key}"
        if not isinstance(key, str) or not isinstance(value, str):
            raise ConfigError(f"{at}: expected a collection name and a version requirement string, such as '*'")
        try:
            dependency = collection_name(key)
            # The syntax ansible-galaxy installs dependencies with, as it does an index's.
            req = VersionReq.from_galaxy(value)
        except ValueError as e:
            raise ConfigError(f"{at}: {e}") from None
        if dependency == name:
            raise ConfigError(f"{at}: the collection can't depend on itself")
        clauses = ",".join(f"{op}{v.text}" for op, v in req.clauses)
        dependencies.append(f"{dependency}{clauses}")

    runtime_file = os.path.join(path, "meta", "runtime.yml")
    runtime_shown = os.path.join(path if shown is None else shown, "meta", "runtime.yml")
    requires_ansible = yaml_mapping(runtime_file, required=False, shown=runtime_shown).get("requires_ansible")
    if requires_ansible is not None:
        if not isinstance(requires_ansible, str):
            raise ConfigError(f"{runtime_shown}: 'requires_ansible' must be a string")
        try:
            _ = SpecifierSet(requires_ansible)
        except ValueError as e:
            raise ConfigError(f"{runtime_shown}: requires_ansible: {e}, expected PEP 440 such as '>=2.17'") from None

    return SourceCollection(
        name,
        version,
        path,
        dependencies=tuple(dependencies),
        requires_ansible=requires_ansible,
        metadata=os.path.basename(metadata_file),
    )


def yaml_mapping(path: str, *, required: bool, shown: str | None = None) -> dict[str, object]:
    """The YAML file at ``path`` as a mapping, empty if it's empty or, unless ``required``, doesn't exist; messages
    call it ``shown`` when given."""
    from alai_core.yaml import YamlError, loads

    where = path if shown is None else shown
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        if required:
            raise ConfigError(f"{where} doesn't exist") from None
        return {}
    except OSError as e:
        raise ConfigError(f"can't read {where}: {e.strerror or e}") from None
    except UnicodeDecodeError as e:
        raise ConfigError(f"{where} isn't UTF-8: {e}") from None
    try:
        data = loads(text, on_warning=lambda w: warn(f"{where}: {w}"))
    except YamlError as e:
        raise ConfigError(f"{where} isn't valid YAML: {e}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected a mapping at the top level")
    return data  # pyright: ignore[reportUnknownVariableType]
