# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A collection directory's own metadata, read as ansible-galaxy and ansible-core read it: ``galaxy.yml`` for a source,
such as a collection project or a path source, or ``MANIFEST.json`` for a built or installed collection, with
``meta/runtime.yml`` for either. The design is in ``design/collection-development.md`` and ``design/install.md``.

PyYAML is imported here only, so it only costs the commands that read one.
"""

from __future__ import annotations

import json
import os

from ._common import ConfigError
from ._manifest import SourceCollection, collection_name
from ._pep440 import SpecifierSet
from ._semver import Version, VersionReq

GALAXY_FILE_NAME = "galaxy.yml"
MANIFEST_FILE_NAME = "MANIFEST.json"


def read_collection_dir(path: str) -> SourceCollection:
    """The collection in the directory ``path``: a source, from its ``galaxy.yml``, or a built or installed
    collection, from its ``MANIFEST.json``. A directory with both is an error, as ansible-galaxy makes it: a
    ``MANIFEST.json`` left in a source is usually stale, and a ``galaxy.yml`` in a built collection isn't what was
    built, so neither can be chosen safely."""
    galaxy_file = os.path.join(path, GALAXY_FILE_NAME)
    manifest_file = os.path.join(path, MANIFEST_FILE_NAME)
    has_galaxy, has_manifest = os.path.isfile(galaxy_file), os.path.isfile(manifest_file)
    if has_galaxy and has_manifest:
        raise ConfigError(
            f"{path} has both a galaxy.yml and a MANIFEST.json, so it's unclear whether it's a collection's source or "
            + "a built collection: remove the MANIFEST.json from a source, or the galaxy.yml from a built collection"
        )
    if has_manifest:
        return read_built_collection(manifest_file)
    return read_source_collection(galaxy_file)


def read_source_collection(galaxy_file: str) -> SourceCollection:
    """Read the collection whose ``galaxy.yml`` is ``galaxy_file``, raising ConfigError for anything ansible-galaxy
    would refuse to build or install."""
    return _collection(galaxy_file, yaml_mapping(galaxy_file, required=True))


def read_built_collection(manifest_file: str) -> SourceCollection:
    """Read the built or installed collection whose ``MANIFEST.json`` is ``manifest_file``, from its
    ``collection_info``, which holds what ``galaxy.yml`` did when it was built."""
    try:
        with open(manifest_file, "rb") as f:
            data = json.load(f)
    except OSError as e:
        raise ConfigError(f"can't read {manifest_file}: {e.strerror or e}") from None
    except ValueError as e:
        raise ConfigError(f"{manifest_file} isn't valid JSON: {e}") from None
    info = data.get("collection_info") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(info, dict):
        raise ConfigError(f"{manifest_file}: expected a 'collection_info' object")
    return _collection(manifest_file, info)


def _collection(metadata_file: str, info: dict[str, object]) -> SourceCollection:
    """The collection ``info``, the mapping read from ``metadata_file``, describes, with ``requires_ansible`` from the
    ``meta/runtime.yml`` beside it, which neither file carries."""
    path = os.path.dirname(metadata_file)
    namespace, short = info.get("namespace"), info.get("name")
    if not isinstance(namespace, str) or not isinstance(short, str):
        raise ConfigError(f"{metadata_file}: 'namespace' and 'name' are required strings")
    try:
        name = collection_name(f"{namespace}.{short}")
    except ValueError as e:
        raise ConfigError(f"{metadata_file}: {e}") from None

    text = info.get("version")
    if not isinstance(text, str):
        raise ConfigError(f"{metadata_file}: 'version' is required and must be a string, such as '1.0.0'")
    try:
        version = Version(text)
    except ValueError as e:
        raise ConfigError(f"{metadata_file}: 'version': {e}") from None

    raw: object = info.get("dependencies") or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{metadata_file}: 'dependencies' must be a mapping of collection names to versions")
    dependencies: list[str] = []
    entries: dict[object, object] = raw  # pyright: ignore[reportUnknownVariableType]
    for key, value in entries.items():
        where = f"{metadata_file}: dependencies.{key}"
        if not isinstance(key, str) or not isinstance(value, str):
            raise ConfigError(f"{where}: expected a collection name and a version requirement string, such as '*'")
        try:
            dependency = collection_name(key)
            # The syntax ansible-galaxy installs dependencies with, as it does an index's.
            req = VersionReq.parse_index(value)
        except ValueError as e:
            raise ConfigError(f"{where}: {e}") from None
        if dependency == name:
            raise ConfigError(f"{where}: the collection can't depend on itself")
        clauses = ",".join(f"{op}{v.text}" for op, v in req.clauses)
        dependencies.append(f"{dependency}{clauses}")

    runtime_file = os.path.join(path, "meta", "runtime.yml")
    requires_ansible = yaml_mapping(runtime_file, required=False).get("requires_ansible")
    if requires_ansible is not None:
        if not isinstance(requires_ansible, str):
            raise ConfigError(f"{runtime_file}: 'requires_ansible' must be a string")
        try:
            _ = SpecifierSet(requires_ansible)
        except ValueError as e:
            raise ConfigError(f"{runtime_file}: requires_ansible: {e}, expected PEP 440 such as '>=2.17'") from None

    return SourceCollection(
        name,
        version,
        path,
        dependencies=tuple(dependencies),
        requires_ansible=requires_ansible,
        metadata=os.path.basename(metadata_file),
    )


def yaml_mapping(path: str, *, required: bool) -> dict[str, object]:
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=loader)  # noqa: S506 # a safe loader, the C one when PyYAML has it
    except FileNotFoundError:
        if required:
            raise ConfigError(f"{path} doesn't exist") from None
        return {}
    except OSError as e:
        raise ConfigError(f"can't read {path}: {e.strerror or e}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} isn't valid YAML: {e}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data  # pyright: ignore[reportUnknownVariableType]
