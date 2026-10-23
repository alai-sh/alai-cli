// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! Choosing one version of every collection: PubGrub over a table Python builds, every version with its dependencies
//! and, for versions a rule leaves out, why. Python has already fetched the index files, applied overrides and
//! decided which versions can't be used (yanked, unreadable, needing a newer ansible-core); this chooses, or proves
//! there's no choice and says why in uv's words. The design is in `design/resolver.md`.

use std::cmp::Reverse;
use std::collections::{HashMap, HashSet};
use std::convert::Infallible;
use std::fmt;
use std::ops::Bound;
use std::sync::Arc;

use pubgrub::{
    DefaultStringReporter, Dependencies, DependencyProvider, DerivationTree, Derived, External, Map,
    PackageResolutionStatistics, PubGrubError, Ranges, ReportFormatter, Reporter, Term,
};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

use crate::error::NoSolution;

/// A pre-release identifier as Python's `Version._key` holds it: `(0, number)` or `(1, text)`, so numbers sort first.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, FromPyObject)]
pub(crate) enum PreId {
    Num(u64),
    Str(String),
}

/// `Version._key` from `_semver.py`: major, minor, patch, 1 for a release or 0 for a pre-release, then the pre-release
/// identifiers. Comparing it is comparing SemVer precedence.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, FromPyObject)]
pub(crate) struct Key(u64, u64, u64, u8, Vec<(u8, PreId)>);

impl fmt::Display for Key {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}.{}", self.0, self.1, self.2)?;
        if self.3 == 0 {
            for (i, (_, id)) in self.4.iter().enumerate() {
                f.write_str(if i == 0 { "-" } else { "." })?;
                match id {
                    PreId::Num(n) => write!(f, "{n}")?,
                    PreId::Str(s) => f.write_str(s)?,
                }
            }
        }
        Ok(())
    }
}

/// The lowest and highest texts a version with a given key can have, for a requirement's bounds: `>=1.0.0` is every
/// version from the first with that precedence, `<=1.0.0` every one up to the last, as a requirement compares
/// precedence alone and a version's text only breaks ties between equal precedence (build metadata).
const LOWEST: &str = "";
const HIGHEST: &str = "\u{10FFFF}";

/// A version ordered as Python orders them: by precedence, then by text.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Version {
    key: Key,
    text: String,
}

impl Version {
    fn bound(key: &Key, text: &str) -> Self {
        Version {
            key: key.clone(),
            text: text.to_owned(),
        }
    }
}

impl fmt::Display for Version {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.text == LOWEST || self.text == HIGHEST {
            // A requirement's bound: the precedence it was written with.
            self.key.fmt(f)
        } else {
            f.write_str(&self.text)
        }
    }
}

type Set = Ranges<Version>;
pub(crate) type Clause = (String, Key);

/// The versions a requirement's clauses accept, all of which must hold; none is every version.
fn set_of(clauses: &[Clause]) -> Set {
    let mut set = Ranges::full();
    for (op, key) in clauses {
        let lowest = Version::bound(key, LOWEST);
        let highest = Version::bound(key, HIGHEST);
        let clause: Set = match op.as_str() {
            ">=" => Ranges::higher_than(lowest),
            ">" => Ranges::strictly_higher_than(highest),
            "<" => Ranges::strictly_lower_than(lowest),
            "<=" => Ranges::lower_than(highest),
            "==" => Ranges::from_range_bounds(lowest..=highest),
            "!=" => Ranges::from_range_bounds(lowest..=highest).complement(),
            // Python validated the operators; anything else accepts everything rather than failing here.
            _ => Ranges::full(),
        };
        set = set.intersection(&clause);
    }
    set
}

#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum Package {
    /// What the requirements came from: the project, or the command line.
    Root,
    /// A collection group, which the root depends on, so a failure says which group asked.
    Group(String),
    Collection(String),
}

impl fmt::Display for Package {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Package::Root => f.write_str("the project"),
            Package::Group(name) => write!(f, "group '{name}'"),
            Package::Collection(name) => f.write_str(name),
        }
    }
}

struct VersionData {
    version: Version,
    prerelease: bool,
    /// Why this version can't be chosen, as a predicate (`is yanked`), or None if it can. Choosing it anyway and
    /// then reporting it unavailable is what puts the reason in a failure's explanation, as uv does.
    unavailable: Option<String>,
    deps: Vec<(Package, Set)>,
}

struct PackageData {
    /// Ascending.
    versions: Vec<VersionData>,
    /// The locked or installed version, tried first while it fits.
    preferred: Option<Version>,
}

struct Table {
    packages: HashMap<Package, PackageData>,
    /// Collections the root or a group names directly, decided before dependencies, as resolvelib did.
    direct: HashSet<Package>,
    /// Why a collection has no versions at all: no configured index has it, or its index's listing is stale.
    missing: HashMap<String, String>,
    /// How a collection read from a directory, tarball or commit is described: `from ../col`, or `the project's
    /// own collection`.
    sources: HashMap<String, String>,
    overrides: HashSet<String>,
    /// `the project depends on`, or `you require` for the command line.
    root_depends: String,
    /// `the project's requirements are unsatisfiable`.
    root_unsat: String,
}

impl Table {
    /// Whether two sets hold the same versions of `package`, of those that exist: two ways of writing one range.
    fn agree(&self, package: &Package, a: &Set, b: &Set) -> bool {
        self.versions(package).all(|v| a.contains(v) == b.contains(v))
    }

    fn versions(&self, package: &Package) -> impl Iterator<Item = &Version> {
        self.packages
            .get(package)
            .into_iter()
            .flat_map(|p| p.versions.iter().map(|v| &v.version))
    }
}

impl DependencyProvider for Table {
    type P = Package;
    type V = Version;
    type VS = Set;
    type M = String;
    type Err = Infallible;
    /// Highest first: conflicts so far, then an exact pin, then a direct requirement, then the fewest candidates,
    /// then the name, so a resolution is repeatable. A package with no candidates comes first of all, so the
    /// conflict is found at once.
    type Priority = (u32, u8, Reverse<usize>, Reverse<String>);

    fn prioritize(&self, package: &Package, range: &Set, stats: &PackageResolutionStatistics) -> Self::Priority {
        let candidates = self.versions(package).filter(|v| range.contains(v)).count();
        let name = Reverse(package.to_string());
        if candidates == 0 {
            return (u32::MAX, 0, Reverse(0), name);
        }
        let kind = if exact(range).is_some() {
            2
        } else if self.direct.contains(package) {
            1
        } else {
            0
        };
        (stats.conflict_count(), kind, Reverse(candidates), name)
    }

    fn choose_version(&self, package: &Package, range: &Set) -> Result<Option<Version>, Infallible> {
        let Some(data) = self.packages.get(package) else {
            return Ok(None);
        };
        if let Some(preferred) = &data.preferred
            && range.contains(preferred)
        {
            return Ok(Some(preferred.clone()));
        }
        // The highest version that fits, but every stable version before any pre-release.
        let stable = data
            .versions
            .iter()
            .rev()
            .find(|v| !v.prerelease && range.contains(&v.version));
        let chosen = stable.or_else(|| data.versions.iter().rev().find(|v| range.contains(&v.version)));
        Ok(chosen.map(|v| v.version.clone()))
    }

    fn get_dependencies(
        &self,
        package: &Package,
        version: &Version,
    ) -> Result<Dependencies<Package, Set, String>, Infallible> {
        let found = self
            .packages
            .get(package)
            .and_then(|data| data.versions.iter().find(|v| v.version == *version));
        Ok(match found {
            Some(v) => match &v.unavailable {
                Some(reason) => Dependencies::Unavailable(reason.clone()),
                None => Dependencies::Available(v.deps.iter().cloned().collect()),
            },
            None => Dependencies::Unavailable("isn't in the table".to_owned()),
        })
    }
}

/// A set as a requirement reads: `>=1.0.0,<2.0.0`, `==1.0.0`, `!=1.0.0`, or nothing for every version; unions with
/// ` | `.
fn bounds_text(set: &Set) -> String {
    let mut segments = set.iter();
    if let (Some((Bound::Unbounded, Bound::Excluded(a))), Some((Bound::Excluded(b), Bound::Unbounded)), None) =
        (segments.next(), segments.next(), segments.next())
        && a.key == b.key
    {
        return format!("!={a}");
    }
    let mut parts = Vec::new();
    for (lower, upper) in set.iter() {
        let mut clauses = Vec::new();
        match (lower, upper) {
            (Bound::Included(a), Bound::Included(b)) if a.key == b.key => clauses.push(format!("=={a}")),
            (lower, upper) => {
                match lower {
                    Bound::Included(v) => clauses.push(format!(">={v}")),
                    Bound::Excluded(v) => clauses.push(format!(">{v}")),
                    Bound::Unbounded => {}
                }
                match upper {
                    Bound::Included(v) => clauses.push(format!("<={v}")),
                    Bound::Excluded(v) => clauses.push(format!("<{v}")),
                    Bound::Unbounded => {}
                }
            }
        }
        parts.push(clauses.join(","));
    }
    parts.join(" | ")
}

/// The one version a set names, `==1.0.0`: a single segment from the first to the last text of one precedence, as
/// a requirement's `==` builds it, or a single version, as a decision does. `Ranges::as_singleton` knows only the
/// second.
fn exact(set: &Set) -> Option<&Version> {
    let mut segments = set.iter();
    match (segments.next(), segments.next()) {
        (Some((Bound::Included(a), Bound::Included(b))), None) if a.key == b.key => Some(a),
        _ => None,
    }
}

/// The versions that exist within `range`, as uv shows them but cut to what there is: each part's bounds are moved
/// in to the first and last version it holds, so `<=1.2.0` rather than `<1.3.0` of a collection with no 1.3.0; a
/// part with no version is dropped; and an open bound stays open, except that a part open at both ends reads
/// `<=last`.
fn availability(range: &Set, versions: &[&Version]) -> Set {
    let mut out = Ranges::empty();
    for (lower, upper) in range.iter() {
        let part: Set = Ranges::from_range_bounds((lower.clone(), upper.clone()));
        let mut inside = versions.iter().filter(|v| part.contains(v));
        let Some(first) = inside.next() else {
            continue;
        };
        let last = inside.next_back().unwrap_or(first);
        let lower = match lower {
            Bound::Unbounded => Bound::Unbounded,
            _ => Bound::Included((*first).clone()),
        };
        let upper = match (&lower, upper) {
            (Bound::Included(_), Bound::Unbounded) => Bound::Unbounded,
            _ => Bound::Included((*last).clone()),
        };
        out = out.union(&Ranges::from_range_bounds((lower, upper)));
    }
    out
}

type Tree = DerivationTree<Package, Set, String>;
type Terms = Map<Package, Term<Set>>;

fn derived(terms: Terms, shared_id: Option<usize>, cause1: Tree, cause2: Tree) -> Tree {
    DerivationTree::Derived(Derived {
        terms,
        shared_id,
        cause1: Arc::new(cause1),
        cause2: Arc::new(cause2),
    })
}

/// Rebuild a tree bottom up, `f` deciding what each derivation becomes from its already rebuilt causes.
fn map_tree(tree: Tree, f: &impl Fn(Terms, Option<usize>, Tree, Tree) -> Tree) -> Tree {
    match tree {
        DerivationTree::External(external) => DerivationTree::External(external),
        DerivationTree::Derived(d) => {
            let cause1 = map_tree(Arc::unwrap_or_clone(d.cause1), f);
            let cause2 = map_tree(Arc::unwrap_or_clone(d.cause2), f);
            f(d.terms, d.shared_id, cause1, cause2)
        }
    }
}

/// What a cause states, as terms: a derivation's own, or an external fact's.
fn stated(tree: &Tree) -> Option<Terms> {
    Some(match tree {
        DerivationTree::Derived(d) => d.terms.clone(),
        DerivationTree::External(External::FromDependencyOf(p, set, d, needed)) => [
            (p.clone(), Term::Positive(set.clone())),
            (d.clone(), Term::Negative(needed.clone())),
        ]
        .into_iter()
        .collect(),
        DerivationTree::External(External::Custom(p, set, _) | External::NoVersions(p, set)) => {
            [(p.clone(), Term::Positive(set.clone()))].into_iter().collect()
        }
        DerivationTree::External(External::NotRoot(_, _)) => return None,
    })
}

/// A `there is no version of A ...` beside a cause that, on the versions there are, already states what's
/// concluded says nothing: it only cut versions that don't exist out of a range. Where it does change the
/// conclusion, as when a dependency has no versions at all, it stays. uv keeps the fact when it leaves a single
/// version, for `only a.a==1.0.0 is available`; here `a.a==1.0.0 depends on` says as much.
fn drop_redundant_no_versions(terms: &Terms, cause1: &Tree, cause2: &Tree, table: &Table) -> Option<Tree> {
    let same = |a: &Terms, b: &Terms| {
        a.len() == b.len()
            && a.iter().all(|(p, t)| match (t, b.get(p)) {
                (Term::Positive(x), Some(Term::Positive(y))) | (Term::Negative(x), Some(Term::Negative(y))) => {
                    table.agree(p, x, y)
                }
                _ => false,
            })
    };
    for (no_versions, other) in [(cause1, cause2), (cause2, cause1)] {
        if let DerivationTree::External(External::NoVersions(_, _)) = no_versions
            && let Some(states) = stated(other)
            && same(terms, &states)
        {
            return Some(other.clone());
        }
    }
    None
}

/// `A>=1 depends on B`, derived from `A==1.0.0 | ==2.0.0 depends on B` and `there is no version of A>1,<2 | >2`,
/// needs no deriving: versions that don't exist can be said to depend on anything, so the conclusion stands on its
/// own, as pubgrub's own `collapse_no_versions` has it. The same when the other cause is `A==3.0.0 depends on B`,
/// the same dependency of other versions: the conclusion is the union. Only the depending side is merged, and only
/// when every cause asks for the same versions of B; a dependency with no versions is the point and stays, and two
/// versions asking for different things, as uv keeps them, say more than their union.
fn merge_depender_gaps(terms: &Terms, cause1: &Tree, cause2: &Tree, table: &Table) -> Option<Tree> {
    if terms.len() != 2 {
        return None;
    }
    let mut depender: Option<(&Package, &Package, &Set)> = None;
    for cause in [cause1, cause2] {
        match cause {
            DerivationTree::External(External::FromDependencyOf(p, _, d, needed)) => match depender {
                Some((dp, dd, ds)) if dp == p && dd == d && table.agree(d, ds, needed) => {}
                Some(_) => return None,
                None => depender = Some((p, d, needed)),
            },
            DerivationTree::External(External::NoVersions(_, _)) => {}
            _ => return None,
        }
    }
    let (p, d, _) = depender?;
    for cause in [cause1, cause2] {
        if let DerivationTree::External(External::NoVersions(np, _)) = cause
            && np != p
        {
            return None;
        }
    }
    let (Some(Term::Positive(set)), Some(Term::Negative(needed))) = (terms.get(p), terms.get(d)) else {
        return None;
    };
    Some(DerivationTree::External(External::FromDependencyOf(
        p.clone(),
        set.clone(),
        d.clone(),
        needed.clone(),
    )))
}

/// Two versions left out for one reason (`a.a==1.0.0 is yanked` and `a.a==1.1.0 is yanked`), concluding that
/// neither can be used, are one fact about both; a run of them, merged from the bottom up, is one fact about every
/// version the reason left out, however many. uv's `collapse_unavailable_versions`.
fn merge_unavailable(terms: &Terms, cause1: &Tree, cause2: &Tree) -> Option<Tree> {
    let mut iter = terms.iter();
    let ((package, Term::Positive(set)), None) = (iter.next()?, iter.next()) else {
        return None;
    };
    let (
        DerivationTree::External(External::Custom(p1, _, reason1)),
        DerivationTree::External(External::Custom(p2, _, reason2)),
    ) = (cause1, cause2)
    else {
        return None;
    };
    (p1 == package && p2 == package && reason1 == reason2)
        .then(|| DerivationTree::External(External::Custom(package.clone(), set.clone(), reason1.clone())))
}

/// Prune a derivation of what it needn't say, so the report reads as a few sentences: one pass, bottom up, so each
/// rule sees causes the others have already reduced. Sets are compared on the versions that exist, so nothing
/// need be simplified first; the formatter simplifies what it prints. uv has more rules, each for one shape
/// (`error.rs` in uv-resolver); these three were what changed any output on alai's tests and on the `ansible`
/// package's collections.
fn prune(tree: Tree, table: &Table) -> Tree {
    map_tree(tree, &|terms, id, cause1, cause2| {
        if let Some(merged) = merge_unavailable(&terms, &cause1, &cause2) {
            return merged;
        }
        if let Some(merged) = merge_depender_gaps(&terms, &cause1, &cause2, table) {
            return merged;
        }
        if let Some(kept) = drop_redundant_no_versions(&terms, &cause1, &cause2, table) {
            return kept;
        }
        derived(terms, id, cause1, cause2)
    })
}

/// Writes a derivation tree in uv's words, each set simplified to the versions that exist, as uv does: `>=11.0.0`
/// rather than every gap between the versions it covers.
struct Formatter<'a> {
    table: &'a Table,
}

impl Formatter<'_> {
    /// A set as a requirement reads, simplified to the versions that exist: `a.a>=1.0.0`.
    fn set(&self, package: &Package, set: &Set) -> String {
        match package {
            Package::Root | Package::Group(_) => package.to_string(),
            Package::Collection(_) => format!("{package}{}", bounds_text(&set.simplify(self.table.versions(package)))),
        }
    }

    /// A set as the thing talked about: the one version it covers, named, when it covers only one (`a.a==2.0.0
    /// depends on`, `a.a==2.0.0 cannot be used`), else as `set`. Not for a requirement, which reads as written.
    fn subject(&self, package: &Package, set: &Set) -> String {
        if let Package::Collection(_) = package {
            let mut inside = self.table.versions(package).filter(|v| set.contains(v));
            if let (Some(only), None) = (inside.next(), inside.next()) {
                return format!("{package}=={only}");
            }
        }
        self.set(package, set)
    }

    fn depends(&self, package: &Package, set: &Set, dependency: &Package, needed: &Set) -> String {
        let subject = match package {
            Package::Root => self.table.root_depends.clone(),
            _ => format!("{} depends on", self.subject(package, set)),
        };
        if let (Package::Root, Package::Group(group)) = (package, dependency) {
            return format!("the project has group '{group}'");
        }
        let mut object = self.set(dependency, needed);
        if let Package::Collection(name) = dependency
            && self.table.overrides.contains(name)
        {
            object.push_str(" ([project.overrides])");
        }
        format!("{subject} {object}")
    }

    fn term(&self, package: &Package, term: &Term<Set>) -> String {
        match term {
            Term::Positive(set) => self.subject(package, set),
            Term::Negative(set) => format!("not {}", self.set(package, set)),
        }
    }
}

impl ReportFormatter<Package, Set, String> for Formatter<'_> {
    type Output = String;

    fn format_external(&self, external: &External<Package, Set, String>) -> String {
        match external {
            External::NotRoot(package, version) => format!("we are solving dependencies of {package} {version}"),
            External::NoVersions(package, set) => {
                let Package::Collection(name) = package else {
                    return format!("there are no versions of {package}");
                };
                if let Some(reason) = self.table.missing.get(name) {
                    return reason.clone();
                }
                if *set == Ranges::full() {
                    return format!("there are no versions of {package}");
                }
                if let Some(version) = exact(set) {
                    return format!("there is no version of {package}=={version}");
                }
                let versions: Vec<&Version> = self.table.versions(package).collect();
                let available = availability(&set.complement(), &versions);
                let source = match self.table.sources.get(name) {
                    Some(source) => format!(", {source},"),
                    None => String::new(),
                };
                if available.is_empty() {
                    return format!("there are no versions of {package}");
                }
                // A few versions are named; more are given as ranges, as uv gives them.
                let listed: Vec<String> = versions
                    .iter()
                    .filter(|v| available.contains(v))
                    .map(|v| format!("{package}=={v}"))
                    .collect();
                match listed.as_slice() {
                    [only] => format!("only {only}{source} is available"),
                    [.., last] if listed.len() <= 3 => {
                        format!(
                            "only {} and {last}{source} are available",
                            listed[..listed.len() - 1].join(", ")
                        )
                    }
                    _ => {
                        let parts: Vec<String> = available
                            .iter()
                            .map(|(lower, upper)| {
                                let part: Set = Ranges::from_range_bounds((lower.clone(), upper.clone()));
                                format!("{package}{}", bounds_text(&part))
                            })
                            .collect();
                        match parts.as_slice() {
                            [only] => format!("only {only}{source} is available"),
                            _ => format!(
                                "only the following versions of {package}{source} are available: {}",
                                parts.join(", ")
                            ),
                        }
                    }
                }
            }
            External::FromDependencyOf(package, set, dependency, needed) => {
                self.depends(package, set, dependency, needed)
            }
            External::Custom(package, set, reason) => {
                let several = self
                    .table
                    .versions(package)
                    .filter(|v| set.contains(v))
                    .nth(1)
                    .is_some();
                let what = self.subject(package, set);
                if several {
                    format!("every version of {what} {reason}")
                } else {
                    format!("{what} {reason}")
                }
            }
        }
    }

    fn format_terms(&self, terms: &Map<Package, Term<Set>>) -> String {
        // "Not in a set with no versions" always holds, so it says nothing.
        let vacuous = |p: &Package, t: &Term<Set>| matches!(t, Term::Negative(set) if !self.table.versions(p).any(|v| set.contains(v)));
        let mut all: Vec<_> = terms.iter().collect();
        all.sort_by(|a, b| a.0.cmp(b.0));
        let kept: Vec<_> = all.iter().copied().filter(|(p, t)| !vacuous(p, t)).collect();
        let terms = if kept.is_empty() { all } else { kept };
        match terms.as_slice() {
            [] => "version solving failed".to_owned(),
            [(Package::Root, Term::Positive(_))] => self.table.root_unsat.clone(),
            [(package, Term::Positive(set))] => format!("{} cannot be used", self.subject(package, set)),
            [(package, Term::Negative(set))] => format!("{} must be used", self.set(package, set)),
            [(p1, Term::Positive(s1)), (p2, Term::Negative(s2))] => self.depends(p1, s1, p2, s2),
            [(p1, Term::Negative(s1)), (p2, Term::Positive(s2))] => self.depends(p2, s2, p1, s1),
            // The root and every group are always selected, so what's incompatible with one can't be used at all.
            [
                (Package::Root | Package::Group(_), Term::Positive(_)),
                (package, Term::Positive(set)),
            ]
            | [
                (package, Term::Positive(set)),
                (Package::Root | Package::Group(_), Term::Positive(_)),
            ] => {
                format!("{} cannot be used", self.subject(package, set))
            }
            slice => {
                let texts: Vec<String> = slice.iter().map(|(p, t)| self.term(p, t)).collect();
                format!("{} are incompatible", texts.join(" and "))
            }
        }
    }

    fn explain_both_external(
        &self,
        external1: &External<Package, Set, String>,
        external2: &External<Package, Set, String>,
        current_terms: &Map<Package, Term<Set>>,
    ) -> String {
        format!(
            "Because {} and {}, we can conclude that {}.",
            self.format_external(external1),
            self.format_external(external2),
            self.format_terms(current_terms)
        )
    }

    fn explain_both_ref(
        &self,
        ref_id1: usize,
        derived1: &Derived<Package, Set, String>,
        ref_id2: usize,
        derived2: &Derived<Package, Set, String>,
        current_terms: &Map<Package, Term<Set>>,
    ) -> String {
        format!(
            "Because we know from ({ref_id1}) that {} and we know from ({ref_id2}) that {}, we can conclude that {}.",
            self.format_terms(&derived1.terms),
            self.format_terms(&derived2.terms),
            self.format_terms(current_terms)
        )
    }

    fn explain_ref_and_external(
        &self,
        ref_id: usize,
        derived: &Derived<Package, Set, String>,
        external: &External<Package, Set, String>,
        current_terms: &Map<Package, Term<Set>>,
    ) -> String {
        format!(
            "Because we know from ({ref_id}) that {} and {}, we can conclude that {}.",
            self.format_terms(&derived.terms),
            self.format_external(external),
            self.format_terms(current_terms)
        )
    }

    fn and_explain_external(
        &self,
        external: &External<Package, Set, String>,
        current_terms: &Map<Package, Term<Set>>,
    ) -> String {
        format!(
            "And because {}, we can conclude that {}.",
            self.format_external(external),
            self.format_terms(current_terms)
        )
    }

    fn and_explain_ref(
        &self,
        ref_id: usize,
        derived: &Derived<Package, Set, String>,
        current_terms: &Map<Package, Term<Set>>,
    ) -> String {
        format!(
            "And because we know from ({ref_id}) that {}, we can conclude that {}.",
            self.format_terms(&derived.terms),
            self.format_terms(current_terms)
        )
    }

    fn and_explain_prior_and_external(
        &self,
        prior_external: &External<Package, Set, String>,
        external: &External<Package, Set, String>,
        current_terms: &Map<Package, Term<Set>>,
    ) -> String {
        format!(
            "And because {} and {}, we can conclude that {}.",
            self.format_external(prior_external),
            self.format_external(external),
            self.format_terms(current_terms)
        )
    }
}

pub(crate) type Requirement = Vec<Clause>;
/// A version as Python passes it: text, key, whether it's a pre-release, why it can't be used or None, and its
/// dependencies.
pub(crate) type VersionEntry = (String, Key, bool, Option<String>, Vec<(String, Requirement)>);

fn dependencies(deps: &[(String, Requirement)]) -> Vec<(Package, Set)> {
    deps.iter()
        .map(|(name, clauses)| (Package::Collection(name.clone()), set_of(clauses)))
        .collect()
}

fn one_version(deps: Vec<(Package, Set)>) -> PackageData {
    PackageData {
        versions: vec![VersionData {
            version: Version {
                key: Key(0, 0, 0, 1, Vec::new()),
                text: "0.0.0".to_owned(),
            },
            prerelease: false,
            unavailable: None,
            deps,
        }],
        preferred: None,
    }
}

/// Choose one version of every collection the root needs, by name, or raise `NoSolution` with the explanation.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub(crate) fn resolve(
    py: Python<'_>,
    packages: HashMap<String, Vec<VersionEntry>>,
    roots: Vec<(String, Requirement)>,
    groups: Vec<(String, Vec<(String, Requirement)>)>,
    preferred: HashMap<String, String>,
    missing: HashMap<String, String>,
    sources: HashMap<String, String>,
    overrides: HashSet<String>,
    root_depends: String,
    root_unsat: String,
) -> PyResult<HashMap<String, String>> {
    let mut table = Table {
        packages: HashMap::with_capacity(packages.len() + groups.len() + 1),
        direct: HashSet::new(),
        missing,
        sources,
        overrides,
        root_depends,
        root_unsat,
    };
    for (name, entries) in packages {
        let mut versions: Vec<VersionData> = entries
            .into_iter()
            .map(|(text, key, prerelease, unavailable, deps)| VersionData {
                version: Version { key, text },
                prerelease,
                unavailable,
                deps: dependencies(&deps),
            })
            .collect();
        versions.sort_by(|a, b| a.version.cmp(&b.version));
        let preferred = preferred
            .get(&name)
            .and_then(|text| versions.iter().find(|v| v.version.text == *text))
            .map(|v| v.version.clone());
        table
            .packages
            .insert(Package::Collection(name), PackageData { versions, preferred });
    }
    let mut root_deps = dependencies(&roots);
    table.direct.extend(root_deps.iter().map(|(p, _)| p.clone()));
    for (group, deps) in groups {
        let deps = dependencies(&deps);
        table.direct.extend(deps.iter().map(|(p, _)| p.clone()));
        root_deps.push((Package::Group(group.clone()), Ranges::full()));
        table.packages.insert(Package::Group(group), one_version(deps));
    }
    let root = one_version(root_deps);
    let root_version = root.versions[0].version.clone();
    table.packages.insert(Package::Root, root);

    let outcome = py.detach(|| match pubgrub::resolve(&table, Package::Root, root_version) {
        Ok(solution) => Ok(solution
            .iter()
            .filter_map(|(package, version)| match package {
                Package::Collection(name) => Some((name.clone(), version.text.clone())),
                _ => None,
            })
            .collect::<HashMap<String, String>>()),
        Err(PubGrubError::NoSolution(tree)) => {
            let tree = prune(tree, &table);
            Err(Some(DefaultStringReporter::report_with_formatter(
                &tree,
                &Formatter { table: &table },
            )))
        }
        // The provider can't fail, and nothing cancels it.
        Err(_) => Err(None),
    });
    match outcome {
        Ok(chosen) => Ok(chosen),
        Err(Some(report)) => Err(NoSolution::new_err(report)),
        Err(None) => Err(PyRuntimeError::new_err("the resolver failed in a way it can't")),
    }
}
