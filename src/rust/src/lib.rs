// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! alai's native code, the private module `alai._native`: resolving collection versions, reading and safely extracting
//! collection tarballs, and placing a cached collection's files, with the GIL released. `src/alai/_native.pyi` types it for Python. The design
//! is in `design/performance.md`.

// alai runs where Ansible's control node does, which isn't Windows: a build there from the sdist is told so here,
// rather than failing on the Unix APIs the modules use.
#[cfg(not(unix))]
compile_error!("alai doesn't run on Windows, as Ansible's control node doesn't");

mod error;
mod extract;
mod link;
mod paths;
mod resolve;

use pyo3::prelude::*;

#[pymodule(gil_used = false)]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ExtractError", m.py().get_type::<error::ExtractError>())?;
    m.add("NoSolution", m.py().get_type::<error::NoSolution>())?;
    m.add_function(wrap_pyfunction!(extract::extract_collection, m)?)?;
    m.add_function(wrap_pyfunction!(link::link_tree, m)?)?;
    m.add_function(wrap_pyfunction!(resolve::resolve, m)?)?;
    Ok(())
}
