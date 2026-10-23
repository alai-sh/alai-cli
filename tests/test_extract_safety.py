# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What a hostile or broken artifact can and can't do when it's extracted: only what FILES.json lists, the last member
of each name, nothing written, read or linked outside the collection."""

from __future__ import annotations

import collections.abc as _c
import gzip
import hashlib
import os
import re
import shutil
import stat
import tarfile
from pathlib import Path

import pytest
from artifact_data import (
    Member,
    build_tarball,
    files_json,
    listed,
    listed_dir,
    manifest_json,
    tar_dir,
    tar_file,
    tar_hardlink,
    tar_special,
    tar_symlink,
)

from alai import _artifacts
from alai._artifacts import ArtifactError
from alai._common import ConfigError
from alai._semver import Version

SECRET = b"outside the collection\n"


@pytest.fixture
def sandbox(tmp_path: Path) -> _c.Iterator[Path]:
    """``tmp_path`` with a file beside the collection's directory and one above it, for an artifact to try to reach.
    The collection is extracted to ``work/collection``, under a ``022`` umask, which the modes asserted assume."""
    (tmp_path / "work").mkdir()
    _ = (tmp_path / "work" / "secret.txt").write_bytes(SECRET)
    _ = (tmp_path / "victim.txt").write_bytes(SECRET)
    old = os.umask(0o022)
    try:
        yield tmp_path
    finally:
        _ = os.umask(old)


def everything(root: Path) -> dict[str, str]:
    """Each path under ``root``: a symlink's target, a directory's mode, or a file's mode and content."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[relative] = f"-> {path.readlink()}"
        elif path.is_dir():
            out[relative] = f"dir {stat.S_IMODE(path.lstat().st_mode):o}"
        else:
            out[relative] = f"file {stat.S_IMODE(path.lstat().st_mode):o} {path.read_bytes()!r}"
    return out


def outside(sandbox: Path) -> dict[str, str]:
    """Everything in the sandbox but the collection itself and the tarball."""
    return {
        p: kind
        for p, kind in everything(sandbox).items()
        if not p.startswith("work/collection") and not p.startswith("artifact") and p != "via"
    }


def extract(
    sandbox: Path, members: list[Member], limit: int = _artifacts.MAX_UNPACKED_SIZE, via: str = "work"
) -> dict[str, str]:
    """Extract ``members`` as ns.coll 1.0.0 and return the collection's tree, checking nothing outside it changed.
    ``via`` is the directory to extract into, ``work`` or a symlink to it."""
    shutil.rmtree(sandbox / "work" / "collection", ignore_errors=True)
    before = outside(sandbox)
    tarball = sandbox / "artifact.tar.gz"
    _ = build_tarball(tarball, members)
    try:
        dest = str(sandbox / via / "collection")
        _artifacts.extract(str(tarball), dest, "ns.coll 1.0.0", expected=("ns.coll", Version("1.0.0")), limit=limit)
    finally:
        assert outside(sandbox) == before
    return everything(sandbox / "work" / "collection")


def refused(sandbox: Path, members: list[Member], message: str) -> None:
    with pytest.raises(ArtifactError, match=message):
        _ = extract(sandbox, members)


def collection(*members: Member, files: list[dict[str, object]]) -> list[Member]:
    """``members`` after MANIFEST.json and a FILES.json listing ``files``, as a build stores them."""
    return [tar_file("MANIFEST.json", manifest_json()), tar_file("FILES.json", files_json(*files)), *members]


def special_files(files: bytes) -> dict[str, str]:
    """The tree entries for MANIFEST.json and a FILES.json of ``files``."""
    return {"FILES.json": f"file 644 {files!r}", "MANIFEST.json": f"file 644 {manifest_json()!r}"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestAppended:
    """Members appended with ``tar -r``: the last of each name is the only one extracted, and FILES.json's
    replacement decides what is."""

    def test_dropped_from_the_new_listing(self, sandbox: Path) -> None:
        new = files_json(listed("a", b"a"))
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files_json(listed("a", b"a"), listed("b", b"b"))),
            tar_file("a", b"a"),
            tar_file("b", b"b"),
            tar_file("FILES.json", new),
        ]

        assert extract(sandbox, members) == {**special_files(new), "a": "file 644 b'a'"}

    def test_added_by_the_new_listing(self, sandbox: Path) -> None:
        # b is stored before the listing that names it.
        new = files_json(listed("a", b"a"), listed("b", b"b"))
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files_json(listed("a", b"a"))),
            tar_file("a", b"a"),
            tar_file("b", b"b"),
            tar_file("FILES.json", new),
        ]

        assert extract(sandbox, members) == {**special_files(new), "a": "file 644 b'a'", "b": "file 644 b'b'"}

    def test_new_content_and_new_listing(self, sandbox: Path) -> None:
        new = files_json(listed("a", b"two"))
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files_json(listed("a", b"one"))),
            tar_file("a", b"one"),
            tar_file("a", b"two"),
            tar_file("FILES.json", new),
        ]

        assert extract(sandbox, members) == {**special_files(new), "a": "file 644 b'two'"}

    def test_one_listing_of_the_second_copy(self, sandbox: Path) -> None:
        members = collection(tar_file("a", b"one"), tar_file("a", b"two"), files=[listed("a", b"two")])

        assert extract(sandbox, members)["a"] == "file 644 b'two'"

    def test_one_listing_of_the_first_copy(self, sandbox: Path) -> None:
        members = collection(tar_file("a", b"one"), tar_file("a", b"two"), files=[listed("a", b"one")])

        refused(sandbox, members, "a in the artifact doesn't match its checksum")

    def test_new_listing_of_the_first_copy(self, sandbox: Path) -> None:
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files_json(listed("a", b"one"))),
            tar_file("a", b"one"),
            tar_file("a", b"two"),
            tar_file("FILES.json", files_json(listed("a", b"one"))),
        ]

        refused(sandbox, members, "a in the artifact doesn't match its checksum")

    def test_file_replaced_by_a_symlink(self, sandbox: Path) -> None:
        members = collection(
            tar_file("a", b"t"),
            tar_file("t", b"t"),
            tar_symlink("a", "t"),
            files=[listed("a", b"t"), listed("t", b"t")],
        )

        tree = extract(sandbox, members)

        assert (tree["a"], tree["t"]) == ("-> t", "file 644 b't'")

    def test_symlink_replaced_by_a_file(self, sandbox: Path) -> None:
        members = collection(
            tar_file("t", b"t"),
            tar_symlink("a", "t"),
            tar_file("a", b"a"),
            files=[listed("a", b"a"), listed("t", b"t")],
        )

        assert extract(sandbox, members)["a"] == "file 644 b'a'"

    def test_escaping_symlink_replaced_by_a_file(self, sandbox: Path) -> None:
        members = collection(tar_symlink("a", "../../victim.txt"), tar_file("a", b"a"), files=[listed("a", b"a")])

        assert extract(sandbox, members)["a"] == "file 644 b'a'"

    def test_file_replaced_by_an_escaping_symlink(self, sandbox: Path) -> None:
        members = collection(tar_file("a", b"a"), tar_symlink("a", "../secret.txt"), files=[listed("a", SECRET)])

        refused(sandbox, members, r"a in the artifact links outside the collection, to \.\./secret\.txt")

    def test_files_written_through_a_replaced_symlink(self, sandbox: Path) -> None:
        # d/x is written through d -> . to ./x, unless only the last d, a directory, is extracted.
        members = collection(
            tar_symlink("d", "."), tar_file("d/x", b"x"), tar_dir("d"), files=[listed_dir("d"), listed("d/x", b"x")]
        )

        tree = extract(sandbox, members)

        assert (tree["d"], tree["d/x"]) == ("dir 755", "file 644 b'x'")
        assert "x" not in tree

    def test_directory_replaced_by_a_symlink(self, sandbox: Path) -> None:
        # d/x is stored first, so it makes d a directory, which the symlink can't then replace.
        members = collection(
            tar_dir("d"), tar_file("d/x", b"x"), tar_symlink("d", "."), files=[listed_dir("d"), listed("d/x", b"x")]
        )

        refused(sandbox, members, "can't extract the artifact")

    def test_escaping_listing_replaced(self, sandbox: Path) -> None:
        new = files_json(listed("a", b"a"))
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files_json(listed("../escape", b"a"))),
            tar_file("a", b"a"),
            tar_file("FILES.json", new),
        ]

        assert extract(sandbox, members) == {**special_files(new), "a": "file 644 b'a'"}

    def test_listing_replaced_by_an_escaping_one(self, sandbox: Path) -> None:
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files_json(listed("a", b"a"))),
            tar_file("a", b"a"),
            tar_file("FILES.json", files_json(listed("../victim.txt", SECRET))),
        ]

        refused(sandbox, members, r"a path outside the collection: \.\./victim\.txt")

    def test_manifest_replaced_by_another_collection(self, sandbox: Path) -> None:
        members = collection(tar_file("MANIFEST.json", manifest_json("ns.other")), files=[])

        refused(sandbox, members, r"MANIFEST\.json is for ns\.other 1\.0\.0")

    @pytest.mark.parametrize("member", [tar_dir("MANIFEST.json"), tar_symlink("MANIFEST.json", "FILES.json")])
    def test_manifest_replaced_by_a_non_file(self, sandbox: Path, member: Member) -> None:
        refused(sandbox, collection(member, files=[]), r"MANIFEST\.json in the artifact isn't a file")

    def test_files_json_replaced_by_a_symlink(self, sandbox: Path) -> None:
        members = collection(
            tar_file("other.json", files_json()), tar_symlink("FILES.json", "other.json"), files=[listed_dir("x")]
        )

        refused(sandbox, members, r"FILES\.json in the artifact isn't a file")

    def test_hardlink_replaced_by_a_file(self, sandbox: Path) -> None:
        members = collection(
            tar_file("t", b"t"),
            tar_hardlink("a", "t"),
            tar_file("a", b"a"),
            files=[listed("a", b"a"), listed("t", b"t")],
        )

        assert extract(sandbox, members)["a"] == "file 644 b'a'"


class TestListing:
    """Only what FILES.json lists is extracted, and only where it says."""

    def test_unlisted_members_left_out(self, sandbox: Path) -> None:
        # plugins is a symlink in the tarball, but FILES.json doesn't list it, so plugins/x's directory is made plainly.
        files = files_json(listed("plugins/x", b"x"))
        members = [
            tar_file("MANIFEST.json", manifest_json()),
            tar_file("FILES.json", files),
            tar_file("sneaky.txt", b"sneaky"),
            tar_symlink("plugins", "../.."),
            tar_file("plugins/x", b"x"),
            tar_file(".hidden", b"hidden"),
        ]

        assert extract(sandbox, members) == {**special_files(files), "plugins": "dir 755", "plugins/x": "file 644 b'x'"}

    def test_listed_twice_the_same(self, sandbox: Path) -> None:
        members = collection(tar_file("a", b"a"), files=[listed("a", b"a"), listed("a", b"a")])

        assert extract(sandbox, members)["a"] == "file 644 b'a'"

    def test_listed_twice_differently(self, sandbox: Path) -> None:
        members = collection(tar_file("a", b"a"), files=[listed("a", b"a"), listed("a", b"b")])

        refused(sandbox, members, "FILES.json lists a more than once, differently")

    def test_file_without_a_checksum(self, sandbox: Path) -> None:
        entry = {"name": "a", "ftype": "file", "chksum_type": None, "chksum_sha256": None}

        refused(sandbox, collection(tar_file("a", b"a"), files=[entry]), r"FILES\.json has no checksum for a")

    def test_unreadable_entry(self, sandbox: Path) -> None:
        members = collection(tar_file("a", b"a"), files=[{"name": "a", "ftype": "fifo"}])

        refused(sandbox, members, r"FILES\.json has an unreadable entry")

    def test_long_name(self, sandbox: Path) -> None:
        # Longer than ustar's 100 bytes, so tarfile stores it in a pax header.
        name = "plugins/" + "m" * 150 + ".py"
        members = collection(
            tar_dir("plugins"), tar_file(name, b"x"), files=[listed_dir("plugins"), listed(name, b"x")]
        )

        assert extract(sandbox, members)[name] == "file 644 b'x'"

    @pytest.mark.parametrize("name", ["MANIFEST.json", "FILES.json"])
    def test_lists_itself_or_the_manifest(self, sandbox: Path, name: str) -> None:
        # Neither can be listed with a checksum that matches, as each depends on the other's content.
        members = collection(files=[listed(name, manifest_json())])

        refused(sandbox, members, rf"FILES\.json lists {re.escape(name)}, which a build never does")

    @pytest.mark.parametrize("name", ["../x", "/abs", "a/../b", "./a", "a//b", "a/", "a\\b", "", "..", "a/.", "a/.."])
    @pytest.mark.parametrize("ftype", ["file", "dir"])
    def test_path_outside_refused(self, sandbox: Path, name: str, ftype: str) -> None:
        # Refused from FILES.json alone, before any member is looked for.
        entry = listed(name, b"x") if ftype == "file" else listed_dir(name)
        members = collection(files=[entry])

        refused(sandbox, members, "a path outside the collection")

    def test_name_not_utf8_refused(self, sandbox: Path) -> None:
        # A lone surrogate is what tarfile decodes a byte that isn't UTF-8 to, and JSON can spell one, but it isn't a
        # string serde_json reads, so FILES.json isn't valid JSON to it.
        members = collection(files=[listed("bad\udc80", b"x")])

        refused(sandbox, members, r"FILES\.json in the artifact isn't valid JSON: lone leading surrogate")

    @pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
    def test_special_file_refused(self, sandbox: Path, kind: bytes) -> None:
        refused(
            sandbox,
            collection(tar_special("dev", kind), files=[listed("dev", b"")]),
            "dev in the artifact isn't a file",
        )
        refused(
            sandbox, collection(tar_special("dev", kind), files=[listed_dir("dev")]), "dev in the artifact isn't a dir"
        )

    def test_file_where_a_directory_is_listed(self, sandbox: Path) -> None:
        refused(
            sandbox, collection(tar_file("d", b"d"), files=[listed_dir("d")]), "d in the artifact isn't a directory"
        )

    def test_hardlink_where_a_directory_is_listed(self, sandbox: Path) -> None:
        members = collection(tar_dir("t"), tar_hardlink("d", "t"), files=[listed_dir("t"), listed_dir("d")])

        refused(sandbox, members, "d in the artifact isn't a directory")


class TestManifest:
    """MANIFEST.json must name the collection and version the lock has."""

    @pytest.mark.parametrize(
        ("manifest", "found"),
        [
            (manifest_json("ns.other"), "ns.other 1.0.0"),
            (manifest_json(version="2.0.0"), "ns.coll 2.0.0"),
            (b"{}", "null.null null"),
        ],
    )
    def test_another_collection(self, sandbox: Path, manifest: bytes, found: str) -> None:
        members = [tar_file("MANIFEST.json", manifest), tar_file("FILES.json", files_json())]

        refused(sandbox, members, rf"MANIFEST\.json is for {re.escape(found)}")


class TestAmbiguousMembers:
    """A member tar readers don't agree on makes the tarball corrupt, listed or not, so alai sees the members tarfile
    and GNU tar would."""

    @pytest.mark.parametrize(
        "kind", [tarfile.AREGTYPE, tarfile.REGTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE]
    )
    def test_name_ending_in_a_slash(self, sandbox: Path, kind: bytes) -> None:
        # tarfile reads an old-style regular file (AREGTYPE) named d/ as the directory d, the others as d/, and drops
        # the / from a pax name of any type.
        info, _ = tar_file("d/", b"")
        info.type = kind
        info.linkname = "a" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
        members = collection(tar_file("a", b"a"), (info, b""), files=[listed("a", b"a")])

        refused(sandbox, members, "can't extract the artifact: d/ ends in / but isn't a directory")

    @pytest.mark.parametrize(
        ("member", "what"),
        [
            (tar_dir("d"), "directory"),
            (tar_symlink("d", "a"), "symlink"),
            (tar_hardlink("d", "a"), "hard link"),
            (tar_special("d", tarfile.FIFOTYPE), "device"),
        ],
    )
    def test_content_where_there_can_be_none(self, sandbox: Path, member: Member, what: str) -> None:
        # tarfile and GNU tar read what follows the header as the next member, and the tar crate skips it, so the
        # x stored as d's content would be a member to one and hidden from the other.
        hidden, data = tar_file("x", b"x")
        content = hidden.tobuf() + (data or b"") + b"\0" * 511
        info, _ = member
        info.size = len(content)
        members = collection(tar_file("a", b"a"), (info, content), files=[listed("a", b"a")])

        refused(sandbox, members, rf"can't extract the artifact: the {what} d has 1024 bytes of content")

    @pytest.mark.parametrize(
        ("records", "key"),
        [([("path", "x")], "path"), ([("comment", "c"), ("linkpath", "x")], "linkpath"), ([("mtime", "0")], "mtime")],
    )
    def test_pax_global_header(self, sandbox: Path, records: list[tuple[str, str]], key: str) -> None:
        # tarfile applies a global header's keys, path and linkpath included, to every member after it; the tar crate
        # leaves them, so the two would name, and link, the members differently.
        members = [global_header(records), *collection(tar_file("a", b"a"), files=[listed("a", b"a")])]

        refused(sandbox, members, f"can't extract the artifact: a pax global header sets {key}")

    @pytest.mark.parametrize("records", [[("comment", "0" * 40)], []])
    def test_pax_global_header_of_a_comment(self, sandbox: Path, records: list[tuple[str, str]]) -> None:
        # As git archive writes one, holding the commit: it describes the archive, so changes no member.
        files = files_json(listed("a", b"a"))
        members = [global_header(records), tar_file("MANIFEST.json", manifest_json()), tar_file("FILES.json", files)]

        assert extract(sandbox, [*members, tar_file("a", b"a")]) == {**special_files(files), "a": "file 644 b'a'"}

    def test_pax_global_header_isnt_a_member(self, sandbox: Path) -> None:
        # tarfile doesn't give one as a member, so FILES.json listing it lists something missing.
        members = [global_header([("comment", "c")]), *collection(files=[listed("pax_global_header", b"")])]

        refused(sandbox, members, "doesn't contain pax_global_header")


def global_header(records: list[tuple[str, str]]) -> Member:
    """A pax global header of ``records``, as ``git archive`` begins a tarball with one."""
    data = b""
    for key, value in records:
        # Each record starts with its own length, those digits included.
        record = f" {key}={value}\n".encode()
        length = len(record) + len(str(len(record)))
        length = len(record) + len(str(length))
        data += str(length).encode() + record
    info = tarfile.TarInfo("pax_global_header")
    info.type = tarfile.XGLTYPE
    info.size = len(data)
    return info, data


class TestModes:
    """Modes come from what a file is, not from the tarball: no setuid, setgid, sticky or world-writable bits."""

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [(0o6755, "755"), (0o4644, "644"), (0o777, "755"), (0o666, "644"), (0o100, "755"), (0, "644")],
    )
    def test_file(self, sandbox: Path, mode: int, expected: str) -> None:
        tree = extract(sandbox, collection(tar_file("a", b"a", mode), files=[listed("a", b"a")]))

        assert tree["a"] == f"file {expected} b'a'"

    @pytest.mark.parametrize("mode", [0o7777, 0o777, 0o700, 0])
    def test_directory(self, sandbox: Path, mode: int) -> None:
        tree = extract(sandbox, collection(tar_dir("d", mode), files=[listed_dir("d")]))

        assert tree["d"] == "dir 755"


class TestSymlinks:
    """A symlink may only lead inside the collection: by name, and on disk however links chain."""

    @pytest.mark.parametrize(
        ("name", "target"),
        [
            ("l", ".."),
            ("l", "../secret.txt"),
            ("l", "../../victim.txt"),
            ("l", "/etc/passwd"),
            ("l", "/"),
            ("l", "x/../../secret.txt"),
            ("l", "./../secret.txt"),
            ("d/l", "../../secret.txt"),
            ("d/l", "e/../../../secret.txt"),
            ("d/e/l", "../../.."),
            # Back into a directory of the collection's own name, after leaving it.
            ("l", "../collection/x"),
        ],
    )
    @pytest.mark.parametrize("ftype", ["file", "dir"])
    def test_leading_out_by_name(self, sandbox: Path, name: str, target: str, ftype: str) -> None:
        parents = ["/".join(name.split("/")[:i]) for i in range(1, name.count("/") + 1)]
        entry = listed(name, SECRET) if ftype == "file" else listed_dir(name)
        members = collection(
            *(tar_dir(p) for p in parents), tar_symlink(name, target), files=[*map(listed_dir, parents), entry]
        )

        refused(sandbox, members, "links outside the collection")

    def test_inside_by_name(self, sandbox: Path) -> None:
        members = collection(
            tar_file("x", b"x"),
            tar_dir("d"),
            tar_dir("d/e"),
            tar_symlink("d/e/l", "../../x"),
            files=[listed("x", b"x"), listed_dir("d"), listed_dir("d/e"), listed("d/e/l", b"x")],
        )

        assert extract(sandbox, members)["d/e/l"] == "-> ../../x"

    def test_written_through_a_link_inside(self, sandbox: Path) -> None:
        members = collection(
            tar_dir("sub"),
            tar_symlink("l", "sub"),
            tar_file("l/x", b"x"),
            files=[listed_dir("sub"), listed_dir("l"), listed("l/x", b"x")],
        )

        tree = extract(sandbox, members)

        assert (tree["l"], tree["sub/x"]) == ("-> sub", "file 644 b'x'")

    def test_chain_leading_out_written_through(self, sandbox: Path) -> None:
        # a -> . and s -> a/.. each stay inside by name, but s is the collection's parent on disk.
        members = collection(
            tar_symlink("a", "."),
            tar_symlink("s", "a/.."),
            tar_file("s/secret.txt", b"overwritten"),
            files=[listed_dir("a"), listed_dir("s"), listed("s/secret.txt", b"overwritten")],
        )

        refused(sandbox, members, r"s/secret\.txt in the artifact leads outside the collection")

    def test_longer_chain_leading_out(self, sandbox: Path) -> None:
        members = collection(
            tar_symlink("a", "."),
            tar_symlink("b", "a/a/a/.."),
            tar_file("b/new.txt", b"new"),
            files=[listed_dir("a"), listed_dir("b"), listed("b/new.txt", b"new")],
        )

        refused(sandbox, members, r"b/new\.txt in the artifact leads outside the collection")

    def test_chain_leading_out_read_through(self, sandbox: Path) -> None:
        # f's checksum would be of secret.txt, beside the collection, if it were read through s.
        members = collection(
            tar_symlink("a", "."),
            tar_symlink("s", "a/.."),
            tar_symlink("f", "s/secret.txt"),
            files=[listed_dir("a"), listed_dir("s"), listed("f", SECRET)],
        )

        refused(sandbox, members, "leads outside the collection")

    def test_link_made_to_lead_out_later(self, sandbox: Path) -> None:
        # s -> a/.. is inside while a doesn't exist; a -> . made afterwards turns s into the parent.
        members = collection(tar_symlink("s", "a/.."), tar_symlink("a", "."), files=[listed_dir("s"), listed_dir("a")])

        refused(sandbox, members, "s in the artifact leads outside the collection")

    @pytest.mark.parametrize(
        "links",
        [[("a", "b"), ("b", "a")], [("a", "a")], [("a", "b"), ("b", "c"), ("c", "a")]],
        ids=["two", "self", "three"],
    )
    def test_circular_as_files(self, sandbox: Path, links: list[tuple[str, str]]) -> None:
        members = collection(*(tar_symlink(n, t) for n, t in links), files=[listed(n, b"") for n, _ in links])

        refused(sandbox, members, "in the artifact can't be read")

    def test_circular_as_directories(self, sandbox: Path) -> None:
        members = collection(
            tar_symlink("a", "b"),
            tar_symlink("b", "a"),
            tar_file("a/x", b"x"),
            files=[listed_dir("a"), listed_dir("b"), listed("a/x", b"x")],
        )

        # The loop stops the directory a/x needs being made.
        refused(sandbox, members, "can't extract the artifact: File exists")

    def test_dangling_as_a_file(self, sandbox: Path) -> None:
        refused(
            sandbox,
            collection(tar_symlink("l", "missing"), files=[listed("l", b"")]),
            "l in the artifact can't be read",
        )

    def test_dangling_as_a_directory(self, sandbox: Path) -> None:
        # Harmless inside the collection, as ansible-galaxy makes it too.
        assert extract(sandbox, collection(tar_symlink("l", "missing"), files=[listed_dir("l")]))["l"] == "-> missing"

    def test_to_an_unlisted_member(self, sandbox: Path) -> None:
        members = collection(tar_file("unlisted", b"u"), tar_symlink("l", "unlisted"), files=[listed("l", b"u")])

        refused(sandbox, members, "l in the artifact can't be read")

    def test_to_a_directory_as_a_file(self, sandbox: Path) -> None:
        members = collection(tar_dir("d"), tar_symlink("l", "d"), files=[listed_dir("d"), listed("l", b"")])

        refused(sandbox, members, "l in the artifact can't be read")

    def test_through_another_link(self, sandbox: Path) -> None:
        members = collection(
            tar_dir("sub"),
            tar_file("sub/t", b"t"),
            tar_symlink("d", "sub"),
            tar_symlink("f", "d/t"),
            files=[listed_dir("sub"), listed("sub/t", b"t"), listed_dir("d"), listed("f", b"t")],
        )

        assert extract(sandbox, members)["f"] == "-> d/t"

    def test_checksum_of_the_target(self, sandbox: Path) -> None:
        members = collection(
            tar_file("t", b"t"), tar_symlink("l", "t"), files=[listed("t", b"t"), listed("l", b"other")]
        )

        refused(sandbox, members, "l in the artifact doesn't match its checksum")

    def test_before_its_target(self, sandbox: Path) -> None:
        # A build stores members in name order, so the link comes first: its checksum is of a file not yet extracted.
        members = collection(
            tar_symlink("a", "z.txt"), tar_file("z.txt", b"z"), files=[listed("a", b"z"), listed("z.txt", b"z")]
        )

        tree = extract(sandbox, members)

        assert (tree["a"], tree["z.txt"]) == ("-> z.txt", "file 644 b'z'")

    def test_dest_behind_a_symlink(self, sandbox: Path) -> None:
        # Extracted through a link to the work directory, so no directory's path is its real one: the same tree,
        # written through a link inside, and still nothing through a chain leading out.
        (sandbox / "via").symlink_to(sandbox / "work")
        members = collection(
            tar_dir("d"),
            tar_file("d/x", b"x"),
            tar_symlink("l", "d"),
            tar_file("l/y", b"y"),
            files=[listed_dir("d"), listed("d/x", b"x"), listed_dir("l"), listed("l/y", b"y")],
        )

        tree = extract(sandbox, members, via="via")

        assert {p: k for p, k in tree.items() if p.startswith(("d", "l"))} == {
            "d": "dir 755",
            "d/x": "file 644 b'x'",
            "d/y": "file 644 b'y'",
            "l": "-> d",
        }
        escaping = collection(
            tar_symlink("a", "."),
            tar_symlink("s", "a/.."),
            tar_file("s/secret.txt", b"overwritten"),
            files=[listed_dir("a"), listed_dir("s"), listed("s/secret.txt", b"overwritten")],
        )
        with pytest.raises(ArtifactError, match=r"s/secret\.txt in the artifact leads outside the collection"):
            _ = extract(sandbox, escaping, via="via")


class TestHardlinks:
    """A hardlink is a copy of a listed file in the collection, never a link to, or a read of, anything else."""

    def test_copy(self, sandbox: Path) -> None:
        members = collection(tar_file("t", b"t"), tar_hardlink("h", "t"), files=[listed("t", b"t"), listed("h", b"t")])

        tree = extract(sandbox, members)

        assert tree["h"] == "file 644 b't'"
        collection_dir = sandbox / "work" / "collection"
        assert (collection_dir / "h").stat().st_ino != (collection_dir / "t").stat().st_ino

    @pytest.mark.parametrize(
        "target", ["../secret.txt", "../../victim.txt", "/etc/passwd", "unlisted", "missing", "h", "d"]
    )
    def test_refused(self, sandbox: Path, target: str) -> None:
        members = collection(
            tar_file("unlisted", b"u"),
            tar_dir("d"),
            tar_hardlink("h", target),
            files=[listed_dir("d"), listed("h", SECRET)],
        )

        refused(sandbox, members, "h in the artifact can't be read")

    def test_through_a_chain_leading_out(self, sandbox: Path) -> None:
        members = collection(
            tar_symlink("a", "."),
            tar_symlink("s", "a/.."),
            tar_hardlink("h", "s"),
            files=[listed_dir("a"), listed_dir("s"), listed("h", SECRET)],
        )

        # Refused at s, the link that leads out, before h is copied through it.
        refused(sandbox, members, "s in the artifact leads outside the collection")

    def test_to_a_symlink_inside(self, sandbox: Path) -> None:
        members = collection(
            tar_file("t", b"t"),
            tar_symlink("l", "t"),
            tar_hardlink("h", "l"),
            files=[listed("t", b"t"), listed("l", b"t"), listed("h", b"t")],
        )

        assert extract(sandbox, members)["h"] == "file 644 b't'"

    def test_with_the_wrong_checksum(self, sandbox: Path) -> None:
        members = collection(tar_file("t", b"t"), tar_hardlink("h", "t"), files=[listed("t", b"t"), listed("h", b"x")])

        refused(sandbox, members, "h in the artifact doesn't match its checksum")


class TestUnpackedSize:
    """A tarball may only unpack to so much, extracted or not, so a small one can't fill the disk or take hours."""

    BIG = b"\0" * 64 * 1024

    def test_listed_file_over_the_limit(self, sandbox: Path) -> None:
        members = collection(tar_file("big", self.BIG), files=[listed("big", self.BIG)])

        message = r"can't extract the artifact: it unpacks to more than 32768 bytes; set ALAI_MAX_UNPACKED_SIZE"
        with pytest.raises(ArtifactError, match=message):
            _ = extract(sandbox, members, limit=32 * 1024)

    def test_unlisted_member_over_the_limit(self, sandbox: Path) -> None:
        # Never written, but read through all the same.
        members = collection(tar_file("padding", self.BIG), files=[])

        with pytest.raises(ArtifactError, match="more than 32768 bytes"):
            _ = extract(sandbox, members, limit=32 * 1024)

    def test_before_files_json(self, sandbox: Path) -> None:
        members = [tar_file("padding", self.BIG), *collection(files=[])]

        with pytest.raises(ArtifactError, match="more than 32768 bytes"):
            _ = extract(sandbox, members, limit=32 * 1024)

    def test_header_claiming_a_huge_member(self, sandbox: Path) -> None:
        # A header saying 1 TiB, with no data after it: refused without decompressing toward it.
        huge, _ = tar_file("huge", b"")
        huge.size = 1 << 40
        raw = b""
        for info, data in collection(files=[]):
            raw += info.tobuf() + (data or b"") + b"\0" * (-len(data or b"") % 512)
        tarball = sandbox / "artifact.tar.gz"
        _ = tarball.write_bytes(gzip.compress(raw + huge.tobuf()))
        dest = str(sandbox / "work" / "collection")

        with pytest.raises(ArtifactError, match="can't extract the artifact"):
            _artifacts.extract(
                str(tarball), dest, "ns.coll 1.0.0", expected=("ns.coll", Version("1.0.0")), limit=1 << 20
            )

    def test_header_claiming_a_huge_files_json(self, sandbox: Path) -> None:
        # FILES.json's header saying 1 TiB: only so much is set aside for it beforehand, so it's refused as corrupt
        # rather than alai failing to allocate that much, which aborts the process.
        manifest, files = collection(files=[])
        files[0].size = 1 << 40
        raw = b"".join(
            info.tobuf() + (data or b"") + b"\0" * (-len(data or b"") % 512) for info, data in (manifest, files)
        )
        tarball = sandbox / "artifact.tar.gz"
        _ = tarball.write_bytes(gzip.compress(raw))
        dest = str(sandbox / "work" / "collection")

        with pytest.raises(ArtifactError, match="can't extract the artifact"):
            _artifacts.extract(
                str(tarball), dest, "ns.coll 1.0.0", expected=("ns.coll", Version("1.0.0")), limit=1 << 20
            )

    def test_hardlink_copy_counts(self, sandbox: Path) -> None:
        # The tarball holds BIG once, under the limit; the hard link's copy of it is a second BIG, over it.
        members = collection(
            tar_file("big", self.BIG), tar_hardlink("h", "big"), files=[listed("big", self.BIG), listed("h", self.BIG)]
        )

        with pytest.raises(ArtifactError, match="more than 102400 bytes"):
            _ = extract(sandbox, members, limit=100 * 1024)

    def test_symlink_checksum_counts(self, sandbox: Path) -> None:
        # Checking the symlink's checksum reads BIG again; a tarball of links to one file could read it for hours.
        members = collection(
            tar_file("big", self.BIG), tar_symlink("l", "big"), files=[listed("big", self.BIG), listed("l", self.BIG)]
        )

        with pytest.raises(ArtifactError, match="more than 102400 bytes"):
            _ = extract(sandbox, members, limit=100 * 1024)

    def test_links_under_the_limit(self, sandbox: Path) -> None:
        members = collection(
            tar_file("big", self.BIG),
            tar_hardlink("h", "big"),
            tar_symlink("l", "big"),
            files=[listed("big", self.BIG), listed("h", self.BIG), listed("l", self.BIG)],
        )

        tree = extract(sandbox, members, limit=200 * 1024)

        assert (tree["h"], tree["l"]) == (f"file 644 {self.BIG!r}", "-> big")

    def test_under_the_limit(self, sandbox: Path) -> None:
        members = collection(tar_file("big", self.BIG), files=[listed("big", self.BIG)])

        assert "big" in extract(sandbox, members, limit=1 << 20)

    def test_no_limit(self, sandbox: Path) -> None:
        members = collection(tar_file("big", self.BIG), files=[listed("big", self.BIG)])

        assert "big" in extract(sandbox, members, limit=0)

    @pytest.mark.parametrize(("value", "expected"), [(None, 1 << 29), ("", 1 << 29), ("0", 0), ("1048576", 1 << 20)])
    def test_environment(self, value: str | None, expected: int) -> None:
        environ = {} if value is None else {"ALAI_MAX_UNPACKED_SIZE": value}

        assert _artifacts.max_unpacked_size(environ) == expected

    @pytest.mark.parametrize("value", ["-1", "1.5", "512MiB", "lots"])
    def test_environment_refused(self, value: str) -> None:
        with pytest.raises(ConfigError, match=f"ALAI_MAX_UNPACKED_SIZE: expected a number of bytes, .* got '{value}'"):
            _ = _artifacts.max_unpacked_size({"ALAI_MAX_UNPACKED_SIZE": value})


def test_executable_mode_of_a_hardlink(sandbox: Path) -> None:
    link, _ = tar_hardlink("h", "t")
    link.mode = 0o755
    members = collection(tar_file("t", b"t"), (link, None), files=[listed("t", b"t"), listed("h", b"t")])

    assert extract(sandbox, members)["h"] == "file 755 b't'"


def test_umask(sandbox: Path) -> None:
    tarball = sandbox / "artifact.tar.gz"
    _ = build_tarball(
        tarball, collection(tar_dir("d"), tar_file("d/x", b"x", 0o755), files=[listed_dir("d"), listed("d/x", b"x")])
    )
    dest = sandbox / "work" / "collection"

    old = os.umask(0o077)
    try:
        _artifacts.extract(str(tarball), str(dest), "ns.coll 1.0.0", expected=("ns.coll", Version("1.0.0")))
    finally:
        _ = os.umask(old)

    assert everything(dest)["d"] == "dir 700"
    assert everything(dest)["d/x"] == "file 700 b'x'"
    assert not os.access(dest / "MANIFEST.json", os.X_OK)
