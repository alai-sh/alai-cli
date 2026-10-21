# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Write ``src/alai/_ansible_package.py``: the collections in the last few releases of the ``ansible`` package, which
``alai find`` ranks first. Each release's list is ``<major>/ansible.in`` in ansible-community/ansible-build-data. Run
from the repository root when a new major release is branched there, and commit the result:

    python scripts/ansible_package.py [MAJOR ...]

With no majors, the five highest in the repository are used.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

REPO = "ansible-community/ansible-build-data"
MODULE = pathlib.Path("src/alai/_ansible_package.py")
RELEASES = 5

HEADER = '''\
# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The collections in the ``ansible`` package, which ``alai find`` ranks first: written by
``scripts/ansible_package.py``, so edit that rather than this."""

RELEASES = "{releases}"
"""The ``ansible`` major releases the list is the union of."""

COLLECTIONS = frozenset(
    (
{collections}
    )
)
'''


def main(argv: list[str]) -> int:
    majors = [int(a) for a in argv] or _latest_majors()
    names: set[str] = set()
    for major in majors:
        text = _get(f"https://raw.githubusercontent.com/{REPO}/main/{major}/ansible.in").decode()
        names.update(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))
    releases = f"{min(majors)}-{max(majors)}" if len(majors) > 1 else str(majors[0])
    body = "\n".join(f'        "{name}",' for name in sorted(names))
    _ = MODULE.write_text(HEADER.format(releases=releases, collections=body))
    _ = sys.stderr.write(f"wrote {len(names)} collections from ansible {releases} to {MODULE}\n")
    return 0


def _latest_majors() -> list[int]:
    listing = json.loads(_get(f"https://api.github.com/repos/{REPO}/contents/"))
    majors = sorted(int(entry["name"]) for entry in listing if entry["type"] == "dir" and entry["name"].isdecimal())
    return majors[-RELEASES:]


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310  # fixed https URLs
        return response.read()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
