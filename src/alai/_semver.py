# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Collection versions and version requirements, compared as ansible-galaxy compares them.

Versions are SemVer 2.0.0, as ``ansible.utils.version.SemanticVersion`` parses them. Requirements use
ansible-galaxy's syntax, from ``ansible.galaxy.dependency_resolution.versioning.meets_requirements``, which the index
spec's "Requirement syntax" section says clients must match. ``VersionReq.parse_index`` reads that syntax exactly as
ansible-galaxy does, for an index's ``deps[].req``, which is copied verbatim from each collection's ``MANIFEST.json``.
``VersionReq.parse`` reads the stricter form ``alai.toml`` takes.
"""

from __future__ import annotations

import collections.abc as _c
import operator
import re

# ansible.utils.version.SEMVER_RE.
_SEMVER_RE = re.compile(
    r"""
    (?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)
    (?:-(?P<prerelease>
        (?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)
        (?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*
    ))?
    (?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?
    """,
    re.VERBOSE,
)

# alai.toml's clauses: an operator and a version of one to three parts with an optional pre-release, no build metadata.
_CLAUSE_RE = re.compile(
    r"""
    \s*(?P<op>==|!=|>=|<=|>|<)
    \s*(?P<version>(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){0,2}
        (?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?)
    \s*
    """,
    re.VERBOSE,
)

# ansible.module_utils.compat.version.LooseVersion.component_re.
_LOOSE_COMPONENT_RE = re.compile(r"(\d+|[a-z]+|\.)")
_LOOSE_EXTRA_RE = re.compile(r"[+-].+$")

# The longest operators first, so ">=" isn't read as ">".
_GALAXY_OPS = ("==", "!=", ">=", "<=", "=", ">", "<")

# SemVer precedence: (major, minor, patch, 1 for a release or 0 for a pre-release, pre-release identifiers).
_Key = tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]


class Version:
    """A collection version: SemVer 2.0.0, kept exactly as published.

    Versions sort by SemVer precedence and then, for two that differ only in build metadata, by their text, so the
    order is total and stable as the index spec asks. ``==`` compares the text. Requirements compare precedence alone,
    as ansible-galaxy does, so ``==1.0.0`` matches ``1.0.0+build``.
    """

    __slots__ = ("_key", "text")

    def __init__(self, text: str) -> None:
        match = _SEMVER_RE.fullmatch(text)
        if not match:
            raise ValueError(f"invalid version '{text}', expected a semantic version such as 1.2.3")
        self.text = text
        prerelease = match.group("prerelease")
        # SemVer precedence: a release sorts after its pre-releases, numeric identifiers sort numerically and before
        # alphanumeric ones, and a longer list of identifiers sorts after a prefix of it.
        self._key: _Key = (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            0 if prerelease else 1,
            tuple((0, int(i)) if i.isdigit() else (1, i) for i in prerelease.split(".")) if prerelease else (),
        )

    @property
    def is_prerelease(self) -> bool:
        return self._key[3] == 0

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Version({self.text!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self.text == other.text

    def __hash__(self) -> int:
        return hash(self.text)

    def __lt__(self, other: Version) -> bool:
        return (self._key, self.text) < (other._key, other.text)

    def __le__(self, other: Version) -> bool:
        return (self._key, self.text) <= (other._key, other.text)

    def __gt__(self, other: Version) -> bool:
        return (self._key, self.text) > (other._key, other.text)

    def __ge__(self, other: Version) -> bool:
        return (self._key, self.text) >= (other._key, other.text)


_OP_TESTS: dict[str, _c.Callable[[_Key, _Key], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


class VersionReq:
    """The versions a requirement accepts: every clause must hold, and no clauses accepts any version."""

    __slots__ = ("clauses", "text")

    def __init__(self, text: str, clauses: _c.Iterable[tuple[str, Version]]) -> None:
        self.text = text
        """As written, for messages."""
        self.clauses = tuple(clauses)
        """``(operator, version)`` pairs, with ansible-galaxy's ``=`` and bare versions as ``==``."""

    @classmethod
    def parse(cls, text: str) -> VersionReq:
        """Parse a requirement written in ``alai.toml``.

        ``*``, or comma-separated clauses of an operator (``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``) and a version,
        with whitespace allowed around each part. A version may leave out its minor and patch parts (``>=9`` is
        ``>=9.0.0``). This is ansible-galaxy's syntax without the parts that read as something else: a bare version,
        ``=``, build metadata (ignored when comparing) and anything ansible-galaxy would parse loosely.
        """
        if text.strip() == "*":
            return cls(text, ())
        clauses: list[tuple[str, Version]] = []
        for clause in text.split(","):
            match = _CLAUSE_RE.fullmatch(clause)
            if not match:
                raise ValueError(_clause_error(text, clause.strip()))
            version = match.group("version")
            core, sep, prerelease = version.partition("-")
            parts = core.split(".")
            padded = ".".join([*parts, *["0"] * (3 - len(parts))]) + sep + prerelease
            clauses.append((match.group("op"), Version(padded)))
        return cls(text, clauses)

    @classmethod
    def parse_index(cls, text: str) -> VersionReq:
        """Parse an index's ``deps[].req`` as ansible-galaxy parses a collection's dependencies.

        This follows ``meets_requirements`` exactly, quirks included: whitespace isn't allowed, a clause without an
        operator means ``==``, ``*`` as any clause matches anything, and the version is read with ``LooseVersion``
        then cut to three parts, so ``1.2.3.4`` means ``1.2.3``. A requirement ansible-galaxy fails on, such as
        ``^1.0.0``, raises ValueError. ansible-galaxy only fails on a bad clause once the clauses before it match, so
        ``>=1.0.0,`` is merely unmet for ``0.9.0`` there; here it's an error for every version.
        """
        clauses: list[tuple[str, Version]] = []
        for clause in text.split(","):
            # meets_requirements reads two characters as the operator when the second is "=" and one otherwise.
            # Trying the two-character operators first gives the same split for every clause LooseVersion can read.
            op = next((op for op in _GALAXY_OPS if clause.startswith(op)), "")
            version = clause[len(op) :] if op else clause
            if version == "*":
                continue
            try:
                clauses.append(("==" if op in ("", "=") else op, _from_loose(version)))
            except ValueError:
                raise ValueError(f"invalid version requirement '{text}': can't read '{clause}'") from None
        return cls(text, clauses)

    def contains(self, version: Version) -> bool:
        return all(_OP_TESTS[op](version._key, bound._key) for op, bound in self.clauses)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"VersionReq({self.text!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VersionReq) and self.clauses == other.clauses

    def __hash__(self) -> int:
        return hash(self.clauses)


def _from_loose(text: str) -> Version:
    """``SemanticVersion.from_loose_version(LooseVersion(text))``."""
    if not text:
        # LooseVersion("") never parses, and from_loose_version fails on it.
        raise ValueError("empty version")
    # LooseVersion tries int() on each component. Only the \d+ matches are all decimal digits, and int() takes those.
    components = [int(c) if c.isdecimal() else c for c in _LOOSE_COMPONENT_RE.split(text) if c and c != "."]

    # The core is the first three components, or fewer if a "-" or "+" comes first.
    end = 3
    for marker in ("-", "+"):
        if marker in components:
            end = min(end, components.index(marker))
    core = components[:end]
    if not all(isinstance(c, int) for c in core):
        raise ValueError(f"non-integer values in '{text}'")

    extra = _LOOSE_EXTRA_RE.search(text)
    numbers = [*core, *[0] * (3 - len(core))]
    return Version(".".join(str(n) for n in numbers) + (extra.group(0) if extra else ""))


def _clause_error(text: str, clause: str) -> str:
    where = f"invalid version requirement '{text}'"
    if not clause:
        return f"{where}: empty clause"
    if clause == "*":
        return f"{where}: '*' can't be combined with other clauses"
    if clause.startswith(("^", "~")):
        return f"{where}: '{clause[0]}' ranges aren't supported, write the bounds, such as '>=1.2.0,<2.0.0'"
    if clause.startswith(("=>", "=<")):
        return f"{where}: use '{clause[1]}=' rather than '{clause[:2]}' in '{clause}'"
    if clause.startswith("=") and not clause.startswith("=="):
        return f"{where}: use '==' rather than '=' in '{clause}'"
    if clause[0].isdigit():
        return f"{where}: '{clause}' needs an operator, such as '=={clause}' for exactly that version"
    if "+" in clause:
        return f"{where}: '{clause}' has build metadata, which version comparisons ignore"
    return f"{where}: expected an operator (==, !=, >=, <=, >, <) and a version such as 1.2.3, got '{clause}'"
