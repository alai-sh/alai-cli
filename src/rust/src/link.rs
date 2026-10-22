// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! Placing a cached collection's files where it's installed.

use std::fs::{self, DirBuilder, File};
use std::io;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt, symlink};
use std::path::{Path, PathBuf};

use pyo3::prelude::*;

use crate::error::{Error, OsContext};
use crate::paths::create;

/// Recreate the directory `source` as the new directory `dest`: directories with their modes and symlinks as they are,
/// and each file cloned, hardlinked, copied with its mode, or symlinked to its absolute path in the cache (`mode`).
/// An error placing a file names the file in the cache, so the caller can say which couldn't be linked.
#[pyfunction]
pub(crate) fn link_tree(py: Python<'_>, source: PathBuf, dest: PathBuf, mode: String) -> PyResult<()> {
    let link = match mode.as_str() {
        "clone" => clone_file,
        "hardlink" => hardlink_file,
        "copy" => copy_file,
        "symlink" => symlink_file,
        _ => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown link mode '{mode}'"
            )));
        }
    };
    py.detach(|| link_dir(&source, &dest, link))?;
    Ok(())
}

type Link = fn(&Path, &Path) -> Result<(), Error>;

fn link_dir(source: &Path, dest: &Path, link: Link) -> Result<(), Error> {
    let meta = fs::metadata(source).at(source)?;
    DirBuilder::new()
        .mode(meta.permissions().mode() & 0o7777)
        .create(dest)
        .at(dest)?;
    for entry in fs::read_dir(source).at(source)? {
        let entry = entry.at(source)?;
        let (src, dst) = (entry.path(), dest.join(entry.file_name()));
        // From the directory entry, not followed, so a symlinked directory is made a link, not walked.
        let kind = entry.file_type().at(&src)?;
        if kind.is_symlink() {
            symlink(fs::read_link(&src).at(&src)?, &dst).at(&dst)?;
        } else if kind.is_dir() {
            link_dir(&src, &dst, link)?;
        } else {
            link(&src, &dst)?;
        }
    }
    Ok(())
}

/// Open `source` and create `dest` with its mode, exactly, refusing a `dest` that's already there.
fn open_pair(source: &Path, dest: &Path) -> Result<(File, File), Error> {
    let src = File::open(source).at(source)?;
    let mode = src.metadata().at(source)?.permissions().mode() & 0o7777;
    let dst = create(dest, mode).map_err(|e| match e {
        Error::Os(err, _) => Error::Os(err, source.to_owned()),
        other => other,
    })?;
    // The process umask masked the mode `create` gave, so a cached file keeps its own, as a hardlink of it would.
    dst.set_permissions(fs::Permissions::from_mode(mode)).at(source)?;
    Ok((src, dst))
}

/// Clone `source` to the new file `dest`, sharing its blocks: `FICLONE` on Linux, `clonefile` on macOS.
#[cfg(target_os = "linux")]
fn clone_file(source: &Path, dest: &Path) -> Result<(), Error> {
    use std::os::fd::AsRawFd;
    let (src, dst) = open_pair(source, dest)?;
    // libc's FICLONE is _IOW(0x94, 9, int) as each architecture encodes it: 0x40049409 on most, 0x80049409 on
    // PowerPC, MIPS and SPARC.
    if unsafe { libc::ioctl(dst.as_raw_fd(), libc::FICLONE, src.as_raw_fd()) } != 0 {
        let err = io::Error::last_os_error();
        drop(dst);
        let _ = fs::remove_file(dest);
        return Err(Error::Os(err, source.to_owned()));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn clone_file(source: &Path, dest: &Path) -> Result<(), Error> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;
    let src = CString::new(source.as_os_str().as_bytes()).map_err(|e| Error::Os(e.into(), source.to_owned()))?;
    let dst = CString::new(dest.as_os_str().as_bytes()).map_err(|e| Error::Os(e.into(), dest.to_owned()))?;
    if unsafe { libc::clonefile(src.as_ptr(), dst.as_ptr(), 0) } != 0 {
        return Err(Error::Os(io::Error::last_os_error(), source.to_owned()));
    }
    Ok(())
}

/// Elsewhere, such as the BSDs, there's no clone: `EOPNOTSUPP`, so `auto` moves on to a hardlink.
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn clone_file(source: &Path, _dest: &Path) -> Result<(), Error> {
    Err(Error::Os(
        io::Error::from_raw_os_error(libc::EOPNOTSUPP),
        source.to_owned(),
    ))
}

fn symlink_file(source: &Path, dest: &Path) -> Result<(), Error> {
    symlink(std::path::absolute(source).at(source)?, dest).at(source)
}

fn hardlink_file(source: &Path, dest: &Path) -> Result<(), Error> {
    fs::hard_link(source, dest).at(source)
}

/// Copy `source` to the new file `dest`, with `copy_file_range` where the platform has it, as `shutil` does.
fn copy_file(source: &Path, dest: &Path) -> Result<(), Error> {
    let (mut src, mut dst) = open_pair(source, dest)?;
    io::copy(&mut src, &mut dst).at(source)?;
    Ok(())
}
