# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

# alai's native code, built from src/rust/ by maturin. Each releases the GIL while it works.

import os

class ExtractError(Exception):
    """A tarball can't be read, or its collection can't be extracted safely: ``(kind, entry, detail)``.

    ``kind`` is one of ``corrupt`` (``detail`` says why), ``missing``, ``not_file``, ``not_dir``, ``path``, ``link``
    (``detail`` is the link's target), ``escape``, ``unreadable``, ``checksum``, ``too_large`` (``detail`` is the
    limit), ``json`` (``detail`` says why), ``not_object``, ``manifest`` (``detail`` is the collection it names),
    ``manifest_fields``, ``files_list``, ``files_entry`` (``detail`` is the entry, as JSON), ``files_checksum``,
    ``files_special`` or ``files_twice``.
    """

def extract_collection(
    tarball: str | os.PathLike[str],
    dest: str | os.PathLike[str],
    limit: int,
    expected: tuple[str, str, str] | None = None,
    /,
) -> tuple[str, str, str]:
    """Extract the collection artifact ``tarball`` into the new directory ``dest`` and return the namespace, name and
    version its ``MANIFEST.json`` gives, which with ``expected`` must be them.

    Two passes: the first reads only the headers, for each name's last member (``tar -r`` appends a replacement) and
    the last ``MANIFEST.json`` and ``FILES.json``, which are checked, with every entry ``FILES.json`` lists, before
    anything is written; the second writes only those last members, as the first recorded them, each file checked
    against its SHA-256. Every path and symlink must stay inside ``dest``, by name and on disk. Files are ``0644``, or
    ``0755`` if the tarball marks them executable, and directories ``0755``, masked by the process umask. More than
    ``limit`` bytes unpacked, in either pass, is ``too_large``, counting what a hard link copies and a symlink's
    checksum reads; 0 is no limit.
    """

def link_tree(source: str | os.PathLike[str], dest: str | os.PathLike[str], mode: str, /) -> None:
    """Recreate the directory ``source`` as the new directory ``dest``: directories with their modes and symlinks as
    they are, each file ``clone``d, ``hardlink``ed, ``copy``d with its mode, or ``symlink``ed to its absolute path. An
    ``OSError`` placing a file names the file in the cache."""
