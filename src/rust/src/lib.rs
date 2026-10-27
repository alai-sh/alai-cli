// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! alai's native code, the private module `alai._native`: reading index files and searching plugin indexes, resolving
//! collection versions, reading and safely extracting collection tarballs, placing a cached collection's files, and
//! reading, writing and editing TOML, with the GIL released where it can be. `src/alai/_native.pyi` types it for
//! Python. The design is in `design/performance.md`.

// alai runs where Ansible's control node does, which isn't Windows: a build there from the sdist is told so here,
// rather than failing on the Unix APIs the modules use.
#[cfg(not(unix))]
compile_error!("alai doesn't run on Windows, as Ansible's control node doesn't");

mod error;
mod extract;
mod http;
mod index;
mod json;
mod link;
mod paths;
mod plugins;
mod resolve;
mod toml;

use pyo3::prelude::*;

#[pymodule(gil_used = false)]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ExtractError", m.py().get_type::<error::ExtractError>())?;
    m.add("HttpError", m.py().get_type::<http::HttpError>())?;
    m.add_class::<http::HttpClient>()?;
    m.add("NoSolution", m.py().get_type::<error::NoSolution>())?;
    m.add("TomlError", m.py().get_type::<toml::TomlError>())?;
    m.add("TomlEditError", m.py().get_type::<toml::TomlEditError>())?;
    m.add_class::<toml::TomlDocument>()?;
    m.add_function(wrap_pyfunction!(extract::extract_collection, m)?)?;
    m.add_function(wrap_pyfunction!(index::parse_index_lines, m)?)?;
    m.add_function(wrap_pyfunction!(link::link_tree, m)?)?;
    m.add_function(wrap_pyfunction!(plugins::plugin_lines, m)?)?;
    m.add_function(wrap_pyfunction!(plugins::plugin_lines_named, m)?)?;
    m.add_function(wrap_pyfunction!(plugins::plugin_names, m)?)?;
    m.add_function(wrap_pyfunction!(resolve::resolve, m)?)?;
    m.add_function(wrap_pyfunction!(toml::toml_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(toml::toml_loads, m)?)?;
    m.add_function(wrap_pyfunction!(toml::toml_read, m)?)?;
    Ok(())
}
