// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! Keeping an artifact inside where it's extracted: entry names and symlink targets checked by name, and every path
//! checked on disk as it's made.

use std::collections::HashSet;
use std::ffi::OsString;
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, symlink};
use std::path::{Component, Path, PathBuf};

use crate::error::{Error, OsContext};

/// The most symlinks followed resolving one path, as Linux's `MAXSYMLINKS`.
const MAX_LINKS: usize = 40;

/// The mode of every directory an artifact is extracted to, masked by the process umask as the kernel applies it.
pub(crate) const DIR_MODE: u32 = 0o755;

/// Refuse a path that isn't plainly inside the collection: absolute, or with an empty, `.` or `..` part.
pub(crate) fn check_path(entry: &str) -> Result<(), Error> {
    if entry.starts_with('/') || entry.contains('\\') || entry.split('/').any(|p| matches!(p, "" | "." | "..")) {
        return Err(Error::extract("path", entry));
    }
    Ok(())
}

/// Refuse a symlink whose target, by name, leads outside the collection.
pub(crate) fn check_link(entry: &str, target: &str) -> Result<(), Error> {
    let mut depth: isize = entry.matches('/').count() as isize;
    let mut outside = target.starts_with('/');
    for part in target.split('/') {
        match part {
            "" | "." => {}
            ".." => depth -= 1,
            _ => depth += 1,
        }
        outside |= depth < 0;
    }
    if outside {
        Err(Error::Extract("link", entry.to_owned(), target.to_owned()))
    } else {
        Ok(())
    }
}

/// A new file at `path`, refusing one that's already there (a symlink included), with `mode` masked by the process
/// umask as the kernel applies it.
pub(crate) fn create(path: &Path, mode: u32) -> Result<File, Error> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(path)
        .at(path)
}

/// The directory `path`, made if it isn't one already (an earlier entry's parent is made with it).
pub(crate) fn make_dir(path: &Path) -> Result<(), Error> {
    match DirBuilder::new().mode(DIR_MODE).create(path) {
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists && path.is_dir() => Ok(()),
        result => result.at(path),
    }
}

/// Where an artifact is being extracted, and the symlinks made in it so far.
pub(crate) struct Target {
    dest: PathBuf,
    real: PathBuf,
    pub(crate) links: Vec<(String, PathBuf)>,
    /// Directories already checked whose path, under `dest`, has no symlink in it, so can't later lead anywhere else:
    /// `dest` itself, just made, to begin with.
    plain: HashSet<PathBuf>,
}

impl Target {
    pub(crate) fn new(dest: &Path) -> Result<Self, Error> {
        let dest = std::path::absolute(dest).at(dest)?;
        let real = realpath(&dest);
        Ok(Target {
            plain: HashSet::from([dest.clone()]),
            dest,
            real,
            links: Vec::new(),
        })
    }

    /// Where `entry` goes, its parent directories made and checked to resolve inside the collection on disk. Unless
    /// the parent's own parent is known to have no symlink, so at most the parent itself is made, and inside, it's
    /// checked before it's made too: through a chain of links leading out, a directory would be made outside.
    pub(crate) fn path(&mut self, entry: &str) -> Result<PathBuf, Error> {
        let path = self.dest.join(entry);
        let parent = path.parent().unwrap_or(&self.dest).to_owned();
        if !self.plain.contains(&parent) {
            if !parent.parent().is_some_and(|above| self.plain.contains(above)) {
                self.check_inside(&parent, entry)?;
            }
            DirBuilder::new()
                .recursive(true)
                .mode(DIR_MODE)
                .create(&parent)
                .at(&parent)?;
            if self.is_plain(&parent, entry)? {
                self.plain.insert(parent);
            }
        }
        Ok(path)
    }

    /// Whether the directory `parent`, just made for `entry`, has no symlink in its path under `dest`: one `lstat`
    /// when its own parent is known to have none, else resolved in full, and compared with where it would be under
    /// the resolved `dest`, which may itself be reached through a link.
    fn is_plain(&self, parent: &Path, entry: &str) -> Result<bool, Error> {
        if parent.parent().is_some_and(|above| self.plain.contains(above))
            && !fs::symlink_metadata(parent).at(parent)?.file_type().is_symlink()
        {
            return Ok(true);
        }
        let relative = parent.strip_prefix(&self.dest).unwrap_or(parent);
        Ok(self.check_inside(parent, entry)? == self.real.join(relative))
    }

    /// Fail unless `path` resolves inside the collection on disk; return where it resolves to.
    pub(crate) fn check_inside(&self, path: &Path, entry: &str) -> Result<PathBuf, Error> {
        let real = realpath(path);
        if !real.starts_with(&self.real) {
            return Err(Error::extract("escape", entry));
        }
        Ok(real)
    }

    pub(crate) fn symlink(&mut self, target: &str, path: &Path, entry: &str) -> Result<(), Error> {
        symlink(target, path).at(path)?;
        self.links.push((entry.to_owned(), path.to_owned()));
        Ok(())
    }
}

/// The absolute path `path` leads to, following every symlink, as Python's `os.path.realpath` does without
/// `strict`: a part that doesn't exist, or can't be read, is taken as it is.
fn realpath(path: &Path) -> PathBuf {
    let mut resolved = PathBuf::from("/");
    // Parts still to resolve, the next last.
    let mut pending: Vec<OsString> = path.components().rev().filter_map(part).collect();
    let mut followed = 0;
    while let Some(next) = pending.pop() {
        if next == ".." {
            resolved.pop();
            continue;
        }
        let candidate = resolved.join(&next);
        let target = match fs::symlink_metadata(&candidate) {
            Ok(meta) if meta.file_type().is_symlink() => fs::read_link(&candidate).ok(),
            _ => None,
        };
        match target {
            Some(target) if followed < MAX_LINKS => {
                followed += 1;
                if target.is_absolute() {
                    resolved = PathBuf::from("/");
                }
                pending.extend(target.components().rev().filter_map(part));
            }
            _ => resolved = candidate,
        }
    }
    resolved
}

fn part(component: Component<'_>) -> Option<OsString> {
    match component {
        Component::Normal(name) => Some(name.to_owned()),
        Component::ParentDir => Some("..".into()),
        Component::RootDir | Component::CurDir | Component::Prefix(_) => None,
    }
}
