# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Collection artifacts for tests: tarballs laid out as ``ansible-galaxy collection build`` writes them."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path


class Link:
    """A symlink entry, pointing at ``target``."""

    def __init__(self, target: str) -> None:
        self.target = target


def build_artifact(
    path: Path,
    name: str = "ns.coll",
    version: str = "1.0.0",
    files: dict[str, bytes | Link] | None = None,
    *,
    executable: frozenset[str] = frozenset(),
    manifest: dict[str, object] | None = None,
    listed: list[dict[str, object]] | None = None,
    extra: list[tarfile.TarInfo] | None = None,
) -> tuple[str, int]:
    """Write a collection tarball to ``path`` and return its SHA-256 and size.

    ``files`` maps each path to its content, or a ``Link``; their directories are added and listed in ``FILES.json``,
    as a build does. ``listed`` replaces the ``FILES.json`` entries and ``manifest`` the ``MANIFEST.json``, for broken
    artifacts; ``extra`` adds members ``FILES.json`` doesn't list.
    """
    files = {"README.md": b"# readme\n"} if files is None else files
    namespace, short = name.split(".")
    contents = {p: c for p, c in files.items() if isinstance(c, bytes)}
    dirs = sorted({"/".join(p.split("/")[:i]) for p in files for i in range(1, p.count("/") + 1)})
    entries: list[dict[str, object]] = [{"name": ".", "ftype": "dir", "chksum_type": None, "chksum_sha256": None}]
    entries += [{"name": d, "ftype": "dir", "chksum_type": None, "chksum_sha256": None} for d in dirs]
    for p, content in sorted(files.items()):
        # A link's checksum is its target's, as a build records it; one pointing outside has none to take.
        data = content if isinstance(content, bytes) else contents.get(_resolve(p, content.target), b"")
        entries.append({"name": p, "ftype": "file", "chksum_type": "sha256", "chksum_sha256": _sha256(data)})
    if manifest is None:
        manifest = {"collection_info": {"namespace": namespace, "name": short, "version": version}}

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        _add(tar, "MANIFEST.json", json.dumps(manifest).encode())
        _add(tar, "FILES.json", json.dumps({"files": entries if listed is None else listed, "format": 1}).encode())
        for d in dirs:
            info = tarfile.TarInfo(d)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        for p, content in sorted(files.items()):
            if isinstance(content, Link):
                info = tarfile.TarInfo(p)
                info.type = tarfile.SYMTYPE
                info.linkname = content.target
                tar.addfile(info)
            else:
                _add(tar, p, content, 0o755 if p in executable else 0o644)
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    data = buffer.getvalue()
    _ = path.write_bytes(data)
    return _sha256(data), len(data)


def _add(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tar.addfile(info, io.BytesIO(data))


def _resolve(path: str, target: str) -> str:
    parts = path.split("/")[:-1]
    for part in target.split("/"):
        if part == "..":
            if not parts:
                return ""
            _ = parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
