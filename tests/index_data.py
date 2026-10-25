# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Building small indexes on disk, in the index spec's layout, for the resolver and command tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from artifact_data import build_artifact


def v(
    vers: str,
    deps: dict[str, str] | None = None,
    *,
    yanked: bool = False,
    ra: str | None = None,
    **extra: object,
) -> dict[str, object]:
    """One index line's worth: a version, its dependencies, and anything else to set or override."""
    line: dict[str, object] = {"vers": vers, "deps": deps or {}, "yanked": yanked}
    if ra is not None:
        line["requires_ansible"] = ra
    line.update(extra)
    return line


def cksum(name: str, vers: str) -> str:
    return hashlib.sha256(f"{name} {vers}".encode()).hexdigest()


def make_index(
    root: Path,
    collections: dict[str, list[dict[str, object]]],
    *,
    raw_lines: dict[str, str] | None = None,
    artifacts: bool = False,
    plugins: list[dict[str, object]] | None = None,
    hashed: bool = True,
) -> str:
    """Write an index in the spec's layout, returning its file:// root URL.

    With ``artifacts``, each version gets a real tarball under ``root/dl``, which ``dl`` points at, and its line the
    tarball's ``cksum`` and ``size``, so it can be installed: one holding only a README, or the file a version's
    ``tarball`` key names, such as a collection downloaded from Galaxy. With ``plugins``, it has a plugin index of those lines
    (see ``plugin``). Index files are found by their SHA-256, ``index/<aa>/<sha256>.jsonl``, as ``index.alai.sh``
    serves them, or with ``hashed=False`` by name, ``index/<namespace>/<name>.jsonl``.
    """
    (root / "index").mkdir(parents=True, exist_ok=True)
    dl = (
        (root / "dl").as_uri() + "/{sha256-checksum}.tar.gz"
        if artifacts
        else "https://dl.example/{sha256-checksum}.tar.gz"
    )
    _ = (root / "config.json").write_text(
        json.dumps(
            {
                "formats": {
                    "1": {
                        "index": "index/{sha256-prefix}/{sha256-checksum}.jsonl"
                        if hashed
                        else "index/{namespace}/{name}.jsonl",
                        "collections": "collections.json",
                        **({"plugins": "plugins.jsonl"} if plugins is not None else {}),
                    }
                },
                "dl": dl,
                "moved-to": None,
            }
        )
    )
    listing: dict[str, dict[str, dict[str, str]]] = {}
    for name, versions in collections.items():
        namespace, short = name.split(".")
        lines: list[str] = []
        for spec in versions:
            line: dict[str, object] = {
                "v": 1,
                "name": name,
                "vers": spec["vers"],
                "deps": [{"name": d, "req": r} for d, r in spec["deps"].items()],  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
                "cksum": cksum(name, str(spec["vers"])),
                "size": 100,
                "yanked": spec["yanked"],
                "origin": "native",
                "published": "2026-01-01T00:00:00Z",
            }
            tarball = spec.get("tarball")
            if isinstance(tarball, Path):
                line["cksum"], line["size"] = _copy_artifact(root / "dl", tarball)
            elif artifacts:
                line["cksum"], line["size"] = _artifact(root / "dl", name, str(spec["vers"]))
            line.update({k: val for k, val in spec.items() if k not in ("vers", "deps", "yanked", "tarball")})
            lines.append(json.dumps(line))
        if raw_lines and name in raw_lines:
            lines.append(raw_lines[name])
        data = ("\n".join(lines) + "\n").encode()
        sha256 = hashlib.sha256(data).hexdigest()
        path = (
            root / "index" / sha256[:2] / f"{sha256}.jsonl" if hashed else root / "index" / namespace / f"{short}.jsonl"
        )
        path.parent.mkdir(exist_ok=True)
        _ = path.write_bytes(data)
        listing.setdefault(namespace, {})[short] = {"updated": "2026-01-01T00:00:00Z", "sha256": sha256}
    _ = (root / "collections.json").write_text(json.dumps({"v": 1, "namespaces": listing}))
    if plugins is not None:
        _ = (root / "plugins.jsonl").write_text("".join(json.dumps(line) + "\n" for line in plugins))
    return root.as_uri() + "/"


def index_path(root: Path, name: str) -> str:
    """Where collection ``name``'s index file is in the index at ``root``, as a URL path: by its SHA-256 in
    ``collections.json``, or by name, as the index's template says."""
    namespace, short = name.split(".")
    template = json.loads((root / "config.json").read_text())["formats"]["1"]["index"]
    sha256 = json.loads((root / "collections.json").read_text())["namespaces"][namespace][short]["sha256"]
    return "/" + (
        template.replace("{namespace}", namespace)
        .replace("{name}", short)
        .replace("{sha256-checksum}", sha256)
        .replace("{sha256-prefix}", sha256[:2])
    )


def plugin(fqcn: str, type_: str = "module", **fields: object) -> dict[str, object]:
    """One plugin index line: ``plugin("a.a.thing", since="1.0.0", routing={...})``."""
    collection, name = fqcn.rsplit(".", 1)
    return {"v": 1, "type": type_, "name": name, "collection": collection, **fields}


def _copy_artifact(directory: Path, tarball: Path) -> tuple[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    data = tarball.read_bytes()
    cksum = hashlib.sha256(data).hexdigest()
    _ = (directory / f"{cksum}.tar.gz").write_bytes(data)
    return cksum, len(data)


def _artifact(directory: Path, name: str, version: str) -> tuple[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / "building.tar.gz"
    cksum, size = build_artifact(temp, name, version, {"README.md": f"{name} {version}\n".encode()})
    _ = temp.rename(directory / f"{cksum}.tar.gz")
    return cksum, size
