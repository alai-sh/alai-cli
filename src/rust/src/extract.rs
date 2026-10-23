// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! Extracting a collection artifact: the tarball read in two passes, its `MANIFEST.json` and `FILES.json` checked, and
//! what they list written.

use std::cell::Cell;
use std::collections::{HashMap, HashSet};
use std::fs::{DirBuilder, File};
use std::io::{self, BufReader, Read, Write};
use std::os::unix::fs::DirBuilderExt;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use flate2::bufread::MultiGzDecoder;
use pyo3::prelude::*;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tar::{Archive, EntryType};

use crate::error::{Error, OsContext};
use crate::paths::{DIR_MODE, Target, check_link, check_path, create, make_dir};

/// How much of the tarball, and of each file, is read at once.
const BUFFER: usize = 256 * 1024;

/// How much may still be unpacked: every byte decompressed from the tarball, and every byte a hard link's copy or a
/// symlink's checksum reads back from disk, as a link costs the tarball only a header and could be made many times
/// over, each a copy or a read of the largest file.
struct Budget {
    limit: u64,
    left: Cell<Option<u64>>,
    over: Cell<bool>,
}

impl Budget {
    /// `limit` bytes; 0 for no limit.
    fn new(limit: u64) -> Rc<Self> {
        Rc::new(Budget {
            limit,
            left: Cell::new((limit > 0).then_some(limit)),
            over: Cell::new(false),
        })
    }

    /// Spend `n` bytes of it, failing (`too_large`) once it's gone.
    fn spend(&self, n: u64) -> Result<(), Error> {
        if let Some(left) = self.left.get() {
            if n > left {
                self.over.set(true);
                return Err(self.exceeded());
            }
            self.left.set(Some(left - n));
        }
        Ok(())
    }

    fn exceeded(&self) -> Error {
        Error::Extract("too_large", String::new(), self.limit.to_string())
    }
}

/// Turns an error reading a tarball into the one to raise.
struct Failure {
    tarball: PathBuf,
    budget: Rc<Budget>,
}

impl Failure {
    /// The filesystem's own error, the tarball unpacking to more than the limit, or a corrupt or truncated tarball or
    /// gzip stream.
    fn of(&self, err: io::Error) -> Error {
        if self.budget.over.get() {
            self.budget.exceeded()
        } else if err.raw_os_error().is_some() {
            Error::Os(err, self.tarball.clone())
        } else {
            Error::Extract("corrupt", String::new(), err.to_string())
        }
    }
}

/// A decompressed stream that fails once more than its budget has come out of it, so a small tarball can't unpack to
/// enough to fill the disk, or take hours to read through.
struct Capped<R> {
    inner: R,
    budget: Rc<Budget>,
}

impl<R: Read> Read for Capped<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let read = self.inner.read(buf)?;
        if self.budget.spend(read as u64).is_err() {
            return Err(io::Error::other("larger than the limit"));
        }
        Ok(read)
    }
}

type Tarball = Archive<Capped<MultiGzDecoder<BufReader<File>>>>;

/// The tarball at `tarball`, failing once it unpacks to more than `budget` has left, and how to report an error
/// reading it. The decoder reads the buffered file directly (`bufread`), rather than through a buffer of its own.
fn open_archive(tarball: &Path, budget: &Rc<Budget>) -> Result<(Tarball, Failure), Error> {
    let file = File::open(tarball).at(tarball)?;
    let stream = Capped {
        inner: MultiGzDecoder::new(BufReader::with_capacity(BUFFER, file)),
        budget: budget.clone(),
    };
    let fail = Failure {
        tarball: tarball.to_owned(),
        budget: budget.clone(),
    };
    Ok((Archive::new(stream), fail))
}

/// A member's name as Python's tarfile gives it, without a directory's trailing `/`; None if it isn't UTF-8, so
/// can't be one `FILES.json` names.
fn member_name<R: Read>(entry: &tar::Entry<R>) -> Option<String> {
    let raw = entry.path_bytes();
    let name = std::str::from_utf8(&raw).ok()?;
    Some(name.trim_end_matches('/').to_owned())
}

/// What a member is, refusing one that tar readers don't agree on, so alai sees the members tarfile and GNU tar
/// would: a name ending in `/` that isn't a directory (tarfile reads an old-style regular file named so as a
/// directory, but not after a long name, and drops the `/` from a pax name of any type), and a directory, link or
/// device with content, which they don't skip and this reader does, so another member could hide in it. A pax global
/// header is `check_global`'s.
fn check_member<R: Read>(entry: &tar::Entry<R>) -> Result<Kind, Error> {
    let header = entry.header();
    let entry_type = header.entry_type();
    let kind = Kind::of(entry_type);
    let raw = entry.path_bytes();
    let shown = String::from_utf8_lossy(&raw);
    if kind != Kind::Dir && raw.ends_with(b"/") {
        let detail = format!("{shown} ends in / but isn't a directory");
        return Err(Error::Extract("corrupt", String::new(), detail));
    }
    let what = match entry_type {
        EntryType::Directory => "directory",
        EntryType::Symlink => "symlink",
        EntryType::Link => "hard link",
        EntryType::Char | EntryType::Block | EntryType::Fifo => "device",
        _ => return Ok(kind),
    };
    let size = entry.size().max(header.entry_size().unwrap_or(0));
    if size > 0 {
        let shown = shown.trim_end_matches('/');
        let detail = format!("the {what} {shown} has {size} bytes of content, which tar readers don't agree on");
        return Err(Error::Extract("corrupt", String::new(), detail));
    }
    Ok(kind)
}

/// Refuse a pax global header that sets anything but `comment`: tarfile applies its keys to every later member, `path`
/// and `linkpath` included, and this reader ignores them, so the two would name, and link, the members differently.
/// `comment` describes the archive, not its members, and is all `git archive` writes (the commit it's of).
fn check_global<R: Read>(entry: &mut tar::Entry<R>, fail: &Failure) -> Result<(), Error> {
    let Some(records) = entry.pax_extensions().map_err(|e| fail.of(e))? else {
        return Ok(());
    };
    for record in records {
        let record = record.map_err(|e| fail.of(e))?;
        if record.key_bytes() != b"comment" {
            let key = String::from_utf8_lossy(record.key_bytes());
            let detail = format!("a pax global header sets {key}, which tar readers don't agree on");
            return Err(Error::Extract("corrupt", String::new(), detail));
        }
    }
    Ok(())
}

const MANIFEST: &str = "MANIFEST.json";
const FILES: &str = "FILES.json";

/// Extract the collection artifact `tarball` into the new directory `dest`, as `ansible-galaxy collection install`
/// does, and return the namespace, name and version its `MANIFEST.json` gives. With `expected`, those must be them.
///
/// Two passes, as a gzip stream can't be seeked and the last member of a name is the one that counts (`tar -r` appends
/// a replacement): the first reads only the headers, for where each name's last member is, and the content of the
/// last `MANIFEST.json` and `FILES.json`; then both are checked, and every entry `FILES.json` lists, before anything
/// is written; the second writes only those last members, from what the first recorded of each. Every path and
/// symlink must stay inside `dest`, by name and on disk. Files are `0644`, or `0755` if the tarball marks them
/// executable, and directories `0755`, both masked by the process umask as the kernel applies it. More than `limit`
/// bytes unpacked, in either pass, is `too_large`, counting what a hard link copies and a symlink's checksum reads;
/// 0 is no limit.
#[pyfunction]
#[pyo3(signature = (tarball, dest, limit, expected=None))]
pub(crate) fn extract_collection(
    py: Python<'_>,
    tarball: PathBuf,
    dest: PathBuf,
    limit: u64,
    expected: Option<(String, String, String)>,
) -> PyResult<(String, String, String)> {
    Ok(py.detach(|| extract_collection_inner(&tarball, &dest, limit, expected.as_ref()))?)
}

fn extract_collection_inner(
    tarball: &Path,
    dest: &Path,
    limit: u64,
    expected: Option<&(String, String, String)>,
) -> Result<(String, String, String), Error> {
    let (members, mut specials) = scan(tarball, limit)?;
    let collection = check_manifest(&special(&mut specials, MANIFEST)?, expected)?;
    let listed = parse_files(&special(&mut specials, FILES)?)?;
    check_listed(&listed, &members)?;
    write(tarball, dest, limit, &listed, &members)?;
    Ok(collection)
}

/// What a member is, as `FILES.json` and tarfile see it.
#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Dir,
    File,
    Symlink,
    Hardlink,
    Other,
}

impl Kind {
    fn of(entry_type: EntryType) -> Kind {
        match entry_type {
            EntryType::Regular => Kind::File,
            EntryType::Directory => Kind::Dir,
            EntryType::Symlink => Kind::Symlink,
            EntryType::Link => Kind::Hardlink,
            _ => Kind::Other,
        }
    }
}

/// The last member of a name, as the first pass finds it and the second writes it.
struct Member {
    index: usize,
    kind: Kind,
    /// A symlink's or hard link's target; None if it isn't UTF-8.
    link: Option<String>,
    /// Whether the tarball marks it executable by its owner.
    executable: bool,
}

impl Member {
    fn link(&self, name: &str) -> Result<&str, Error> {
        self.link.as_deref().ok_or_else(|| Error::extract("unreadable", name))
    }
}

/// One entry `FILES.json` lists.
struct Listed {
    name: String,
    dir: bool,
    sha256: Option<String>,
}

/// The first pass: each name's last member, and the content of the last `MANIFEST.json` and `FILES.json` (None for one
/// that isn't a regular file), every member checked as `check_member` does.
#[allow(clippy::type_complexity)]
fn scan(
    tarball: &Path,
    limit: u64,
) -> Result<(HashMap<String, Member>, HashMap<&'static str, Option<Vec<u8>>>), Error> {
    let mut members = HashMap::new();
    let mut specials = HashMap::new();
    let (mut archive, fail) = open_archive(tarball, &Budget::new(limit))?;
    for (index, entry) in archive.entries().map_err(|e| fail.of(e))?.enumerate() {
        let mut entry = entry.map_err(|e| fail.of(e))?;
        // Not a member, as tarfile reads it, so nothing FILES.json can list.
        if entry.header().entry_type() == EntryType::XGlobalHeader {
            check_global(&mut entry, &fail)?;
            continue;
        }
        let kind = check_member(&entry)?;
        let Some(name) = member_name(&entry) else {
            continue;
        };
        if let Some(special) = [MANIFEST, FILES].into_iter().find(|s| *s == name) {
            let data = if kind == Kind::File {
                // The header's size is the tarball's own claim, so only so much is set aside for it beforehand.
                let mut data = Vec::with_capacity(entry.size().min(BUFFER as u64) as usize);
                entry.read_to_end(&mut data).map_err(|e| fail.of(e))?;
                Some(data)
            } else {
                None
            };
            specials.insert(special, data);
        }
        let link = match kind {
            Kind::Symlink | Kind::Hardlink => entry
                .link_name_bytes()
                .and_then(|b| std::str::from_utf8(&b).ok().map(str::to_owned)),
            _ => None,
        };
        let executable = entry.header().mode().unwrap_or(0) & 0o100 != 0;
        members.insert(
            name,
            Member {
                index,
                kind,
                link,
                executable,
            },
        );
    }
    Ok((members, specials))
}

/// The content of the last `MANIFEST.json` or `FILES.json`: `missing` if there's none, `not_file` if it isn't a file.
fn special(specials: &mut HashMap<&'static str, Option<Vec<u8>>>, name: &str) -> Result<Vec<u8>, Error> {
    match specials.remove(name) {
        None => Err(Error::extract("missing", name)),
        Some(None) => Err(Error::extract("not_file", name)),
        Some(Some(data)) => Ok(data),
    }
}

fn parse_object(data: &[u8], name: &str) -> Result<serde_json::Map<String, Value>, Error> {
    match serde_json::from_slice(data) {
        Ok(Value::Object(obj)) => Ok(obj),
        Ok(_) => Err(Error::extract("not_object", name)),
        Err(e) => Err(Error::Extract("json", name.to_owned(), e.to_string())),
    }
}

/// The namespace, name and version `MANIFEST.json`'s `collection_info` gives: with `expected`, `manifest` unless
/// they're those, compared as text, as alai's `Version` compares, the message giving what it found as JSON (`null`
/// for a missing field); without, `manifest_fields` unless all three are strings, for the caller to check.
fn check_manifest(data: &[u8], expected: Option<&(String, String, String)>) -> Result<(String, String, String), Error> {
    let obj = parse_object(data, MANIFEST)?;
    let info = obj.get("collection_info").and_then(Value::as_object);
    let field = |key: &str| info.and_then(|i| i.get(key));
    let found = (field("namespace"), field("name"), field("version"));
    let text = (
        found.0.and_then(Value::as_str),
        found.1.and_then(Value::as_str),
        found.2.and_then(Value::as_str),
    );
    match expected {
        Some((namespace, name, version)) => {
            if text != (Some(namespace.as_str()), Some(name.as_str()), Some(version.as_str())) {
                let shown = format!("{}.{} {}", text_of(found.0), text_of(found.1), text_of(found.2));
                return Err(Error::Extract("manifest", String::new(), shown));
            }
            Ok((namespace.clone(), name.clone(), version.clone()))
        }
        None => match text {
            (Some(namespace), Some(name), Some(version)) => {
                Ok((namespace.to_owned(), name.to_owned(), version.to_owned()))
            }
            _ => Err(Error::extract("manifest_fields", MANIFEST)),
        },
    }
}

/// A field as a message shows it: a string as it is, anything else as JSON, `null` when missing.
fn text_of(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => "null".to_owned(),
    }
}

/// `FILES.json`'s entries, each a name, `file` or `dir`, and for a file a SHA-256: every name plainly inside the
/// collection, neither `MANIFEST.json` nor `FILES.json` (a build never lists them, and neither could be listed with a
/// checksum that matches), and one listed twice only if both agree. `.`, the collection itself, is left out.
fn parse_files(data: &[u8]) -> Result<Vec<Listed>, Error> {
    let obj = parse_object(data, FILES)?;
    let Some(entries) = obj.get("files").and_then(Value::as_array) else {
        return Err(Error::extract("files_list", FILES));
    };
    let mut listed: Vec<Listed> = Vec::with_capacity(entries.len());
    for entry in entries {
        let field = |key: &str| entry.as_object().and_then(|o| o.get(key)).and_then(Value::as_str);
        let (Some(name), Some(ftype @ ("file" | "dir"))) = (field("name"), field("ftype")) else {
            return Err(Error::Extract("files_entry", String::new(), entry.to_string()));
        };
        if name != "." {
            check_path(name)?;
        }
        let sha256 = field("chksum_sha256");
        if ftype == "file" && sha256.is_none() {
            return Err(Error::extract("files_checksum", name));
        }
        if name == MANIFEST || name == FILES {
            return Err(Error::extract("files_special", name));
        }
        listed.push(Listed {
            name: name.to_owned(),
            dir: ftype == "dir",
            sha256: sha256.map(str::to_owned),
        });
    }
    // The same entry twice is the same entry; listed twice differently, one of them can't match the artifact.
    let mut seen: HashMap<&str, (bool, Option<&str>)> = HashMap::new();
    let mut unique = HashSet::new();
    for (i, item) in listed.iter().enumerate() {
        let key = (item.dir, item.sha256.as_deref());
        match seen.get(item.name.as_str()) {
            Some(first) if *first != key => return Err(Error::extract("files_twice", &item.name)),
            Some(_) => {}
            None => {
                seen.insert(&item.name, key);
                unique.insert(i);
            }
        }
    }
    let mut kept: Vec<Listed> = listed
        .into_iter()
        .enumerate()
        .filter(|(i, _)| unique.contains(i))
        .map(|(_, item)| item)
        .collect();
    kept.retain(|item| item.name != ".");
    Ok(kept)
}

/// Before anything is written, in `FILES.json`'s order: each entry's last member is there, of the type listed, and a
/// symlink's target stays inside the collection by name.
fn check_listed(listed: &[Listed], members: &HashMap<String, Member>) -> Result<(), Error> {
    for item in listed {
        let name = item.name.as_str();
        let Some(member) = members.get(name) else {
            return Err(Error::extract("missing", name));
        };
        match (item.dir, member.kind) {
            (true, Kind::Dir) | (false, Kind::File | Kind::Hardlink) => {}
            (_, Kind::Symlink) => check_link(name, member.link(name)?)?,
            (true, _) => return Err(Error::extract("not_dir", name)),
            (false, _) => return Err(Error::extract("not_file", name)),
        }
    }
    Ok(())
}

/// A member the second pass writes: its name, whether `FILES.json` lists it as a directory, its checksum, and what
/// the first pass recorded of it.
#[derive(Clone, Copy)]
struct Todo<'a> {
    name: &'a str,
    dir: bool,
    sha256: Option<&'a str>,
    member: &'a Member,
}

/// A symlink or hard link whose checksum, or content, waits until every member is extracted, as what it points to can
/// come after it.
enum Deferred<'a> {
    Symlink {
        entry: &'a str,
        path: PathBuf,
        sha256: Option<&'a str>,
    },
    Hardlink {
        entry: &'a str,
        path: PathBuf,
        target: &'a str,
        mode: u32,
        sha256: Option<&'a str>,
    },
}

/// The second pass: write the last member of each listed name, and of `MANIFEST.json` and `FILES.json`, into the new
/// directory `dest`, each file checked against its SHA-256 as it's written. What each member is, and where a link
/// leads, is as the first pass recorded and checked it; only a file's content is taken from this pass.
fn write(
    tarball: &Path,
    dest: &Path,
    limit: u64,
    listed: &[Listed],
    members: &HashMap<String, Member>,
) -> Result<(), Error> {
    let mut todo: HashMap<usize, Todo> = HashMap::with_capacity(listed.len() + 2);
    for item in listed {
        let member = &members[&item.name];
        let sha256 = item.sha256.as_deref();
        let (name, dir) = (item.name.as_str(), item.dir);
        todo.insert(
            member.index,
            Todo {
                name,
                dir,
                sha256,
                member,
            },
        );
    }
    for name in [MANIFEST, FILES] {
        let member = &members[name];
        let (dir, sha256) = (false, None);
        todo.insert(
            member.index,
            Todo {
                name,
                dir,
                sha256,
                member,
            },
        );
    }
    let names: HashSet<&str> = todo.values().map(|item| item.name).collect();

    DirBuilder::new().mode(DIR_MODE).create(dest).at(dest)?;
    let mut out = Target::new(dest)?;
    let mut deferred: Vec<Deferred> = Vec::new();
    let mut buffer = vec![0u8; BUFFER];

    let budget = Budget::new(limit);
    let (mut archive, fail) = open_archive(tarball, &budget)?;
    for (index, entry) in archive.entries().map_err(|e| fail.of(e))?.enumerate() {
        let Some(&Todo {
            name,
            dir,
            sha256,
            member,
        }) = todo.get(&index)
        else {
            continue;
        };
        let mut entry = entry.map_err(|e| fail.of(e))?;
        let mode = if member.executable { 0o755 } else { 0o644 };
        match member.kind {
            Kind::Dir => {
                let path = out.path(name)?;
                make_dir(&path)?;
            }
            Kind::File => {
                let path = out.path(name)?;
                let mut written = create(&path, mode)?;
                let mut digest = Sha256::new();
                loop {
                    let read = entry.read(&mut buffer).map_err(|e| fail.of(e))?;
                    if read == 0 {
                        break;
                    }
                    digest.update(&buffer[..read]);
                    written.write_all(&buffer[..read]).at(&path)?;
                }
                check_sha256(digest, sha256, name)?;
            }
            Kind::Symlink => {
                let target = member.link(name)?;
                let path = out.path(name)?;
                out.symlink(target, &path, name)?;
                if !dir {
                    deferred.push(Deferred::Symlink {
                        entry: name,
                        path,
                        sha256,
                    });
                }
            }
            Kind::Hardlink => {
                let target = member.link(name)?;
                let path = out.path(name)?;
                deferred.push(Deferred::Hardlink {
                    entry: name,
                    path,
                    target,
                    mode,
                    sha256,
                });
            }
            // check_listed refused these before anything was written.
            Kind::Other => return Err(Error::extract(if dir { "not_dir" } else { "not_file" }, name)),
        }
    }

    // A link made later can change where an earlier one leads, so each is checked again once all exist, before
    // anything is read through one.
    for (entry, path) in &out.links {
        out.check_inside(path, entry)?;
    }
    for item in deferred {
        match item {
            // A symlink's checksum in FILES.json is of the file it points to, as ansible-galaxy checks it.
            Deferred::Symlink { entry, path, sha256 } => {
                let Some(mut file) = open_regular(&path) else {
                    return Err(Error::extract("unreadable", entry));
                };
                let digest = read_hashed(&mut file, &path, None, &mut buffer, &budget)?;
                check_sha256(digest, sha256, entry)?;
            }
            // A hard link is written as a copy of what it links to, as tarfile extracts it: a listed member, named
            // from the top of the tarball.
            Deferred::Hardlink {
                entry,
                path,
                target,
                mode,
                sha256,
            } => {
                if check_path(target).is_err() || !names.contains(target) {
                    return Err(Error::extract("unreadable", entry));
                }
                let source = dest.join(target);
                out.check_inside(&source, entry)?;
                let Some(mut file) = open_regular(&source) else {
                    return Err(Error::extract("unreadable", entry));
                };
                let mut written = create(&path, mode)?;
                let digest = read_hashed(&mut file, &source, Some((&mut written, &path)), &mut buffer, &budget)?;
                check_sha256(digest, sha256, entry)?;
            }
        }
    }
    Ok(())
}

fn open_regular(path: &Path) -> Option<File> {
    let file = File::open(path).ok()?;
    file.metadata().ok()?.is_file().then_some(file)
}

/// Read `file`, at `source`, through, hashing it and writing it to `out` if given, each byte spent from `budget`: it's
/// what a link in the tarball copies or checks, so it counts as unpacked.
fn read_hashed(
    file: &mut File,
    source: &Path,
    mut out: Option<(&mut File, &Path)>,
    buffer: &mut [u8],
    budget: &Budget,
) -> Result<Sha256, Error> {
    let mut digest = Sha256::new();
    loop {
        let read = file.read(buffer).at(source)?;
        if read == 0 {
            return Ok(digest);
        }
        budget.spend(read as u64)?;
        digest.update(&buffer[..read]);
        if let Some((written, path)) = out.as_mut() {
            written.write_all(&buffer[..read]).at(path)?;
        }
    }
}

fn check_sha256(digest: Sha256, expected: Option<&str>, entry: &str) -> Result<(), Error> {
    let Some(expected) = expected else {
        return Ok(());
    };
    let actual: String = digest.finalize().iter().map(|b| format!("{b:02x}")).collect();
    if actual == expected {
        Ok(())
    } else {
        Err(Error::extract("checksum", entry))
    }
}
