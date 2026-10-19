# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""ansible-core versions and ``requires_ansible`` specifiers, which follow PEP 440 rather than SemVer.

ansible-core checks a collection's ``requires_ansible`` with ``packaging.specifiers.SpecifierSet`` against its own
version with any pre-release, post-release or dev part dropped (``_does_collection_support_ansible_version`` in
``lib/ansible/plugins/loader.py``). This module does the same without ``packaging``, whose specifiers module costs
~30 ms to import. Local versions (``+local``) aren't supported in specifiers.
"""

from __future__ import annotations

import collections.abc as _c
import re

# PEP 440's canonical pattern, without the local part.
_VERSION_PATTERN = r"""
    v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
"""
_VERSION_RE = re.compile(rf"\s*{_VERSION_PATTERN}\s*", re.VERBOSE | re.IGNORECASE)
_SPECIFIER_RE = re.compile(r"\s*(?P<op>~=|===|==|!=|<=|>=|<|>)\s*(?P<version>[^,;\s)]+)\s*")

_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}

# A sort key: (epoch, release with trailing zeros stripped, pre, post, dev), each made comparable as packaging's
# _cmpkey does with its infinities.
_Key = tuple[int, tuple[int, ...], tuple[int, int], int, tuple[int, int]]


class Version:
    """A PEP 440 version, such as ``2.19.0`` or ``2.19.0rc1``."""

    __slots__ = ("_key", "epoch", "is_postrelease", "is_prerelease", "release", "text")

    def __init__(self, text: str) -> None:
        match = _VERSION_RE.fullmatch(text)
        if not match:
            raise ValueError(f"invalid version '{text}'")
        self.text = text.strip()
        self.epoch = int(match.group("epoch") or 0)
        self.release = tuple(int(i) for i in match.group("release").split("."))
        pre = (_PRE_RANK[match.group("pre_l").lower()], int(match.group("pre_n") or 0)) if match.group("pre") else None
        post = int(match.group("post_n1") or match.group("post_n2") or 0) if match.group("post") else None
        dev = int(match.group("dev_n") or 0) if match.group("dev") else None
        self.is_prerelease = pre is not None or dev is not None
        self.is_postrelease = post is not None

        release = self.release
        while len(release) > 1 and release[-1] == 0:
            release = release[:-1]
        if pre is None:
            # A dev release of a final version sorts before its pre-releases; otherwise a final sorts after them.
            pre = (-1, 0) if post is None and dev is not None else (3, 0)
        self._key: _Key = (self.epoch, release, pre, -1 if post is None else post, (1, 0) if dev is None else (0, dev))

    @property
    def base(self) -> Version:
        """The epoch and release alone, as ``packaging``'s ``base_version``: ``2.19.0`` for ``2.19.0rc1``."""
        release = ".".join(str(i) for i in self.release)
        return Version(f"{self.epoch}!{release}" if self.epoch else release)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Version({self.text!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key == other._key

    def __hash__(self) -> int:
        return hash(self._key)

    def __lt__(self, other: Version) -> bool:
        return self._key < other._key

    def __le__(self, other: Version) -> bool:
        return self._key <= other._key

    def __gt__(self, other: Version) -> bool:
        return self._key > other._key

    def __ge__(self, other: Version) -> bool:
        return self._key >= other._key


class Specifier:
    """One clause of a specifier set, such as ``>=2.17`` or ``==2.18.*``."""

    __slots__ = ("op", "prefix", "text", "version")

    def __init__(self, text: str) -> None:
        match = _SPECIFIER_RE.fullmatch(text)
        if not match:
            raise ValueError(f"invalid specifier '{text.strip()}'")
        self.text = text.strip()
        self.op = match.group("op")
        version = match.group("version")
        self.prefix = False
        """``==`` or ``!=`` with a trailing ``.*``: a prefix match on the release."""

        if self.op == "===":
            # Arbitrary equality compares strings and needn't be a valid version.
            self.version: Version | None = None
            self.prefix = False
            return
        if self.op in ("==", "!=") and version.endswith(".*"):
            self.prefix = True
            version = version[:-2]
        try:
            self.version = Version(version)
        except ValueError:
            raise ValueError(f"invalid specifier '{self.text}'") from None
        if self.prefix and (self.version.is_prerelease or self.version.is_postrelease):
            raise ValueError(f"invalid specifier '{self.text}': '.*' can only follow a release such as 2.17")
        if self.op == "~=" and len(self.version.release) < 2:
            raise ValueError(f"invalid specifier '{self.text}': '~=' needs at least two release parts, such as ~=2.17")

    def contains(self, version: Version) -> bool:
        if self.version is None:
            return version.text.lower() == self.text[3:].strip().lower()
        bound = self.version
        if self.op in ("==", "!="):
            equal = _prefix_match(version, bound) if self.prefix else version == bound
            return equal if self.op == "==" else not equal
        if self.op == "~=":
            return version >= bound and _prefix_match(version, Version(".".join(str(i) for i in bound.release[:-1])))
        if self.op == "<=":
            return version <= bound
        if self.op == ">=":
            return version >= bound
        if self.op == "<":
            # <2.19 doesn't admit 2.19's own pre-releases, unless the bound is a pre-release itself.
            return version < bound and (bound.is_prerelease or not version.is_prerelease or version.base != bound.base)
        # >2.19 doesn't admit 2.19's post-releases, unless the bound is a post-release itself.
        return version > bound and (bound.is_postrelease or not version.is_postrelease or version.base != bound.base)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Specifier({self.text!r})"


class SpecifierSet:
    """Comma-separated specifiers, all of which must hold, such as ``>=2.17,<2.20``."""

    __slots__ = ("specifiers", "text")

    def __init__(self, text: str) -> None:
        self.text = text
        # packaging skips empty items, so ">=2.17," is ">=2.17".
        self.specifiers = tuple(Specifier(s) for s in text.split(",") if s.strip())

    def contains(self, version: Version) -> bool:
        """Whether ansible-core ``version`` meets every specifier, compared as ansible-core does, by its base
        version: ``2.19.0rc1`` meets ``>=2.19``."""
        base = version.base
        return all(s.contains(base) for s in self.specifiers)

    def __iter__(self) -> _c.Iterator[Specifier]:
        return iter(self.specifiers)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"SpecifierSet({self.text!r})"


def _prefix_match(version: Version, prefix: Version) -> bool:
    if version.epoch != prefix.epoch:
        return False
    n = len(prefix.release)
    release = (*version.release, *[0] * (n - len(version.release)))
    return release[:n] == prefix.release
