// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! The errors the native code raises: `ExtractError` for an artifact that can't be extracted safely, and `OSError`
//! for the filesystem's own.

use std::io;
use std::path::{Path, PathBuf};

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyOSError};
use pyo3::prelude::*;

create_exception!(
    alai._native,
    ExtractError,
    PyException,
    "A tarball can't be read, or an entry can't be extracted safely."
);

pub(crate) enum Error {
    /// `ExtractError(kind, entry, detail)`.
    Extract(&'static str, String, String),
    /// An `OSError` from the filesystem, for `path`.
    Os(io::Error, PathBuf),
}

impl Error {
    pub(crate) fn extract(kind: &'static str, entry: &str) -> Self {
        Error::Extract(kind, entry.to_owned(), String::new())
    }
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
            Error::Extract(kind, entry, detail) => ExtractError::new_err((kind, entry, detail)),
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
