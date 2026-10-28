# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""alai's side of extracting an artifact: its limit and messages. What a hostile or broken artifact can and can't do
is alai-core's, and tested there."""

from __future__ import annotations

from pathlib import Path

import pytest
from alai_core.semver import Version
from artifact_data import (
    Member,
    build_tarball,
    files_json,
    listed,
    listed_dir,
    manifest_json,
    tar_dir,
    tar_file,
    tar_symlink,
)

from alai import _artifacts
from alai._artifacts import ArtifactError
from alai._common import ConfigError


def extract(tmp_path: Path, members: list[Member], *, limit: int = _artifacts.MAX_UNPACKED_SIZE) -> None:
    tarball = tmp_path / "artifact.tar.gz"
    _ = build_tarball(tarball, members)
    dest = str(tmp_path / "collection")
    _artifacts.extract(str(tarball), dest, "ns.coll 1.0.0", expected=("ns.coll", Version("1.0.0")), limit=limit)


def collection(*members: Member, files: list[dict[str, object]]) -> list[Member]:
    return [tar_file("MANIFEST.json", manifest_json()), tar_file("FILES.json", files_json(*files)), *members]


def test_named_by_where_it_came_from(tmp_path: Path) -> None:
    members = collection(tar_file("a", b"a"), files=[listed("a", b"other")])

    with pytest.raises(ArtifactError, match=r"^ns\.coll 1\.0\.0: a in the artifact doesn't match its checksum"):
        extract(tmp_path, members)


def test_too_large_says_how_to_allow_it(tmp_path: Path) -> None:
    big = b"\0" * 64 * 1024
    members = collection(tar_file("big", big), files=[listed("big", big)])

    message = (
        r"^ns\.coll 1\.0\.0: can't extract the artifact: it unpacks to more than 32768 bytes; set "
        + r"ALAI_MAX_UNPACKED_SIZE to a larger number of bytes, or 0 for no limit, if it's genuine$"
    )
    with pytest.raises(ArtifactError, match=message):
        extract(tmp_path, members, limit=32 * 1024)


def test_filesystem_error(tmp_path: Path) -> None:
    # d/x makes d a directory, which the symlink can't then replace.
    members = collection(
        tar_dir("d"), tar_file("d/x", b"x"), tar_symlink("d", "."), files=[listed_dir("d"), listed("d/x", b"x")]
    )

    with pytest.raises(ArtifactError, match=r"^ns\.coll 1\.0\.0: can't extract the artifact: File exists$"):
        extract(tmp_path, members)


@pytest.mark.parametrize(("value", "expected"), [(None, 1 << 29), ("", 1 << 29), ("0", 0), ("1048576", 1 << 20)])
def test_environment(value: str | None, expected: int) -> None:
    environ = {} if value is None else {"ALAI_MAX_UNPACKED_SIZE": value}

    assert _artifacts.max_unpacked_size(environ) == expected


@pytest.mark.parametrize("value", ["-1", "1.5", "512MiB", "lots"])
def test_environment_refused(value: str) -> None:
    with pytest.raises(ConfigError, match=f"ALAI_MAX_UNPACKED_SIZE: expected a number of bytes, .* got '{value}'"):
        _ = _artifacts.max_unpacked_size({"ALAI_MAX_UNPACKED_SIZE": value})
