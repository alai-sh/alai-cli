# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A Python simple index on disk, as PyPI serves one as JSON (PEP 691), with small ansible-core and ansible-rulebook
sdists made from plugin index lines, for the find tests and the docs recordings. Served by ``IndexServer``, which
answers a project's ``<name>/`` with the ``index.json`` there."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

CORE_VERSION = "2.20.0"
EDA_VERSION = "1.2.0"


def make_python_index(root: Path, lines: list[dict[str, object]]) -> None:
    """ansible-core ``CORE_VERSION`` holding the ``ansible.builtin`` lines in ``lines``, and ansible-rulebook
    ``EDA_VERSION`` the ``eda.builtin`` ones, each the only release of its project."""
    sdist(root, "ansible-core", CORE_VERSION, ansible_core_files(lines))
    page(root, "ansible-core", [CORE_VERSION])
    sdist(root, "ansible-rulebook", EDA_VERSION, ansible_rulebook_files(lines))
    page(root, "ansible-rulebook", [EDA_VERSION])


def sdist(root: Path, project: str, version: str, files: dict[str, str]) -> Path:
    """Write ``root/files/<project>-<version>.tar.gz``, an sdist with ``files`` below its top-level directory, as
    PyPI names one (``ansible_core-2.20.0.tar.gz``)."""
    top = f"{project.replace('-', '_')}-{version}"
    (root / "files").mkdir(parents=True, exist_ok=True)
    path = root / "files" / f"{top}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def page(root: Path, project: str, versions: list[str], *, yanked: tuple[str, ...] = (), sha256: bool = True) -> None:
    """The project's page, ``root/<project>/index.json``, listing the sdist of each of ``versions`` (written with
    ``sdist`` first), those in ``yanked`` as yanked, with each file's SHA-256 unless not ``sha256``."""
    files: list[dict[str, object]] = []
    for version in versions:
        name = f"{project.replace('-', '_')}-{version}.tar.gz"
        digest = hashlib.sha256((root / "files" / name).read_bytes()).hexdigest()
        files.append(
            {
                "filename": name,
                "url": f"../files/{name}",
                "hashes": {"sha256": digest} if sha256 else {},
                "yanked": version in yanked,
            }
        )
    (root / project).mkdir(parents=True, exist_ok=True)
    _ = (root / project / "index.json").write_text(
        json.dumps({"meta": {"api-version": "1.1"}, "name": project, "files": files})
    )


def ansible_core_files(lines: list[dict[str, object]]) -> dict[str, str]:
    """An ansible-core source tree's files, as far as extraction reads them, from the ``ansible.builtin`` lines in
    ``lines``: a plugin file with ``DOCUMENTATION`` for each shipped plugin, and every line's routing in
    ``ansible_builtin_runtime.yml``."""
    files = {"lib/ansible/release.py": f'__version__ = "{CORE_VERSION}"\n'}
    routing: dict[str, dict[str, object]] = {}
    for line in _lines(lines, "ansible.builtin"):
        type_, name = str(line["type"]), str(line["name"])
        if line.get("short_description") is not None or "routing" not in line:
            directory = "lib/ansible/modules" if type_ == "module" else f"lib/ansible/plugins/{type_}"
            files[f"{directory}/{name}.py"] = _plugin_source(type_, name, line)
        if (entry := line.get("routing")) is not None:
            routing.setdefault("modules" if type_ == "module" else type_, {})[name] = entry
    files["lib/ansible/config/ansible_builtin_runtime.yml"] = json.dumps({"plugin_routing": routing})
    return files


def ansible_rulebook_files(lines: list[dict[str, object]]) -> dict[str, str]:
    """An ansible-rulebook source tree's files, from the ``eda.builtin`` lines in ``lines``."""
    files = {
        "PKG-INFO": f"Metadata-Version: 2.1\nName: ansible-rulebook\nVersion: {EDA_VERSION}\n",
        "ansible_rulebook/__init__.py": "",
    }
    for line in _lines(lines, "eda.builtin"):
        type_, name = str(line["type"]), str(line["name"])
        files[f"ansible_rulebook/{type_}/{name}.py"] = _plugin_source(type_, name, line)
    return files


def _lines(lines: list[dict[str, object]], collection: str) -> list[dict[str, object]]:
    return [line for line in lines if line.get("collection") == collection]


def _plugin_source(type_: str, name: str, line: dict[str, object]) -> str:
    """A plugin file: its ``DOCUMENTATION`` (JSON, which is YAML) with the line's ``short_description`` and its
    ``since`` as ``version_added``, and for a filter or test, the class that names it."""
    docs: dict[str, object] = {"name": name}
    if (short := line.get("short_description")) is not None:
        docs["short_description"] = short
    if (since := line.get("since")) is not None:
        docs["version_added"] = since
    source = f"DOCUMENTATION = {json.dumps(json.dumps(docs))}\n"
    if type_ in ("filter", "test"):
        source += f"class {type_.title()}Module:\n    def {type_}s(self):\n        return {{{name!r}: None}}\n"
    return source
