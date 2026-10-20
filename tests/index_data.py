# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Building small indexes on disk, in the index spec's layout, for the resolver and command tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


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
    root: Path, collections: dict[str, list[dict[str, object]]], *, raw_lines: dict[str, str] | None = None
) -> str:
    """Write an index in the spec's layout, returning its file:// root URL."""
    (root / "index").mkdir(parents=True, exist_ok=True)
    _ = (root / "config.json").write_text(
        json.dumps(
            {
                "formats": {"1": {"index": "index/", "collections": "collections.json"}},
                "dl": "https://dl.example/{sha256-checksum}.tar.gz",
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
            line.update({k: val for k, val in spec.items() if k not in ("vers", "deps", "yanked")})
            lines.append(json.dumps(line))
        if raw_lines and name in raw_lines:
            lines.append(raw_lines[name])
        path = root / "index" / namespace / f"{short}.jsonl"
        path.parent.mkdir(exist_ok=True)
        data = ("\n".join(lines) + "\n").encode()
        _ = path.write_bytes(data)
        listing.setdefault(namespace, {})[short] = {
            "updated": "2026-01-01T00:00:00Z",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    _ = (root / "collections.json").write_text(json.dumps({"v": 1, "namespaces": listing}))
    return root.as_uri() + "/"
