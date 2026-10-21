# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Editing one array of strings in a TOML document, leaving every other byte of it as it was.

``alai add`` and ``alai remove`` change a requirement array in ``alai.toml``. Rather than parse the whole document into
an editable tree and write it back out, which risks re-laying-out anything else in it, the array's text is found with a
small scanner and the edit spliced into it. Every edit is then checked: the result, parsed with ``tomllib``, must be the
original document with only that array changed, or the edit is refused with ``TomlEditError`` and nothing changes.
The design is in ``design/manifest-editing.md``.
"""

from __future__ import annotations

import collections.abc as _c
import copy
import re
import sys

from ._common import Error

if sys.version_info >= (3, 11):
    import tomllib  # pyright: ignore[reportUnreachable]  # checked against the 3.10 floor
else:
    import tomli as tomllib

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
# A scalar runs to whitespace, a separator or a comment, except that a datetime may have a space between its date and
# its time.
_SCALAR = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?|[^\s,\]}#]+")


class TomlEditError(Error):
    """An edit that can't be made by splicing the text, or that wouldn't give exactly the intended document."""


class _Item:
    """One value in the target array: where its text is, and where the comma after it ends, if it has one."""

    __slots__ = ("comma_end", "end", "start")

    def __init__(self, start: int, end: int, comma_end: int | None) -> None:
        self.start = start
        self.end = end
        self.comma_end = comma_end


class _Array:
    """Where an array's brackets and items are in the text."""

    __slots__ = ("close", "items", "open")

    def __init__(self, open_: int, close: int, items: list[_Item]) -> None:
        self.open = open_
        self.close = close
        """The index of the closing ``]``."""
        self.items = items


def values(text: str, path: tuple[str, ...]) -> list[str] | None:
    """The array of strings at ``path``, or None if there's no such key."""
    node: object = tomllib.loads(text)
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = _t_dict(node)[key]
    if not isinstance(node, list) or not all(isinstance(v, str) for v in _t_list(node)):
        raise TomlEditError(f"{_dotted(path)} isn't an array of strings")
    return [str(v) for v in _t_list(node)]


def insert(text: str, path: tuple[str, ...], index: int, value: str) -> str:
    """Insert ``value`` before the item at ``index``, or after the last with ``index`` the number of items."""
    array = _find(text, path)
    newline = _newline(text)
    quoted = _quote(value)
    items = array.items
    if not items:
        if _multiline(text, array):
            indent = _indent(text, array.open) + "    "
            edited = _splice(text, array.open + 1, array.open + 1, f"{newline}{indent}{quoted},")
        else:
            edited = _splice(text, array.open + 1, array.open + 1, quoted)
    elif index < len(items):
        target = items[index]
        if _own_line(text, target.start):
            start = text.rfind("\n", 0, target.start) + 1
            edited = _splice(text, start, start, f"{text[start : target.start]}{quoted},{newline}")
        else:
            edited = _splice(text, target.start, target.start, f"{quoted}, ")
    else:
        last = items[-1]
        after = last.comma_end if last.comma_end is not None else last.end
        line_end = _line_end(text, after)
        if _multiline(text, array) and _own_line(text, last.start) and line_end < array.close:
            comma = "" if last.comma_end is not None else ","
            indent = text[text.rfind("\n", 0, last.start) + 1 : last.start]
            edited = _splice(text, last.end, last.end, comma)
            line_end += len(comma)
            edited = _splice(edited, line_end, line_end, f"{newline}{indent}{quoted},")
        else:
            edited = _splice(text, last.end, last.end, f", {quoted}")
    return _checked(text, edited, path, lambda old: [*old[:index], value, *old[index:]])


def replace(text: str, path: tuple[str, ...], index: int, value: str) -> str:
    """Replace the item at ``index`` with ``value``, in place."""
    item = _find(text, path).items[index]
    edited = _splice(text, item.start, item.end, _quote(value))
    return _checked(text, edited, path, lambda old: [*old[:index], value, *old[index + 1 :]])


def remove(text: str, path: tuple[str, ...], index: int) -> str:
    """Remove the item at ``index``. On a line of its own, the line goes; a comment on it is kept on its own line, as
    uv keeps one."""
    array = _find(text, path)
    items = array.items
    item = items[index]
    after = item.comma_end if item.comma_end is not None else item.end
    rest = text[after : _line_end(text, after)]
    alone = not rest.strip() or rest.strip().startswith("#")
    if _multiline(text, array) and _own_line(text, item.start) and alone and _line_end(text, after) < array.close:
        if rest.strip():
            edited = _splice(text, item.start, after + len(rest) - len(rest.lstrip()), "")
        else:
            # The whole line, through its newline, whether that's \n or \r\n.
            line_start = text.rfind("\n", 0, item.start) + 1
            edited = _splice(text, line_start, text.index("\n", after) + 1, "")
    elif index + 1 < len(items):
        edited = _splice(text, item.start, items[index + 1].start, "")
    elif index > 0:
        previous = items[index - 1]
        # The last of several: from the end of the one before, so its comma goes rather than leaving one dangling.
        end = item.end if item.comma_end is None else item.comma_end
        edited = _splice(text, previous.end, end, "" if item.comma_end is None else ",")
    else:
        edited = _splice(text, item.start, after, "")
    return _checked(text, edited, path, lambda old: [*old[:index], *old[index + 1 :]])


def create(text: str, path: tuple[str, ...], items: _c.Sequence[str]) -> str:
    """Add the array at ``path``, which doesn't exist: under its table's header if it has one, else in a new table at
    the end of the document."""
    newline = _newline(text)
    table, key = path[:-1], path[-1]
    line = f"{_key(key)} = [{', '.join(_quote(v) for v in items)}]"
    scan = _Scan(text)
    scan.document()
    if table in scan.headers:
        at = scan.table_ends[table]
        edited = _splice(text, at, at, f"{line}{newline}")
    else:
        prefix = "" if not text or text.endswith("\n") else newline
        gap = newline if text.strip() else ""
        header = f"[{'.'.join(_key(k) for k in table)}]" if table else ""
        block = f"{header}{newline}{line}{newline}" if table else f"{line}{newline}"
        edited = f"{text}{prefix}{gap}{block}"
    return _checked(text, edited, path, lambda _: list(items))


def set_table(text: str, path: tuple[str, ...], value: _c.Mapping[str, str | bool]) -> str:
    """Set the key at ``path`` to the inline table ``value``, such as ``{ path = "../col", editable = true }``: in
    place if it's there, else as a new line at the end of its table, made at the end of the document if it has no
    header yet."""
    newline = _newline(text)
    inline = "{ " + ", ".join(f"{_key(k)} = {_scalar(v)}" for k, v in value.items()) + " }"
    scan = _Scan(text)
    scan.document()
    found = scan.values.get(path)
    if found is not None:
        edited = _splice(text, found[0], found[1], inline)
    else:
        table, key = path[:-1], path[-1]
        line = f"{_key(key)} = {inline}"
        if table in scan.headers:
            at = scan.table_ends[table]
            edited = _splice(text, at, at, f"{line}{newline}")
        else:
            prefix = "" if not text or text.endswith("\n") else newline
            gap = newline if text.strip() else ""
            edited = f"{text}{prefix}{gap}[{_dotted(table)}]{newline}{line}{newline}"

    def change(node: dict[str, object]) -> None:
        node[path[-1]] = dict(value)

    return _checked_with(text, edited, path, change)


def remove_key(text: str, path: tuple[str, ...]) -> str:
    """Remove the key at ``path``, written on a line of its own, and that line with it. A table left empty keeps its
    header, as uv leaves ``[tool.uv.sources]``."""
    scan = _Scan(text)
    scan.document()
    found = scan.values.get(path)
    if found is None:
        raise TomlEditError(f"{_dotted(path)} isn't written as a key of its own, such as inside an inline table")
    start = text.rfind("\n", 0, found[0]) + 1
    # Only a comment can follow a value on its line, and it goes with it.
    end = _line_end(text, found[1])
    end = end + 2 if text.startswith("\r\n", end) else end + 1 if end < len(text) else end
    edited = _splice(text, start, end, "")

    def change(node: dict[str, object]) -> None:
        _ = node.pop(path[-1], None)

    return _checked_with(text, edited, path, change)


def _checked(before: str, after: str, path: tuple[str, ...], change: _c.Callable[[list[str]], list[str]]) -> str:
    """``after`` if it's ``before`` with only the array at ``path`` changed as ``change`` says, else an error."""

    def apply(node: dict[str, object]) -> None:
        current = node.get(path[-1])
        node[path[-1]] = change([str(v) for v in _t_list(current)] if isinstance(current, list) else [])

    return _checked_with(before, after, path, apply)


def _checked_with(
    before: str, after: str, path: tuple[str, ...], change: _c.Callable[[dict[str, object]], None]
) -> str:
    """``after`` if it's ``before`` with only the key at ``path`` changed, as ``change`` changes the table holding it,
    else an error."""
    old = tomllib.loads(before)
    expected = copy.deepcopy(old)
    node = expected
    for key in path[:-1]:
        node = _t_dict(node.setdefault(key, {}))
    change(node)
    try:
        got = tomllib.loads(after)
    except tomllib.TOMLDecodeError as e:
        raise TomlEditError(f"editing {_dotted(path)} would leave the file unreadable: {e}") from None
    if got != expected:
        raise TomlEditError(f"editing {_dotted(path)} would change more than it should")
    return after


def _find(text: str, path: tuple[str, ...]) -> _Array:
    scan = _Scan(text)
    scan.document()
    found = scan.values.get(path)
    if found is None:
        raise TomlEditError(f"{_dotted(path)} isn't written as a key of its own, such as inside an inline table")
    start, _ = found
    if text[start] != "[":
        raise TomlEditError(f"{_dotted(path)} isn't an array")
    return _Scan(text).array(start)


class _Scan:
    """Just enough of a TOML reader to say where each key's value is in the text.

    Keys in inline tables aren't recorded, so an array inside one can't be edited; the document is otherwise read as
    TOML 1.0 has it. Anything it doesn't expect is a ``TomlEditError``, never a guess.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.values: dict[tuple[str, ...], tuple[int, int]] = {}
        """Where each key's value is, by its full path."""
        self.headers: set[tuple[str, ...]] = set()
        """Tables with a ``[header]`` of their own (not ``[[arrays of tables]]``)."""
        self.table_ends: dict[tuple[str, ...], int] = {}
        """For each table with a header, where a new key would go: after its last key's line, else after the header."""

    def document(self) -> None:
        table: tuple[str, ...] = ()
        count: dict[tuple[str, ...], int] = {}
        text = self.text
        while True:
            self._blank()
            if self.pos >= len(text):
                return
            if text[self.pos] == "[":
                array = text.startswith("[[", self.pos)
                self.pos += 2 if array else 1
                keys = self._key()
                self._spaces()
                closing = "]]" if array else "]"
                if not text.startswith(closing, self.pos):
                    raise self._error("an unclosed table header")
                self.pos += len(closing)
                if array:
                    count[keys] = count.get(keys, -1) + 1
                    table = (*keys, f"[{count[keys]}]")
                else:
                    table = keys
                    self.headers.add(keys)
                self._end_of_line()
                self.table_ends[table] = self.pos
                continue
            keys = self._key()
            self._spaces()
            if text[self.pos : self.pos + 1] != "=":
                raise self._error("a key without '='")
            self.pos += 1
            self._spaces()
            start = self.pos
            self._value()
            self.values[(*table, *keys)] = (start, self.pos)
            self._end_of_line()
            self.table_ends[table] = self.pos

    def array(self, start: int) -> _Array:
        """The items of the array whose ``[`` is at ``start``."""
        text = self.text
        self.pos = start + 1
        items: list[_Item] = []
        while True:
            self._blank()
            if text[self.pos : self.pos + 1] == "]":
                return _Array(start, self.pos, items)
            begin = self.pos
            self._value()
            end = self.pos
            self._blank()
            comma = None
            if text[self.pos : self.pos + 1] == ",":
                self.pos += 1
                comma = self.pos
            elif text[self.pos : self.pos + 1] != "]":
                raise self._error("an array item without ',' or ']' after it")
            items.append(_Item(begin, end, comma))

    def _value(self) -> None:
        text = self.text
        pos: int = self.pos
        for quote in ('"""', "'''"):
            if text.startswith(quote, pos):
                end = pos + 3
                while True:
                    end = text.find(quote, end)
                    if end < 0:
                        raise self._error("an unclosed multi-line string")
                    if quote == '"""' and _escaped(text, end):
                        end += 1
                        continue
                    break
                # Up to two more quotes right after the delimiter are the end of the content, as in """a"""".
                run = 3
                while run < 5 and text[end + run : end + run + 1] == quote[0]:
                    run += 1
                self.pos = end + run
                return
        if text.startswith('"', pos):
            end = pos + 1
            while True:
                if end >= len(text) or text[end] in "\r\n":
                    raise self._error("an unclosed string")
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    break
                end += 1
            self.pos = end + 1
            return
        if text.startswith("'", pos):
            end = text.find("'", pos + 1)
            if end < 0 or "\n" in text[pos:end]:
                raise self._error("an unclosed string")
            self.pos = end + 1
            return
        if text.startswith("[", pos):
            self.pos = self.array(pos).close + 1
            return
        if text.startswith("{", pos):
            self.pos += 1
            while True:
                self._spaces()
                if text[self.pos : self.pos + 1] == "}":
                    self.pos += 1
                    return
                _ = self._key()
                self._spaces()
                if text[self.pos : self.pos + 1] != "=":
                    raise self._error("an inline table key without '='")
                self.pos += 1
                self._spaces()
                self._value()
                self._spaces()
                if text[self.pos : self.pos + 1] == ",":
                    self.pos += 1
                elif text[self.pos : self.pos + 1] != "}":
                    raise self._error("an inline table without ',' or '}'")
        match = _SCALAR.match(text, pos)
        if not match:
            raise self._error("a value it can't read")
        self.pos = match.end()

    def _key(self) -> tuple[str, ...]:
        text = self.text
        keys: list[str] = []
        while True:
            self._spaces()
            pos = self.pos
            if text.startswith(('"', "'"), pos):
                self._value()
                keys.append(str(tomllib.loads(f"k = {text[pos : self.pos]}")["k"]))
            else:
                match = _BARE_KEY.match(text, pos)
                if not match:
                    raise self._error("a key it can't read")
                keys.append(match.group())
                self.pos = match.end()
            self._spaces()
            if text[self.pos : self.pos + 1] != ".":
                return tuple(keys)
            self.pos += 1

    def _spaces(self) -> None:
        while self.text[self.pos : self.pos + 1] in (" ", "\t") and self.pos < len(self.text):
            self.pos += 1

    def _blank(self) -> None:
        """Whitespace, newlines and comments."""
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            if char in " \t\r\n":
                self.pos += 1
            elif char == "#":
                self.pos = _line_end(text, self.pos)
            else:
                return

    def _end_of_line(self) -> None:
        """Past the rest of a line that's ended: spaces, a comment, and the newline."""
        self._spaces()
        text = self.text
        if text[self.pos : self.pos + 1] == "#":
            self.pos = _line_end(text, self.pos)
        if text.startswith("\r\n", self.pos):
            self.pos += 2
        elif text[self.pos : self.pos + 1] == "\n":
            self.pos += 1
        elif self.pos < len(text):
            raise self._error("more on a line after its value")

    def _error(self, what: str) -> TomlEditError:
        line = self.text.count("\n", 0, self.pos) + 1
        return TomlEditError(f"line {line} has {what}")


def _escaped(text: str, index: int) -> bool:
    """Whether the character at ``index`` is escaped by an odd number of backslashes before it."""
    count = 0
    while index - count - 1 >= 0 and text[index - count - 1] == "\\":
        count += 1
    return count % 2 == 1


def _splice(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


def _line_end(text: str, pos: int) -> int:
    """The index of the newline ending the line ``pos`` is on, or the end of the text."""
    end = text.find("\n", pos)
    end = len(text) if end < 0 else end
    return end - 1 if end > 0 and text[end - 1] == "\r" else end


def _own_line(text: str, pos: int) -> bool:
    """Whether only whitespace comes before ``pos`` on its line."""
    return not text[text.rfind("\n", 0, pos) + 1 : pos].strip()


def _multiline(text: str, array: _Array) -> bool:
    return "\n" in text[array.open : array.close]


def _indent(text: str, pos: int) -> str:
    line = text[text.rfind("\n", 0, pos) + 1 : pos]
    return line[: len(line) - len(line.lstrip())]


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _scalar(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _quote(value)


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _quote(key)


def _dotted(path: tuple[str, ...]) -> str:
    return ".".join(_key(k) for k in path)


def _t_dict(value: object) -> dict[str, object]:
    return value  # pyright: ignore[reportReturnType]


def _t_list(value: object) -> list[object]:
    return value  # pyright: ignore[reportReturnType]
