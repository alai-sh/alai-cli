// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! What reading a JSON Lines file needs, as Python reads one: a line's object, and which lines are blank.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString};
use serde_json::{Map, Value};

/// Whether `line` is only whitespace, as Python's `bytes.strip` sees it: space, `\t`, `\n`, `\r`, `\x0b` and `\x0c`.
pub(crate) fn is_blank(line: &[u8]) -> bool {
    line.iter()
        .all(|b| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c))
}

/// The line as a JSON object, or why it isn't one: `not valid JSON` or `not a JSON object`. A UTF-8 byte order mark
/// is skipped, as `json.loads` skips one.
pub(crate) fn object(line: &[u8]) -> Result<Map<String, Value>, &'static str> {
    let line = line.strip_prefix(b"\xef\xbb\xbf").unwrap_or(line);
    match serde_json::from_slice(line) {
        Ok(Value::Object(obj)) => Ok(obj),
        Ok(_) => Err("not a JSON object"),
        Err(_) => Err("not valid JSON"),
    }
}

/// Whether `value` is a JSON integer, which Python reads as an `int`: not a float, such as `1.0`, or a bool.
pub(crate) fn is_integer(value: &Value) -> bool {
    value.as_i64().is_some() || value.as_u64().is_some()
}

/// `value` as `json.loads` would give it, but for an object's keys, which are in sorted order.
pub(crate) fn to_python<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        Value::Null => py.None().into_bound(py),
        Value::Bool(flag) => PyBool::new(py, *flag).to_owned().into_any(),
        Value::Number(number) => {
            if let Some(n) = number.as_i64() {
                n.into_pyobject(py)?.into_any()
            } else if let Some(n) = number.as_u64() {
                n.into_pyobject(py)?.into_any()
            } else {
                number.as_f64().unwrap_or(f64::NAN).into_pyobject(py)?.into_any()
            }
        }
        Value::String(text) => PyString::new(py, text).into_any(),
        Value::Array(items) => {
            let items = items.iter().map(|v| to_python(py, v)).collect::<PyResult<Vec<_>>>()?;
            PyList::new(py, items)?.into_any()
        }
        Value::Object(obj) => {
            let dict = PyDict::new(py);
            for (key, v) in obj {
                dict.set_item(key, to_python(py, v)?)?;
            }
            dict.into_any()
        }
    })
}
