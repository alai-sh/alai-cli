// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! Searching an index's plugin index, `plugins.jsonl`: finding the lines a search wants in the raw bytes and reading
//! only those, as `alai._plugins` did itself. A line that can't be read, or that needs a newer alai, is skipped, as a
//! plugin index is a search aid.

use std::collections::HashSet;

use memchr::memmem;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde_json::Value;

use crate::json::{is_blank, object, to_python};

/// The highest line `v` this alai reads. Signed, unlike the index reader's, as a line with a `v` of 0 or less is
/// still read: a plugin index is a search aid, so only one from the future is skipped.
const LINE_VERSION: i64 = 1;

/// A line's `type`, `name`, `collection`, `since`, `until`, `short_description` and `routing`, the strings `None` when
/// they aren't strings and `routing` when it isn't an object.
type Plugin = (
    String,
    String,
    String,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<Value>,
);

/// A plugin as Python takes it: its fields, and the line itself, for what reads the rest of it.
type PyPlugin<'py> = (
    String,
    String,
    String,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<Bound<'py, PyAny>>,
    Bound<'py, PyBytes>,
);

/// Every readable line that contains all of `tokens`, or every readable line if there are none. The first token is
/// searched for, so it should be the rarest.
#[pyfunction]
pub(crate) fn plugin_lines<'py>(py: Python<'py>, data: &[u8], tokens: Vec<Vec<u8>>) -> PyResult<Vec<PyPlugin<'py>>> {
    let found = py.detach(|| {
        let Some((first, rest)) = tokens.split_first() else {
            return data
                .split(|&b| b == b'\n')
                .filter(|line| !is_blank(line))
                .filter_map(|line| read(line).map(|plugin| (plugin, line)))
                .collect();
        };
        let rest: Vec<_> = rest.iter().map(memmem::Finder::new).collect();
        let mut found = Vec::new();
        let mut at = 0;
        for hit in memmem::find_iter(data, first) {
            if hit < at {
                continue; // in a line already looked at
            }
            let (start, end) = line_at(data, hit);
            at = end + 1;
            let line = &data[start..end];
            if rest.iter().all(|token| token.find(line).is_some())
                && let Some(plugin) = read(line)
            {
                found.push((plugin, line));
            }
        }
        found
    });
    to_plugins(py, found)
}

/// Every readable line with a `"name": "..."` whose value is one of `names`, each once.
#[pyfunction]
pub(crate) fn plugin_lines_named<'py>(
    py: Python<'py>,
    data: &[u8],
    names: HashSet<String>,
) -> PyResult<Vec<PyPlugin<'py>>> {
    let found = py.detach(|| {
        let mut found = Vec::new();
        let mut at = 0;
        for (offset, value) in name_values(data) {
            if offset < at || !std::str::from_utf8(value).is_ok_and(|v| names.contains(v)) {
                continue;
            }
            let (start, end) = line_at(data, offset);
            at = end + 1;
            let line = &data[start..end];
            if let Some(plugin) = read(line) {
                found.push((plugin, line));
            }
        }
        found
    });
    to_plugins(py, found)
}

/// Every `"name": "..."` value in `data` that has no escape, read without parsing the lines: the names to suggest from.
#[pyfunction]
pub(crate) fn plugin_names(py: Python<'_>, data: &[u8]) -> HashSet<String> {
    py.detach(|| {
        name_values(data)
            .filter_map(|(_, value)| std::str::from_utf8(value).ok().map(str::to_owned))
            .collect()
    })
}

/// The start and end of the line holding the byte at `offset`, its newline left out.
fn line_at(data: &[u8], offset: usize) -> (usize, usize) {
    let start = memchr::memrchr(b'\n', &data[..offset]).map_or(0, |i| i + 1);
    let end = memchr::memchr(b'\n', &data[offset..]).map_or(data.len(), |i| offset + i);
    (start, end)
}

/// Where each value of a `"name"` key in `data` is, and the value up to its closing quote, as the regex
/// `"name"\s*:\s*"([^"\\]*)"` finds them: whitespace is Python's `\s` for bytes, and a value with an escape in it
/// isn't one.
fn name_values(data: &[u8]) -> impl Iterator<Item = (usize, &[u8])> {
    const KEY: &[u8] = b"\"name\"";
    let space = |b: &u8| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c);
    let mut keys = memmem::find_iter(data, KEY);
    std::iter::from_fn(move || {
        for found in keys.by_ref() {
            let mut i = found + KEY.len();
            while data.get(i).is_some_and(space) {
                i += 1;
            }
            if data.get(i) != Some(&b':') {
                continue;
            }
            i += 1;
            while data.get(i).is_some_and(space) {
                i += 1;
            }
            if data.get(i) != Some(&b'"') {
                continue;
            }
            let start = i + 1;
            let Some(len) = memchr::memchr2(b'"', b'\\', &data[start..]) else {
                continue;
            };
            if data[start + len] == b'\\' {
                continue;
            }
            // A value holds no '"', so the next key can't start inside this one.
            return Some((found, &data[start..start + len]));
        }
        None
    })
}

/// The line as a plugin, or None if it can't be read: valid JSON, an object, a `v` no higher than this alai reads, and
/// a `type`, a `name` and a `collection` with one dot.
fn read(line: &[u8]) -> Option<Plugin> {
    let obj = object(line).ok()?;
    // An integer, not a float or a bool, as the reader checked before.
    if !obj.get("v").and_then(Value::as_i64).is_some_and(|v| v <= LINE_VERSION) {
        return None;
    }
    let text = |key: &str| obj.get(key).and_then(Value::as_str).map(str::to_owned);
    let (type_, name, collection) = (text("type")?, text("name")?, text("collection")?);
    if collection.matches('.').count() != 1 {
        return None;
    }
    let routing = obj.get("routing").filter(|r| r.is_object()).cloned();
    Some((
        type_,
        name,
        collection,
        text("since"),
        text("until"),
        text("short_description"),
        routing,
    ))
}

fn to_plugins<'py>(py: Python<'py>, found: Vec<(Plugin, &[u8])>) -> PyResult<Vec<PyPlugin<'py>>> {
    found
        .into_iter()
        .map(
            |((type_, name, collection, since, until, description, routing), line)| {
                let routing = routing.map(|r| to_python(py, &r)).transpose()?;
                Ok((
                    type_,
                    name,
                    collection,
                    since,
                    until,
                    description,
                    routing,
                    PyBytes::new(py, line),
                ))
            },
        )
        .collect()
}
