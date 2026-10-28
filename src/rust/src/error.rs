// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! The errors the native code raises: `NoSolution` for requirements that can't all be met, and `OSError` for the
//! filesystem's own.

use std::io;
use std::path::{Path, PathBuf};

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyOSError};
use pyo3::prelude::*;

create_exception!(
    alai._native,
    NoSolution,
    PyException,
    "The requirements can't all be met; the message explains why."
);

pub(crate) enum Error {
    /// An `OSError` from the filesystem, for `path`.
    Os(io::Error, PathBuf),
}

pub(crate) trait OsContext<T> {
    fn at(self, path: &Path) -> Result<T, Error>;
}

impl<T> OsContext<T> for io::Result<T> {
    fn at(self, path: &Path) -> Result<T, Error> {
        self.map_err(|e| Error::Os(e, path.to_owned()))
    }
}

impl From<Error> for PyErr {
    fn from(err: Error) -> PyErr {
        match err {
            Error::Os(err, path) => {
                let text = err.to_string();
                // io::Error's message ends in " (os error N)", which OSError's strerror doesn't have.
                let strerror = text.split(" (os error ").next().unwrap_or(&text).to_owned();
                match err.raw_os_error() {
                    Some(errno) => PyOSError::new_err((errno, strerror, path)),
                    None => PyOSError::new_err(strerror),
                }
            }
        }
    }
}
