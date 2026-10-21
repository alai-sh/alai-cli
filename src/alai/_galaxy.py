# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A collection source checkout's own metadata: ``galaxy.yml`` and ``meta/runtime.yml``, read as ansible-galaxy and
ansible-core read them, for a collection project. The design is in ``design/collection-development.md``.

PyYAML is imported here only, so it only costs the commands run in such a project.
"""

from __future__ import annotations

import os

from ._common import ConfigError
from ._manifest import RootCollection, collection_name
from ._pep440 import SpecifierSet
from ._semver import Version, VersionReq

GALAXY_FILE_NAME = "galaxy.yml"


def read_root_collection(galaxy_file: str) -> RootCollection:
    """Read the collection whose ``galaxy.yml`` is ``galaxy_file``, raising ConfigError for anything ansible-galaxy
    would refuse to build or install."""
    path = os.path.dirname(galaxy_file)
    info = _yaml_mapping(galaxy_file, required=True)
    namespace, short = info.get("namespace"), info.get("name")
    if not isinstance(namespace, str) or not isinstance(short, str):
        raise ConfigError(f"{galaxy_file}: 'namespace' and 'name' are required strings")
    try:
        name = collection_name(f"{namespace}.{short}")
    except ValueError as e:
        raise ConfigError(f"{galaxy_file}: {e}") from None

    text = info.get("version")
    if not isinstance(text, str):
        raise ConfigError(f"{galaxy_file}: 'version' is required and must be a string, such as '1.0.0'")
    try:
        version = Version(text)
    except ValueError as e:
        raise ConfigError(f"{galaxy_file}: 'version': {e}") from None

    raw: object = info.get("dependencies") or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{galaxy_file}: 'dependencies' must be a mapping of collection names to versions")
    dependencies: list[str] = []
    entries: dict[object, object] = raw  # pyright: ignore[reportUnknownVariableType]
    for key, value in entries.items():
        where = f"{galaxy_file}: dependencies.{key}"
        if not isinstance(key, str) or not isinstance(value, str):
            raise ConfigError(f"{where}: expected a collection name and a version requirement string, such as '*'")
        try:
            dependency = collection_name(key)
            # The syntax ansible-galaxy installs galaxy.yml's dependencies with, as it does an index's.
            req = VersionReq.parse_index(value)
        except ValueError as e:
            raise ConfigError(f"{where}: {e}") from None
        if dependency == name:
            raise ConfigError(f"{where}: the collection can't depend on itself")
        clauses = ",".join(f"{op}{v.text}" for op, v in req.clauses)
        dependencies.append(f"{dependency}{clauses}")

    runtime_file = os.path.join(path, "meta", "runtime.yml")
    requires_ansible = _yaml_mapping(runtime_file, required=False).get("requires_ansible")
    if requires_ansible is not None:
        if not isinstance(requires_ansible, str):
            raise ConfigError(f"{runtime_file}: 'requires_ansible' must be a string")
        try:
            _ = SpecifierSet(requires_ansible)
        except ValueError as e:
            raise ConfigError(f"{runtime_file}: requires_ansible: {e}, expected PEP 440 such as '>=2.17'") from None

    return RootCollection(name, version, path, dependencies=tuple(dependencies), requires_ansible=requires_ansible)


def _yaml_mapping(path: str, *, required: bool) -> dict[str, object]:
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
