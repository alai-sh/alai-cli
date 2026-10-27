// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! Reading an index file's lines: the JSON and each field's type, checked in the order and with the words
//! `alai._index` used when it read them itself, so its messages are unchanged. What's left to Python is what has a
//! Python type: the version, its requirements and `requires_ansible`.

use pyo3::prelude::*;
use serde_json::{Map, Value};

use crate::json::{is_blank, is_integer, object};

/// The highest index line `v` this alai reads.
const LINE_VERSION: u64 = 1;

#[derive(IntoPyObject)]
pub(crate) enum Deprecated {
    Flag(bool),
    Message(String),
}

/// A line's fields once their types are checked: `cksum`, `size`, `yanked`, `published`, `deprecated`, each
/// of `deps` as `(name, req)`, and `requires_ansible`.
type Fields = (
    String,
    u64,
    bool,
    String,
    Deprecated,
    Vec<(String, String)>,
    Option<String>,
);

#[derive(IntoPyObject)]
pub(crate) enum Read {
    Fields(Fields),
    /// Why the line can't be read at all.
    Error(String),
}

/// Read collection `name`'s index file: each line that isn't blank, by number from 1, with its `vers` if it got that
/// far and what was read, and how many lines were skipped because their `v` needs a newer alai.
///
/// A line with a `vers` is reported with it even when a later field is wrong, so the caller checks the version first,
/// as it was checked first before; its message wins. A line is blank as Python's `bytes.strip` sees it, and a UTF-8
/// byte order mark is skipped, as `json.loads` skips one.
#[pyfunction]
pub(crate) fn parse_index_lines(
    py: Python<'_>,
    data: &[u8],
    name: &str,
) -> (Vec<(usize, Option<String>, Read)>, usize) {
    py.detach(|| {
        let mut entries = Vec::new();
        let mut newer = 0;
        for (number, line) in data.split(|&b| b == b'\n').enumerate() {
            if is_blank(line) {
                continue;
            }
            let obj = match object(line) {
                Ok(obj) => obj,
                Err(why) => {
                    entries.push((number + 1, None, Read::Error(why.to_owned())));
                    continue;
                }
            };
            // A newer line's 'v' is an integer above, not a float or a bool.
            if obj.get("v").and_then(Value::as_u64).is_some_and(|v| v > LINE_VERSION) {
                newer += 1;
                continue;
            }
            let (vers, read) = match fields(&obj, name) {
                Ok((vers, fields)) => (Some(vers), Read::Fields(fields)),
                Err((vers, why)) => (vers, Read::Error(why)),
            };
            entries.push((number + 1, vers, read));
        }
        (entries, newer)
    })
}

/// The line's fields, or why it can't be read with its `vers` if it got that far.
fn fields(obj: &Map<String, Value>, name: &str) -> Result<(String, Fields), (Option<String>, String)> {
    // Python compared 'v' with 1 by value, so 1.0 was accepted too, and true wasn't.
    if !obj.get("v").and_then(Value::as_f64).is_some_and(|v| v == 1.0) {
        return Err((None, format!("'v' must be {LINE_VERSION}")));
    }
    if obj.get("name").and_then(Value::as_str) != Some(name) {
        return Err((None, format!("'name' must be '{name}', the file's collection")));
    }
    let vers = string(obj, "vers").map_err(|e| (None, e))?.to_owned();
    let fail = |why: String| (Some(vers.clone()), why);
    let cksum = string(obj, "cksum").map_err(fail)?;
    if !is_sha256(cksum) {
        return Err(fail(format!("{vers}: 'cksum' must be a lowercase hex sha256")));
    }
    let size = match obj.get("size") {
        Some(v) if is_integer(v) => match v.as_u64() {
            Some(size) => size,
            None => return Err(fail(format!("{vers}: 'size' must not be negative"))),
        },
        _ => return Err(fail(required("size", "an integer"))),
    };
    let yanked = boolean(obj, "yanked").map_err(fail)?;
    let published = string(obj, "published").map_err(fail)?;
    let Some(raw_deps) = obj.get("deps").and_then(Value::as_array) else {
        return Err(fail(required("deps", "an array")));
    };
    let deprecated = match obj.get("deprecated") {
        None => Deprecated::Flag(false),
        Some(Value::Bool(flag)) => Deprecated::Flag(*flag),
        Some(Value::String(message)) => Deprecated::Message(message.clone()),
        Some(_) => return Err(fail(format!("{vers}: 'deprecated' must be true or a message"))),
    };
    let mut deps = Vec::with_capacity(raw_deps.len());
    for dep in raw_deps {
        let field = |key: &str| dep.as_object().and_then(|d| d.get(key)).and_then(Value::as_str);
        let (Some(dep_name), Some(req)) = (field("name"), field("req")) else {
            return Err(fail(format!(
                "{vers}: each of 'deps' must have a 'name' and a 'req' string"
            )));
        };
        deps.push((dep_name.to_owned(), req.to_owned()));
    }
    let requires_ansible = match obj.get("requires_ansible") {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) => Some(text.clone()),
        Some(_) => return Err(fail(format!("{vers}: 'requires_ansible' must be a string"))),
    };
    let fields = (
        cksum.to_owned(),
        size,
        yanked,
        published.to_owned(),
        deprecated,
        deps,
        requires_ansible,
    );
    Ok((vers, fields))
}

/// Whether `text` is a SHA-256 in lowercase hex.
fn is_sha256(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn required(key: &str, kind: &str) -> String {
    format!("'{key}' is required and must be {kind}")
}

fn string<'a>(obj: &'a Map<String, Value>, key: &str) -> Result<&'a str, String> {
    obj.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| required(key, "a string"))
}

fn boolean(obj: &Map<String, Value>, key: &str) -> Result<bool, String> {
    obj.get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| required(key, "a boolean"))
}
