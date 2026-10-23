// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! TOML, through `toml_edit`, cargo's and uv's: reading a document as `tomllib` would give it, writing `alai.lock`, and
//! editing `alai.toml` as uv edits `pyproject.toml`, keeping the rest of its text. Every edit is checked: the text it
//! gives, read back, must be the document as it was with only that change made, or it's refused. The design is in
//! `design/manifest-editing.md`.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::error::OsContext;
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};
use toml_edit::{
    Array, ArrayOfTables, Datetime, DocumentMut, InlineTable, Item, Offset, RawString, Table, Time, Value,
};

create_exception!(
    alai._native,
    TomlError,
    PyValueError,
    "The text isn't valid TOML: the message says why and where."
);
create_exception!(
    alai._native,
    TomlEditError,
    PyException,
    "An edit that can't be made, or that wouldn't give exactly the intended document."
);

/// The TOML document `text` as `tomllib.loads` gives it: tables as dicts, arrays as lists, and datetimes as
/// `datetime` objects. TOML 1.1, so an inline table may span lines.
#[pyfunction]
pub(crate) fn toml_loads<'py>(py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyAny>> {
    let doc = parse(py, text)?;
    table_to_python(py, doc.as_table())
}

/// The TOML file at `path`, as `toml_loads` reads its text. A file that isn't UTF-8 is a `TomlError`, saying where;
/// one that can't be read an `OSError` naming it.
#[pyfunction]
pub(crate) fn toml_read<'py>(py: Python<'py>, path: PathBuf) -> PyResult<Bound<'py, PyAny>> {
    let text = read_text(py, &path)?;
    toml_loads(py, &text)
}

/// The table `table` as TOML, laid out the same way every time, with `header` before its first key:
///
/// - In `table` itself, a dict is a `[table]`, a list of dicts an `[[array]]` of them, each after a blank line, and
///   anything else a key, written before them.
/// - In a table, a dict of strings, booleans and integers is an inline table, `{ k = "v" }`, and any other dict is
///   written as dotted keys, `a.b.c = ...`.
/// - An array is on one line if that's at most `width` characters, else one item per line, with a trailing comma.
///
/// Keys and inline tables keep the dicts' order.
#[pyfunction]
pub(crate) fn toml_dumps(table: &Bound<'_, PyDict>, header: &str, width: usize) -> PyResult<String> {
    let mut doc = DocumentMut::new();
    let root = doc.as_table_mut();
    let mut first_value: Option<String> = None;
    for (key, value) in table.iter() {
        let key: String = key.extract()?;
        if let Ok(dict) = value.cast::<PyDict>() {
            root.insert(&key, Item::Table(table_from(dict, width)?));
        } else if let Some(tables) = list_of_dicts(&value)? {
            let mut array = ArrayOfTables::new();
            for dict in tables {
                array.push(table_from(&dict, width)?);
            }
            root.insert(&key, Item::ArrayOfTables(array));
        } else {
            root.insert(&key, Item::Value(value_from(&value, width)?));
            first_value.get_or_insert(key);
        }
    }
    match first_value.and_then(|key| root.key_mut(&key)) {
        Some(mut key) => key.leaf_decor_mut().set_prefix(header),
        None => doc.decor_mut().set_prefix(header),
    }
    Ok(doc.to_string())
}

/// A TOML document being edited: `alai.toml`, for `alai add` and `alai remove`.
#[pyclass(module = "alai._native")]
pub(crate) struct TomlDocument {
    doc: DocumentMut,
    /// What the document reads as: every edit changes this too, and the text it gives must read as exactly this.
    expected: Plain,
    /// The text, as last checked.
    text: String,
    /// Whether every line ended in `\r\n`, which `toml_edit` writes as `\n`, so they're put back.
    crlf: bool,
}

#[pymethods]
impl TomlDocument {
    #[new]
    fn new(py: Python<'_>, text: &str) -> PyResult<Self> {
        let doc = parse(py, text)?;
        let newlines = text.matches('\n').count();
        Ok(TomlDocument {
            expected: Plain::of_table(doc.as_table()),
            doc,
            text: text.to_owned(),
            crlf: newlines > 0 && text.matches("\r\n").count() == newlines,
        })
    }

    /// The TOML file at `path`, read as `toml_read` reads one, its bytes as they are, so its newlines are kept.
    #[staticmethod]
    fn read(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let text = read_text(py, &path)?;
        Self::new(py, &text)
    }

    fn __str__(&self) -> &str {
        &self.text
    }

    /// The document as `toml_loads` gives it.
    fn data<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        table_to_python(py, self.doc.as_table())
    }

    /// The array of strings at `path`, or None if there's no such key.
    fn values(&self, path: Vec<String>) -> PyResult<Option<Vec<String>>> {
        let mut item = self.doc.as_item();
        for key in &path {
            match item.get(key.as_str()) {
                Some(found) => item = found,
                None => return Ok(None),
            }
        }
        let strings = item.as_array().and_then(|array| {
            array
                .iter()
                .map(|v| v.as_str().map(str::to_owned))
                .collect::<Option<Vec<_>>>()
        });
        match strings {
            Some(strings) => Ok(Some(strings)),
            None => Err(TomlEditError::new_err(format!(
                "{} isn't an array of strings",
                dotted(&path)
            ))),
        }
    }

    /// Insert `value` at `index` in the array at `path`, as `uv add` does: comments kept with the item they follow,
    /// and the array then one item per line.
    fn insert(&mut self, path: Vec<String>, index: usize, value: &str) -> PyResult<()> {
        insert_formatted(self.array(&path)?, index, value);
        self.expected_array(&path)?
            .insert(index, Plain::String(value.to_owned()));
        self.check(&path)
    }

    /// Replace the item at `index` in the array at `path` with `value`, keeping its comments.
    fn replace(&mut self, path: Vec<String>, index: usize, value: &str) -> PyResult<()> {
        let array = self.array(&path)?;
        let _ = array.replace(index, value);
        reformat_multiline(array);
        self.expected_array(&path)?[index] = Plain::String(value.to_owned());
        self.check(&path)
    }

    /// Remove the item at `index` from the array at `path`, as `uv remove` does: a comment on the line before stays
    /// there, and the array is then one item per line, or `[]`.
    fn remove(&mut self, path: Vec<String>, index: usize) -> PyResult<()> {
        remove_at(self.array(&path)?, index);
        let _ = self.expected_array(&path)?.remove(index);
        self.check(&path)
    }

    /// Set the key at `path` to an array of `items`, one per line, creating the tables it's in: the one holding it
    /// with a header, as `[project.groups.test]`, those above it without, unless they're dotted keys or inline.
    fn create(&mut self, path: Vec<String>, items: Vec<String>) -> PyResult<()> {
        let mut array = Array::new();
        for item in &items {
            array.push(item.as_str());
        }
        reformat_multiline(&mut array);
        self.set(&path, Value::Array(array))?;
        let plain = Plain::Array(items.into_iter().map(Plain::String).collect());
        self.expected.set(&path, plain);
        self.check(&path)
    }

    /// Set the key at `path` to an inline table of `value`, in its order: in place, keeping any comment after it, or
    /// at the end of its table, created as `create` creates one.
    fn set_table(&mut self, path: Vec<String>, value: &Bound<'_, PyDict>) -> PyResult<()> {
        let mut table = InlineTable::new();
        let mut plain = BTreeMap::new();
        for (key, v) in value.iter() {
            let key: String = key.extract()?;
            let v = value_from(&v, usize::MAX)?;
            let _ = plain.insert(key.clone(), Plain::of_value(&v));
            let _ = table.insert(&key, v);
        }
        self.set(&path, Value::InlineTable(table))?;
        self.expected.set(&path, Plain::Table(plain));
        self.check(&path)
    }

    /// Remove the key at `path`, with any comment on its line or above it, as `uv remove` removes a source; its table
    /// stays, even if that leaves it empty.
    fn remove_key(&mut self, path: Vec<String>) -> PyResult<()> {
        let (key, parents) = split_path(&path)?;
        let mut item = self.doc.as_item_mut();
        for name in parents {
            item = item
                .as_table_like_mut()
                .and_then(|table| table.get_mut(name))
                .ok_or_else(|| TomlEditError::new_err(format!("there's no {}", dotted(&path))))?;
        }
        let removed = item.as_table_like_mut().and_then(|table| table.remove(key));
        if removed.is_none() {
            return Err(TomlEditError::new_err(format!("there's no {}", dotted(&path))));
        }
        self.expected.remove(&path);
        self.check(&path)
    }
}

impl TomlDocument {
    /// The array at `path`, which must be there.
    fn array(&mut self, path: &[String]) -> PyResult<&mut Array> {
        let mut item = self.doc.as_item_mut();
        for key in path {
            match item.as_table_like_mut().and_then(|table| table.get_mut(key)) {
                Some(found) => item = found,
                None => return Err(TomlEditError::new_err(format!("there's no {}", dotted(path)))),
            }
        }
        item.as_array_mut()
            .ok_or_else(|| TomlEditError::new_err(format!("{} isn't an array", dotted(path))))
    }

    fn expected_array(&mut self, path: &[String]) -> PyResult<&mut Vec<Plain>> {
        match self.expected.get_mut(path) {
            Some(Plain::Array(items)) => Ok(items),
            _ => Err(TomlEditError::new_err(format!("{} isn't an array", dotted(path)))),
        }
    }

    /// Set the key at `path` to `value`, keeping the decoration of the value it replaces, creating its tables.
    fn set(&mut self, path: &[String], mut value: Value) -> PyResult<()> {
        let (key, parents) = split_path(path)?;
        let mut item = self.doc.as_item_mut();
        for (depth, name) in parents.iter().enumerate() {
            if item.get(name.as_str()).is_none() {
                let holder = depth + 1 == parents.len();
                match item {
                    Item::Table(table) => {
                        let mut child = Table::new();
                        child.set_dotted(table.is_dotted());
                        child.set_implicit(!holder);
                        let _ = table.insert(name, Item::Table(child));
                    }
                    Item::Value(Value::InlineTable(table)) => {
                        let _ = table.insert(name, Value::InlineTable(InlineTable::new()));
                    }
                    _ => return Err(not_a_table(path, depth)),
                }
            }
            item = item
                .as_table_like_mut()
                .and_then(|table| table.get_mut(name))
                .filter(|child| child.is_table_like())
                .ok_or_else(|| not_a_table(path, depth + 1))?;
        }
        let table = item
            .as_table_like_mut()
            .ok_or_else(|| not_a_table(path, parents.len()))?;
        match table.get_mut(key).and_then(Item::as_value_mut) {
            Some(old) => {
                *value.decor_mut() = old.decor().clone();
                *old = value;
            }
            None => {
                if let Item::Value(Value::InlineTable(inline)) = item {
                    // The space before the closing brace is the last value's: it moves to the new last one, so the
                    // comma isn't written after it.
                    if let Some((_, last)) = inline.iter_mut().last() {
                        let suffix = last.decor().suffix().cloned();
                        last.decor_mut().set_suffix("");
                        if let Some(suffix) = suffix {
                            value.decor_mut().set_suffix(suffix);
                        }
                    }
                    let _ = inline.insert(key, value);
                } else if let Some(table) = item.as_table_like_mut() {
                    let _ = table.insert(key, Item::Value(value));
                }
            }
        }
        Ok(())
    }

    /// Render the edited document and check it reads as `expected`; it's only kept if it does.
    fn check(&mut self, path: &[String]) -> PyResult<()> {
        let mut text = self.doc.to_string();
        if self.crlf {
            text = text.replace("\r\n", "\n").replace('\n', "\r\n");
        }
        match text.parse::<DocumentMut>() {
            Err(e) => Err(TomlEditError::new_err(format!(
                "editing {} would leave the file unreadable: {}",
                dotted(path),
                message(&text, &e)
            ))),
            Ok(got) if Plain::of_table(got.as_table()) != self.expected => Err(TomlEditError::new_err(format!(
                "editing {} would change more than it should",
                dotted(path)
            ))),
            Ok(_) => {
                self.text = text;
                Ok(())
            }
        }
    }
}

/// A TOML value as what it reads as, without its layout, to compare documents by.
#[derive(Clone, Debug, PartialEq)]
enum Plain {
    String(String),
    Integer(i64),
    /// The bits, so NaN equals itself.
    Float(u64),
    Boolean(bool),
    Datetime(Datetime),
    Array(Vec<Plain>),
    Table(BTreeMap<String, Plain>),
}

impl Plain {
    fn of_table(table: &dyn toml_edit::TableLike) -> Plain {
        Plain::Table(table.iter().map(|(k, v)| (k.to_owned(), Plain::of_item(v))).collect())
    }

    fn of_item(item: &Item) -> Plain {
        match item {
            Item::None => Plain::Table(BTreeMap::new()),
            Item::Value(value) => Plain::of_value(value),
            Item::Table(table) => Plain::of_table(table),
            Item::ArrayOfTables(tables) => Plain::Array(tables.iter().map(|t| Plain::of_table(t)).collect()),
        }
    }

    fn of_value(value: &Value) -> Plain {
        match value {
            Value::String(v) => Plain::String(v.value().clone()),
            Value::Integer(v) => Plain::Integer(*v.value()),
            Value::Float(v) => Plain::Float(v.value().to_bits()),
            Value::Boolean(v) => Plain::Boolean(*v.value()),
            Value::Datetime(v) => Plain::Datetime(*v.value()),
            Value::Array(items) => Plain::Array(items.iter().map(Plain::of_value).collect()),
            Value::InlineTable(table) => Plain::of_table(table),
        }
    }

    fn get_mut(&mut self, path: &[String]) -> Option<&mut Plain> {
        path.iter().try_fold(self, |node, key| match node {
            Plain::Table(table) => table.get_mut(key),
            _ => None,
        })
    }

    /// Set the key at `path`, creating the tables it's in.
    fn set(&mut self, path: &[String], value: Plain) {
        let Some((key, parents)) = path.split_last() else {
            return;
        };
        let mut node = self;
        for name in parents {
            let Plain::Table(table) = node else { return };
            node = table
                .entry(name.clone())
                .or_insert_with(|| Plain::Table(BTreeMap::new()));
        }
        if let Plain::Table(table) = node {
            let _ = table.insert(key.clone(), value);
        }
    }

    fn remove(&mut self, path: &[String]) {
        if let Some((key, parents)) = path.split_last()
            && let Some(Plain::Table(table)) = self.get_mut(parents)
        {
            let _ = table.remove(key);
        }
    }
}

/// The file at `path` as UTF-8 text, read with the GIL released; where it isn't UTF-8 is said as a syntax error's
/// place is.
fn read_text(py: Python<'_>, path: &Path) -> PyResult<String> {
    let bytes = py.detach(|| fs::read(path)).at(path)?;
    String::from_utf8(bytes).map_err(|e| {
        let valid = String::from_utf8_lossy(&e.as_bytes()[..e.utf8_error().valid_up_to()]);
        TomlError::new_err(format!("not UTF-8 {}", position(&valid, valid.len())))
    })
}

fn parse(py: Python<'_>, text: &str) -> PyResult<DocumentMut> {
    py.detach(|| text.parse::<DocumentMut>())
        .map_err(|e| TomlError::new_err(message(text, &e)))
}

/// `toml_edit`'s error as one line, with where it is as `tomllib` says it: `... (at line 3, column 7)`, the column in
/// characters, from 1.
fn message(text: &str, err: &toml_edit::TomlError) -> String {
    let what = err.message().trim_end();
    let Some(span) = err.span() else {
        return what.to_owned();
    };
    format!("{what} {}", position(text, span.start))
}

/// Where byte `offset` of `text` is, as `tomllib` says it: `(at line 3, column 7)`, the column in characters, from 1.
fn position(text: &str, offset: usize) -> String {
    let before = &text[..offset.min(text.len())];
    let line = before.matches('\n').count() + 1;
    let column = before.rsplit('\n').next().unwrap_or(before).chars().count() + 1;
    format!("(at line {line}, column {column})")
}

fn table_to_python<'py>(py: Python<'py>, table: &dyn toml_edit::TableLike) -> PyResult<Bound<'py, PyAny>> {
    let dict = PyDict::new(py);
    for (key, item) in table.iter() {
        dict.set_item(key, item_to_python(py, item)?)?;
    }
    Ok(dict.into_any())
}

fn item_to_python<'py>(py: Python<'py>, item: &Item) -> PyResult<Bound<'py, PyAny>> {
    match item {
        Item::None => Ok(PyDict::new(py).into_any()),
        Item::Value(value) => value_to_python(py, value),
        Item::Table(table) => table_to_python(py, table),
        Item::ArrayOfTables(tables) => {
            let items = tables
                .iter()
                .map(|t| table_to_python(py, t))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, items)?.into_any())
        }
    }
}

fn value_to_python<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        Value::String(v) => PyString::new(py, v.value()).into_any(),
        Value::Integer(v) => v.value().into_pyobject(py)?.into_any(),
        Value::Float(v) => PyFloat::new(py, *v.value()).into_any(),
        Value::Boolean(v) => PyBool::new(py, *v.value()).to_owned().into_any(),
        Value::Datetime(v) => datetime_to_python(py, v.value())?,
        Value::Array(items) => {
            let items = items
                .iter()
                .map(|v| value_to_python(py, v))
                .collect::<PyResult<Vec<_>>>()?;
            PyList::new(py, items)?.into_any()
        }
        Value::InlineTable(table) => table_to_python(py, table)?,
    })
}

/// A datetime as `tomllib` gives it: a `datetime`, aware if it has an offset, a `date` or a `time`, with fractional
/// seconds cut to microseconds.
fn datetime_to_python<'py>(py: Python<'py>, value: &Datetime) -> PyResult<Bound<'py, PyAny>> {
    let module = py.import("datetime")?;
    let time = |t: &Time| {
        (
            t.hour,
            t.minute,
            t.second.unwrap_or(0),
            t.nanosecond.unwrap_or(0) / 1000,
        )
    };
    match (value.date, value.time) {
        (Some(d), Some(t)) => {
            let (hour, minute, second, micro) = time(&t);
            let tz = match value.offset {
                None => py.None().into_bound(py),
                Some(Offset::Z) => module.getattr("timezone")?.getattr("utc")?,
                Some(Offset::Custom { minutes }) => {
                    let delta = module.getattr("timedelta")?.call1((0, i32::from(minutes) * 60))?;
                    module.getattr("timezone")?.call1((delta,))?
                }
            };
            module
                .getattr("datetime")?
                .call1((d.year, d.month, d.day, hour, minute, second, micro, tz))
        }
        (Some(d), None) => module.getattr("date")?.call1((d.year, d.month, d.day)),
        (None, Some(t)) => module.getattr("time")?.call1(time(&t)),
        (None, None) => Err(PyValueError::new_err("a datetime with neither a date nor a time")),
    }
}

/// A table for `toml_dumps`: its dicts are inline tables, or dotted keys when they hold more than scalars.
fn table_from(dict: &Bound<'_, PyDict>, width: usize) -> PyResult<Table> {
    let mut table = Table::new();
    for (key, value) in dict.iter() {
        let key: String = key.extract()?;
        let item = match value.cast::<PyDict>() {
            Ok(inner) if !inner.values().iter().all(|v| is_scalar(&v)) => {
                let mut dotted = table_from(inner, width)?;
                dotted.set_dotted(true);
                Item::Table(dotted)
            }
            _ => Item::Value(value_from(&value, width)?),
        };
        let _ = table.insert(&key, item);
    }
    Ok(table)
}

fn is_scalar(value: &Bound<'_, PyAny>) -> bool {
    value.is_instance_of::<PyString>() || value.is_instance_of::<PyInt>() || value.is_instance_of::<PyFloat>()
}

/// The dicts in `value` if it's a non-empty list of nothing else.
fn list_of_dicts<'py>(value: &Bound<'py, PyAny>) -> PyResult<Option<Vec<Bound<'py, PyDict>>>> {
    let Ok(list) = value.cast::<PyList>() else {
        return Ok(None);
    };
    if list.is_empty() || !list.iter().all(|v| v.is_instance_of::<PyDict>()) {
        return Ok(None);
    }
    Ok(Some(
        list.iter().map(|v| v.cast_into::<PyDict>()).collect::<Result<_, _>>()?,
    ))
}

fn value_from(value: &Bound<'_, PyAny>, width: usize) -> PyResult<Value> {
    // bool before int, which it's a subclass of.
    if let Ok(flag) = value.cast::<PyBool>() {
        Ok(Value::from(flag.is_true()))
    } else if value.is_instance_of::<PyInt>() {
        Ok(Value::from(value.extract::<i64>()?))
    } else if value.is_instance_of::<PyFloat>() {
        Ok(Value::from(value.extract::<f64>()?))
    } else if let Ok(text) = value.cast::<PyString>() {
        Ok(Value::from(text.to_str()?))
    } else if let Ok(dict) = value.cast::<PyDict>() {
        let mut table = InlineTable::new();
        for (key, v) in dict.iter() {
            let _ = table.insert(&key.extract::<String>()?, value_from(&v, width)?);
        }
        Ok(Value::InlineTable(table))
    } else if let Ok(list) = value.cast::<PyList>() {
        let mut array = Array::new();
        for v in list.iter() {
            array.push(value_from(&v, width)?);
        }
        if Value::Array(array.clone()).to_string().chars().count() > width {
            for item in array.iter_mut() {
                item.decor_mut().set_prefix("\n    ");
                item.decor_mut().set_suffix("");
            }
            array.set_trailing("\n");
            array.set_trailing_comma(true);
        }
        Ok(Value::Array(array))
    } else {
        Err(PyValueError::new_err(format!(
            "can't write a {} as TOML",
            value.get_type().name()?
        )))
    }
}

fn split_path(path: &[String]) -> PyResult<(&str, &[String])> {
    path.split_last()
        .map(|(key, parents)| (key.as_str(), parents))
        .ok_or_else(|| PyValueError::new_err("an empty key path"))
}

fn not_a_table(path: &[String], depth: usize) -> PyErr {
    TomlEditError::new_err(format!("{} isn't a table", dotted(&path[..depth.min(path.len())])))
}

/// A key path as it's written in TOML: `project.groups."my.group".collections`.
fn dotted(path: &[String]) -> String {
    path.iter()
        .map(|key| {
            if !key.is_empty() && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
                key.clone()
            } else {
                format!("{key:?}")
            }
        })
        .collect::<Vec<_>>()
        .join(".")
}

// What follows is uv's layout for a requirement array, from `uv-project-edit` (uv 0.13.5, `add_dependency`,
// `remove_dependency_at` and `reformat_array_multiline`), so `alai add` and `alai remove` leave an array as `uv add` and
// `uv remove` leave one: one item per line with a trailing comma, comments kept with the item they follow.

/// Insert `value` at `index`, moving comments so they stay with the lines they were on, as `uv add` does.
fn insert_formatted(array: &mut Array, index: usize, value: &str) {
    let mut value = Value::from(value);
    let len = array.len();
    if index == len {
        // At the end: the comments after the last item, such as one at the end of its line, stay before the new one.
        value.decor_mut().set_prefix(array.trailing().clone());
        array.set_trailing("");
    } else if index > 0
        && let Some(next) = array.get_mut(index)
    {
        // Between two: the comment at the end of the line before stays there, before the new item.
        let prefix = next.decor().prefix().cloned().unwrap_or_default();
        next.decor_mut().set_prefix("");
        value.decor_mut().set_prefix(prefix);
    }
    array.insert_formatted(index, value);

    if array.len() > 1 && index == 0 {
        // At the front: a comment on the bracket's line stays there, and one on a line of its own moves down with
        // the item it was above.
        let prefix = array
            .get(1)
            .and_then(|v| v.decor().prefix())
            .cloned()
            .unwrap_or_default();
        match prefix
            .as_str()
            .and_then(|p| p.split_once(['\r', '\n']).map(|(first, rest)| (p, first, rest)))
        {
            Some((whole, first, rest)) => {
                let newline = match &whole[first.len()..] {
                    n if n.starts_with("\r\n") => "\r\n",
                    n if n.starts_with('\r') => "\r",
                    _ => "\n",
                };
                let last = rest.lines().last().unwrap_or_default();
                if let Some(new) = array.get_mut(0) {
                    new.decor_mut().set_prefix(format!("{first}{newline}{last}"));
                }
                if let Some(old) = array.get_mut(1) {
                    old.decor_mut().set_prefix(format!("{newline}{rest}"));
                }
            }
            None => {
                if let Some(new) = array.get_mut(0) {
                    new.decor_mut().set_prefix(prefix);
                }
            }
        }
    }
    reformat_multiline(array);
}

/// Remove the item at `index`, its prefix, which holds the comment at the end of the line before, going to what
/// follows it.
fn remove_at(array: &mut Array, index: usize) {
    let prefix = array
        .get(index)
        .and_then(|v| v.decor().prefix())
        .and_then(RawString::as_str)
        .filter(|p| !p.is_empty())
        .map(str::to_owned);
    if let Some(prefix) = prefix {
        match array.get_mut(index + 1) {
            Some(next) => {
                let existing = next.decor().prefix().and_then(RawString::as_str).unwrap_or_default();
                let joined = format!("{prefix}{existing}");
                next.decor_mut().set_prefix(joined);
            }
            None => {
                let existing = array.trailing().as_str().unwrap_or_default();
                let joined = format!("{prefix}{existing}");
                array.set_trailing(joined);
            }
        }
    }
    let _ = array.remove(index);
    reformat_multiline(array);
}

/// A comment in an item's decoration: on a line of its own, or at the end of a line after this whitespace.
struct Comment {
    text: String,
    end_of_line: Option<String>,
}

fn comments(raw: Option<&RawString>) -> Vec<Comment> {
    let mut found = Vec::new();
    let (mut prev_empty, mut prev_comment) = (false, false);
    for line in raw.and_then(RawString::as_str).unwrap_or_default().lines() {
        let trimmed = line.trim();
        if let Some((before, comment)) = line.split_once('#') {
            let end_of_line = if prev_empty || prev_comment {
                None
            } else {
                let spaces = before.len() - before.trim_end().len();
                Some(before[before.len() - spaces..].to_owned())
            };
            found.push(Comment {
                text: format!("#{}", comment.trim_end()),
                end_of_line,
            });
            prev_comment = true;
        } else {
            prev_comment = false;
        }
        prev_empty = trimmed.is_empty();
    }
    found
}

/// Lay the array out one item per line, with a trailing comma, indented as its first item is (four spaces if it's
/// on the bracket's line), each comment kept on its own line or at the end of the line it was on.
fn reformat_multiline(array: &mut Array) {
    // Without a trailing comma, comments after the last item are in its suffix; with one, they follow the comma.
    if !array.trailing_comma()
        && let Some(last) = array.iter_mut().last()
        && let Some(suffix) = last.decor().suffix().and_then(RawString::as_str)
        && suffix.contains('#')
    {
        let suffix = suffix.to_owned();
        last.decor_mut().set_suffix("");
        let trailing = array.trailing().as_str().unwrap_or_default();
        let joined = format!("{suffix}{trailing}");
        array.set_trailing(joined);
    }

    let indent = array
        .iter()
        .next()
        .and_then(|first| first.decor().prefix())
        .and_then(RawString::as_str)
        // Only from an item on a line of its own: uv also takes the space after the bracket of a one-line array, and
        // so indents by one space after `uv remove` of its first item.
        .filter(|p| p.contains('\n'))
        .and_then(|p| p.lines().last())
        .map(|line| line.split_once('#').map_or(line, |(before, _)| before))
        .filter(|indent| !indent.is_empty())
        .unwrap_or("    ");
    let line_start = format!("\n{indent}");

    let place = |found: Vec<Comment>, out: &mut String| {
        for comment in found {
            match &comment.end_of_line {
                None => out.push_str(&line_start),
                Some(spaces) => out.push_str(spaces),
            }
            out.push_str(&comment.text);
        }
    };
    for item in array.iter_mut() {
        let decor = item.decor_mut();
        let mut prefix = String::new();
        let mut found = comments(decor.prefix());
        found.extend(comments(decor.suffix()));
        place(found, &mut prefix);
        prefix.push_str(&line_start);
        decor.set_prefix(prefix);
        decor.set_suffix("");
    }
    let mut trailing = String::new();
    place(comments(Some(array.trailing())), &mut trailing);
    if !trailing.is_empty() || !array.is_empty() {
        trailing.push('\n');
    }
    array.set_trailing(trailing);
    array.set_trailing_comma(true);
}
