"""
ark/ui/browser_logic.py -- Session B's non-widget logic, same discipline
as ark/ui/logic.py (see that module's own docstring): pure Python, zero
Streamlit dependency, so it's fully unit-testable
(tests/test_estate_browser.py) without Streamlit installed. `app.py`'s
new page (ark/ui/pages/1_Project_Browser.py) is the only thing that turns
these already-computed values into `st.*` widget calls.

Import-boundary note, same rule ark/ui/logic.py already follows and
tests/test_milestone8.py's TestImportBoundary already checks for it: this
module never imports `ark.mutation.engine`, `ark.mutation.operators`, or
`ark.mutation.ledger` directly. It reaches saved mutation data only
through `ark.generator.persistence.load_estate()` (the same blessed
gateway Session A built), and it reaches "what the estate looked like
before mutation" only by re-rendering the saved estate through the
existing, unmodified `MuleSoftAdapter` -- never by re-running the mutation
engine or reconstructing anything the ledger already recorded.

Scope, matching the session's own scope: this browses estates
`ark.experiment.runner`'s `save_estates=True` flag (Session A) already
wrote to disk. It does not run trajectories, does not call an agent, and
does not modify the evaluator, the renderer, or any mutation operator --
`render()` is called here exactly the way `ark.experiment.runner` already
calls it: as a read-only consumer, never re-implemented.
"""

from __future__ import annotations

import difflib
import html
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ark.adapters.base import RenderedEstate, TargetAdapter
from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.generator.persistence import (
    GROUND_TRUTH_FILENAME,
    MANIFEST_FILENAME,
    MUTATED_ESTATE_FILENAME,
    RENDERED_DIRNAME,
    LoadedEstate,
    load_estate,
)

GROUND_TRUTH_LEAF = "__ground_truth__"
MANIFEST_LEAF = "__manifest__"
MUTATED_ESTATE_LEAF = "__mutated_estate__"
"""Sentinel leaf values build_file_tree() uses for the three flat,
whole-estate files (as opposed to a `rendered/...` leaf, whose value is
always the real relative artifact path -- see build_file_tree()).
MUTATED_ESTATE_LEAF is Session G's addition, always present in the tree
even for an estate saved before mutated_estate.json existed -- the page
shows "not available for this estate" for that case rather than omitting
the leaf, see read_saved_file_text()'s use in the page."""


@dataclass
class SavedEstateSummary:
    """One row in the list view. Built from manifest.json + a light,
    non-validating peek at ground_truth.json's raw JSON -- deliberately
    NOT load_estate() (which runs full structural/referential validation
    via validate_ground_truth()): a list scan over many saved estates
    should not let one malformed folder take down the whole list. Full
    validation happens later, when a specific estate is actually opened
    (see open_saved_estate() below), where a failure can be shown scoped
    to that one estate instead."""

    estate_id: str
    """The on-disk directory name -- see ark.generator.persistence's own
    module docstring on why this can differ from the estate's internal
    ground_truth.json `estate_id` field (also captured below)."""
    ground_truth_estate_id: str | None
    domain: str | None
    profile_name: str | None
    trajectory_seed: int | None
    mutation_count: int | None
    """len(ledger["records"]) if a ledger was saved, else None -- None is
    a real, honest "no ledger was saved for this estate" (e.g. a plain
    baseline snapshot), not a synonym for zero."""
    generator_seed: int | None
    generator_version: str | None
    saved_at: str
    """ISO8601 UTC -- manifest.json's own on-disk mtime, not a value
    manifest.json's content actually contains (neither the ledger nor the
    generation manifest records a "saved to disk at" timestamp -- see
    ark.generator.persistence's module docstring for what's deliberately
    NOT recorded there). Labeled as such in the UI, not presented as if
    it came from the estate's own data."""
    is_readable: bool
    """False if manifest.json or ground_truth.json couldn't even be
    parsed as JSON -- surfaced as a row the list still shows (with
    whatever fields could be recovered as None), not a silently dropped
    entry, matching this project's "surface real problems" ethos."""


def discover_saved_estates(estates_dir: str | Path) -> list[SavedEstateSummary]:
    """Scan `<estates_dir>/*/manifest.json` for saved estates (i.e.
    exactly what ark.experiment.runner's save_estates=True flag writes).
    Returns one SavedEstateSummary per estate directory found, sorted by
    estate_id. An estates_dir that doesn't exist yet returns an empty
    list, not an error -- a brand new project with nothing saved is a
    normal, expected state, not a bug."""
    root = Path(estates_dir)
    if not root.is_dir():
        return []

    summaries: list[SavedEstateSummary] = []
    for manifest_path in sorted(root.glob(f"*/{MANIFEST_FILENAME}")):
        summaries.append(_summarize_one(manifest_path.parent))
    return summaries


def _summarize_one(estate_dir: Path) -> SavedEstateSummary:
    is_readable = True
    ledger: dict | None = None
    generation_manifest: dict | None = None
    ground_truth_estate_id: str | None = None
    domain: str | None = None

    manifest_path = estate_dir / MANIFEST_FILENAME
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        ledger = manifest_payload.get("ledger")
        generation_manifest = manifest_payload.get("generation_manifest")
    except (OSError, json.JSONDecodeError):
        is_readable = False

    ground_truth_path = estate_dir / GROUND_TRUTH_FILENAME
    try:
        raw_ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
        ground_truth_estate_id = raw_ground_truth.get("estate_id")
        domain = raw_ground_truth.get("domain")
    except (OSError, json.JSONDecodeError):
        is_readable = False

    try:
        saved_at_mtime = manifest_path.stat().st_mtime
        saved_at = datetime.fromtimestamp(saved_at_mtime, tz=timezone.utc).isoformat()
    except OSError:
        saved_at = ""
        is_readable = False

    return SavedEstateSummary(
        estate_id=estate_dir.name,
        ground_truth_estate_id=ground_truth_estate_id,
        domain=domain,
        profile_name=ledger.get("profile_name") if ledger else None,
        trajectory_seed=ledger.get("trajectory_seed") if ledger else None,
        mutation_count=len(ledger.get("records", [])) if ledger else None,
        generator_seed=generation_manifest.get("seed") if generation_manifest else None,
        generator_version=generation_manifest.get("generator_version") if generation_manifest else None,
        saved_at=saved_at,
        is_readable=is_readable,
    )


def filter_saved_estates(summaries: list[SavedEstateSummary], query: str) -> list[SavedEstateSummary]:
    """Case-insensitive substring match against estate_id,
    ground_truth_estate_id, profile_name, and domain -- whichever of
    those a given summary actually has. An empty/whitespace-only query
    returns every summary unchanged (no filtering), matching how a search
    box that hasn't been typed into yet should behave."""
    normalized = query.strip().lower()
    if not normalized:
        return list(summaries)

    def matches(summary: SavedEstateSummary) -> bool:
        haystacks = (
            summary.estate_id,
            summary.ground_truth_estate_id,
            summary.profile_name,
            summary.domain,
        )
        return any(h is not None and normalized in h.lower() for h in haystacks)

    return [s for s in summaries if matches(s)]


# ---------------------------------------------------------------------------
# Session D: "Estate Directory Discoverability" -- finding *which* estates/
# folders exist at all, as distinct from browsing a folder once you already
# know its path (everything above this point, untouched by this session).
# ---------------------------------------------------------------------------

DEFAULT_ESTATE_SEARCH_ROOTS: tuple[str, ...] = ("examples", "ui_runs")
"""Derived from the actual conventions observed in this repo, not guessed
-- now two of them:

1. `examples/<script-specific-name>/run_output` -- every demo/example
   script that calls run_experiment(..., output_dir=...) (grep the repo
   for `output_dir=` to check this directly) uses this shape. There's no
   single shared output_dir across scripts, but there IS a single shared
   ancestor: `examples/`.
2. `ui_runs/<estate-name-slug>` -- the live Streamlit "Run Experiment"
   button (ark/ui/app.py) always saves here now, keyed by whatever name
   the user typed into the "Estate name" field
   (ark.ui.logic.slugify_run_name()'d first). Added specifically so a
   real, user-triggered run shows up in this dropdown with no manual path
   entry needed -- the exact gap that prompted adding it.

A caller that saved estates somewhere else entirely (neither convention)
is exactly what the Project Browser's free-text field stays for --
discovery augments manual entry, it never replaces it."""

_SKIP_DIR_NAMES = frozenset(
    {".git", "__pycache__", "node_modules", "venv", ".venv", ".pytest_cache", "ark.egg-info"}
)
"""Directory names never descended into while scanning -- version control
internals, caches, and virtual envs that could otherwise make a recursive
scan slow or noisy without ever containing a real estates/ folder."""

_MAX_SCAN_DEPTH = 8
"""A generous but finite recursion cap, purely so a pathological directory
structure (a symlink cycle, an unexpectedly deep tree) can't make discovery
hang -- not a limit expected to matter for this project's real layout."""


@dataclass
class DiscoveredEstateRoot:
    """One `estates/` folder found by discover_estate_roots(), already
    confirmed (via the existing, unmodified discover_saved_estates()) to
    contain at least one real saved estate -- an empty or nonexistent
    `estates/` folder never appears here at all, so the UI never has to
    special-case "found but empty"."""

    path: str
    estate_count: int
    most_recent_saved_at: str
    """The max() of every estate's own SavedEstateSummary.saved_at inside
    this folder -- reuses Session A's per-estate manifest.json mtime
    (see SavedEstateSummary.saved_at's own docstring); not a new timestamp
    source, just the most recent one already available."""


def _candidate_estates_dirs(base: Path, depth: int = 0) -> list[Path]:
    if depth > _MAX_SCAN_DEPTH or not base.is_dir():
        return []
    if base.name in _SKIP_DIR_NAMES or (base.name.startswith(".") and depth > 0):
        return []

    found: list[Path] = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir() or entry.name in _SKIP_DIR_NAMES:
            continue
        if entry.name == "estates":
            found.append(entry)
            continue  # a folder named "estates" is a leaf for this search -- don't descend into it further
        if entry.name.startswith("."):
            continue
        found.extend(_candidate_estates_dirs(entry, depth + 1))
    return found


def discover_estate_roots(search_roots: list[str] | None = None) -> list[DiscoveredEstateRoot]:
    """Walk `search_roots` (default: DEFAULT_ESTATE_SEARCH_ROOTS above)
    looking for any directory literally named `estates` that contains at
    least one valid saved estate. Validation reuses discover_saved_estates()
    verbatim (never reimplemented here) -- the exact same "does this
    subfolder have a manifest.json" check the list view itself already
    relies on, so a folder that passes here is guaranteed to actually show
    something if opened.

    Returns one DiscoveredEstateRoot per distinct folder found (de-duplicated
    if reachable from more than one search root), sorted by
    most_recent_saved_at descending -- newest first, so a caller can pick
    result[0] as a sensible default without any further logic.

    A `search_roots` entry that doesn't exist on disk is skipped silently,
    not an error -- e.g. the default "examples" root is meaningless (and
    harmless to skip) for a copy of this project laid out differently.
    """
    roots = search_roots if search_roots is not None else list(DEFAULT_ESTATE_SEARCH_ROOTS)

    by_path: dict[str, DiscoveredEstateRoot] = {}
    for search_root in roots:
        base = Path(search_root)
        if not base.is_dir():
            continue
        for candidate in _candidate_estates_dirs(base):
            candidate_key = str(candidate.resolve())
            if candidate_key in by_path:
                continue
            summaries = discover_saved_estates(candidate)
            if not summaries:
                continue
            most_recent = max((s.saved_at for s in summaries if s.saved_at), default="")
            by_path[candidate_key] = DiscoveredEstateRoot(
                path=str(candidate),
                estate_count=len(summaries),
                most_recent_saved_at=most_recent,
            )

    return sorted(by_path.values(), key=lambda root: root.most_recent_saved_at, reverse=True)


def open_saved_estate(estate_dir: str | Path) -> LoadedEstate:
    """Thin passthrough to ark.generator.persistence.load_estate() --
    kept as a named function here (rather than importing load_estate
    directly into the UI page) purely so every real piece of Session B
    logic lives in this one Streamlit-free module, matching ark/ui/logic.py's
    own "app.py calls logic, never reimplements it" convention. This is
    where full structural/referential validation happens (unlike the
    lightweight discover_saved_estates() scan above) -- a malformed
    estate raises here, scoped to the one estate the user actually opened."""
    return load_estate(estate_dir)


def build_file_tree(rendered_artifact_paths: list[str]) -> dict[str, Any]:
    """Build the nested-folder structure the sidebar tree renders,
    mirroring the exact on-disk layout Session A's save_estate() writes:
    ground_truth.json, mutated_estate.json (Session G), and manifest.json
    as flat leaves, `rendered/` as a folder containing whatever nested
    structure the real artifact paths describe (e.g.
    "OrderAPI/src/main/mule/OrderAPI.xml" becomes three nested folder
    levels under rendered/).

    A leaf's value is either GROUND_TRUTH_LEAF/MUTATED_ESTATE_LEAF/
    MANIFEST_LEAF (the three sentinels) or, under "rendered", the real
    artifact path string itself -- so a caller can go straight from
    "which leaf did the user click" to "which dict key fetches its
    content" with no extra lookup table. A folder is a dict; a leaf is a
    string. Empty rendered_artifact_paths still produces a `rendered`
    key -- an empty folder, not an absent one, since save_estate() always
    creates that directory even for zero artifacts.

    MUTATED_ESTATE_LEAF is always included, even when this particular
    estate has no mutated_estate.json on disk (an estate saved before
    Session G, or saved without a mutated estate on purpose) -- the page
    is expected to show "not available for this estate" when it opens a
    leaf whose file doesn't exist (read_saved_file_text() already
    returns None for that, no new error path needed), rather than this
    function silently omitting the entry.

    Return value's top-level key order is deliberate and meaningful here
    (unlike a typical dict): ground_truth.json, mutated_estate.json,
    manifest.json, rendered -- so the mutated estate sits directly below
    its ground-truth sibling when the page renders top-level entries in
    this order rather than alphabetically (see
    ark/ui/pages/1_Project_Browser.py's _render_tree, which sorts nested
    folder contents but preserves this top level's order as-is).
    """
    rendered_tree: dict[str, Any] = {}
    for path in sorted(rendered_artifact_paths):
        parts = [p for p in path.split("/") if p]
        node = rendered_tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if parts:
            node[parts[-1]] = path

    return {
        GROUND_TRUTH_FILENAME: GROUND_TRUTH_LEAF,
        MUTATED_ESTATE_FILENAME: MUTATED_ESTATE_LEAF,
        MANIFEST_FILENAME: MANIFEST_LEAF,
        RENDERED_DIRNAME: rendered_tree,
    }


def read_saved_file_text(estate_dir: str | Path, relative_path: str) -> str | None:
    """Read a file's raw text straight off disk, for the two flat,
    whole-estate leaves (ground_truth.json / manifest.json) that don't
    have a "before vs. after mutation" pairing -- shown as a single,
    literal view of what's actually on disk, not reconstructed from
    load_estate()'s already-parsed dataclasses (which would risk a subtle
    mismatch with the real file, and manifest.json's ledger specifically
    can't be re-serialized here anyway without importing
    ark.mutation.ledger directly, which this module deliberately never
    does -- see the module docstring).

    Returns None if the file doesn't exist or can't be read/decoded --
    the caller shows "unavailable" rather than crashing the page.
    """
    path = Path(estate_dir) / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def baseline_text_for_rendered_path(
    loaded: LoadedEstate, rendered_path: str, *, adapter: TargetAdapter | None = None
) -> str | None:
    """The "ground truth (before mutation)" side of the diff for one
    rendered/ file: re-render `loaded.estate` (Session A's corrected
    design -- this is the saved BASELINE, not the mutated estate; see
    ark.experiment.runner.TrajectoryRunResult.baseline_estate's docstring)
    through the same adapter used to produce rendered_path in the first
    place, then look up that exact relative path in the result.

    Returns None if the baseline render has no artifact at that path --
    a real, expected outcome (e.g. a mutation that added a whole new
    artifact with no pre-mutation counterpart), shown by the caller as
    "no ground truth entry" rather than treated as an error. Re-renders on
    every call rather than caching -- these are small, fast, demo-sized
    estates (see MuleSoftAdapter/render_application_xml, both untouched by
    this session), and caching would be premature complexity here.
    """
    resolved_adapter = adapter if adapter is not None else MuleSoftAdapter()
    baseline_rendered: RenderedEstate = resolved_adapter.render(loaded.estate)
    return baseline_rendered.artifacts.get(rendered_path)


_DIFF_TABLE_CSS = """
<style>
table.diff {font-family: monospace; border: 1px solid #444; width: 100%; font-size: 0.8rem;}
table.diff td, table.diff th {padding: 2px 6px; white-space: pre-wrap; word-break: break-all;}
.diff_header {background-color: #2b2b2b; color: #aaa; text-align: right;}
td.diff_header {background-color: #2b2b2b; color: #aaa;}
.diff_next {background-color: #2b2b2b;}
.diff_add {background-color: #144a2a; color: #d4f8d4;}
.diff_chg {background-color: #4a3f14; color: #f8f0d4;}
.diff_sub {background-color: #4a1414; color: #f8d4d4;}
</style>
"""
"""Minimal, self-contained CSS for difflib.HtmlDiff's default class names
(diff_add/diff_chg/diff_sub/diff_header/diff_next) -- difflib.make_table()
emits a plain <table> with no styles of its own (unlike make_file(), which
embeds a stylesheet we don't want the rest of, e.g. its own <html>/<head>
wrapper). Dark-theme colors chosen to stay readable inside Streamlit's
default dark-friendly layout; not meant to be a general-purpose theme."""


def html_diff_table(
    before: str | None,
    after: str | None,
    *,
    before_label: str = "Ground truth (before mutation)",
    after_label: str = "Rendered (after mutation)",
) -> str:
    """A side-by-side, line-level HTML diff table -- stdlib difflib only
    (difflib.HtmlDiff), per the session's own "don't build a custom diff
    algorithm or pull in a heavy dependency" instruction. `before`/`after`
    of None (e.g. baseline_text_for_rendered_path() found no match) is
    treated as empty text, not an error -- the resulting table simply
    shows every line of whichever side IS present as fully added/removed,
    which is the honest, correct diff of "nothing" vs. "something."

    Returns a complete, self-contained HTML fragment (table + a small
    inline <style> block) -- safe to hand directly to
    st.markdown(..., unsafe_allow_html=True) with no further assembly.
    """
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()
    differ = difflib.HtmlDiff(wrapcolumn=100)
    table = differ.make_table(
        before_lines, after_lines, before_label, after_label, context=True, numlines=3
    )
    return _DIFF_TABLE_CSS + table


def is_identical(before: str | None, after: str | None) -> bool:
    """Whether a diff pane would show zero changes -- used by the UI page
    to show a plain "no differences" message instead of a same-vs-same
    diff table (which difflib would render as a wall of unchanged-context
    lines with nothing highlighted, technically correct but not useful)."""
    return (before or "") == (after or "")


# ---------------------------------------------------------------------------
# Session G: "Estate Deletion, Mutated JSON View, and Interactive Mutation
# Highlighting." Three additions, kept in their own section below everything
# Sessions A/B/D built (all untouched above this point):
#   1. delete_estate() -- the first, and only, destructive action this
#      module performs. Everything else here is read-only.
#   2. mutated_estate.json support -- already handled above, in
#      build_file_tree()'s new MUTATED_ESTATE_LEAF entry; reading it back
#      is just read_saved_file_text(estate_dir, MUTATED_ESTATE_FILENAME),
#      the existing function, unchanged.
#   3. Ledger-driven mutation highlighting -- MutationHighlight,
#      mutation_highlights_for_estate(), highlights_by_entity_id(), and
#      highlighted_mutated_json_html() below. Deliberately NOT a difflib
#      comparison of ground_truth.json vs. mutated_estate.json (that would
#      be guessing which lines look different); this reads
#      loaded.ledger.records directly -- the same MutationLedger
#      load_estate() already reconstructs -- so only entities the ledger
#      itself names as affected are ever flagged, never a text-diff
#      approximation of that. Note this only ever touches loaded.ledger's
#      attributes by duck-typed access (`.records`, `.affected_entity_ids`,
#      etc.) -- it never imports ark.mutation.ledger's MutationLedger/
#      MutationRecord classes by name, preserving this module's own
#      import-boundary rule (see the module docstring): the ledger object
#      itself still only ever arrives via
#      ark.generator.persistence.load_estate(), the one blessed gateway.
# ---------------------------------------------------------------------------


def delete_estate(estate_id: str, estates_dir: str | Path) -> bool:
    """Permanently remove `<estates_dir>/<estate_id>/` from disk -- the
    first destructive action anywhere in this module, so it is kept as
    its own small, standalone function (not folded into
    discover_saved_estates() or open_saved_estate()) specifically so it's
    trivial to audit in isolation from every read-only function above it.
    The caller (ark/ui/pages/1_Project_Browser.py) is responsible for
    requiring an explicit confirmation step before ever calling this --
    this function itself performs the delete unconditionally and
    immediately, with no confirmation of its own, the same way
    shutil.rmtree() would.

    Returns True if a directory was actually removed, False if
    `estate_id` had no matching directory under `estates_dir` to begin
    with -- a no-op, not an error (e.g. a stale list showing an estate
    that was already deleted in another browser tab/session). A real
    OSError while deleting (e.g. a permissions problem) is left to
    propagate -- silently swallowing that would hide a genuine failure
    to delete from whoever asked for it.

    Refuses (raises ValueError, without touching disk) if the resolved
    target would land outside `estates_dir` itself -- e.g. an
    `estate_id` containing "..". `estate_id` is always meant to be one of
    the literal directory names discover_saved_estates() just listed, but
    this guard costs nothing and turns a hypothetical path-traversal
    mistake into a loud, immediate error instead of a silent deletion of
    the wrong directory.
    """
    root = Path(estates_dir).resolve()
    target = (root / estate_id).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Refusing to delete {target} -- it is not inside {root}.")
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


@dataclass
class MutationHighlight:
    """One ledger record's effect on one entity it names -- the unit
    this session's highlighting is built from. A single ledger record
    can list more than one affected_entity_id (see
    ark.mutation.ledger.MutationRecord.affected_entity_ids); one
    MutationHighlight is produced per (record, entity_id) pair, so a
    caller can look up "what happened to this specific entity" without
    re-deriving it from the raw ledger each time. Every field here is
    copied verbatim from the originating MutationRecord -- no new
    computation, no re-derivation, just the ledger's own answer, flattened
    for lookup by entity_id."""

    entity_id: str
    mutation_id: str
    transformation_type: str
    rationale: str
    severity: float
    original_state: dict
    transformed_state: dict


def mutation_highlights_for_estate(loaded: LoadedEstate) -> list[MutationHighlight]:
    """Every ledger record's effect on every entity it names, flattened
    to one MutationHighlight per (record, entity_id) pair. `loaded.ledger`
    (already reconstructed by ark.generator.persistence.load_estate() --
    never re-derived, re-run, or independently diffed here) is the single
    source of truth for which entities were mutated and why; this
    performs no comparison of ground_truth.json against
    mutated_estate.json of its own.

    Returns [] if this estate has no saved ledger at all (e.g. a plain
    baseline snapshot with no mutation ever run against it) -- a real,
    honest "nothing to highlight," not an error.
    """
    if loaded.ledger is None:
        return []

    highlights: list[MutationHighlight] = []
    for record in loaded.ledger.records:
        for entity_id in record.affected_entity_ids:
            highlights.append(
                MutationHighlight(
                    entity_id=entity_id,
                    mutation_id=record.mutation_id,
                    transformation_type=record.transformation_type,
                    rationale=record.rationale,
                    severity=record.severity,
                    original_state=record.original_state,
                    transformed_state=record.transformed_state,
                )
            )
    return highlights


def highlights_by_entity_id(highlights: list[MutationHighlight]) -> dict[str, list[MutationHighlight]]:
    """Group MutationHighlight entries by entity_id, preserving ledger
    order within each group -- an entity touched by more than one
    mutation (e.g. two separate operators both happening to touch the
    same step) gets every one of its highlights, in the order the ledger
    itself realized them, not just the first. Used by the page to render
    one clickable expander per mutated entity, each listing every
    rationale that applies to it."""
    grouped: dict[str, list[MutationHighlight]] = {}
    for highlight in highlights:
        grouped.setdefault(highlight.entity_id, []).append(highlight)
    return grouped


_MUTATION_HIGHLIGHT_CSS = """
<style>
pre.mutation-highlight-json {
  font-family: monospace; font-size: 0.8rem; white-space: pre-wrap;
  word-break: break-all; border: 1px solid #444; border-radius: 4px;
  padding: 8px; margin: 0;
}
pre.mutation-highlight-json mark {
  background-color: #4a3f14; color: #f8f0d4; border-radius: 2px; padding: 0 2px;
}
</style>
"""
"""Same minimal, self-contained-block pattern as _DIFF_TABLE_CSS above,
kept separate from it since this highlights one JSON file's text rather
than a difflib table -- a different mechanism (see this section's own
docstring), so deliberately not sharing markup/classes with
_DIFF_TABLE_CSS even though both end up dark-themed <mark>/diff colors."""


def highlighted_mutated_json_html(mutated_json_text: str, highlighted_entity_ids: set[str]) -> str:
    """Wrap the `"id": "<value>"` line of every entity_id in
    `highlighted_entity_ids` in an HTML <mark>, leaving every other
    character of `mutated_json_text` completely untouched -- a targeted,
    ledger-driven highlight (see mutation_highlights_for_estate()), not a
    difflib-based guess: only entities the ledger actually names as
    affected are ever marked here, regardless of whether their serialized
    fields happen to differ from ground_truth.json's copy.

    Relies on `mutated_json_text` being exactly what
    ark.core.serialize.estate_to_dict() + json.dumps(indent=2) produces
    (the same shape ground_truth.json/mutated_estate.json both use, and
    exactly what read_saved_file_text() returns for this file) -- every
    entity dataclass in ark.core.models (Application, API, Flow, and
    every Step kind) declares `id` as its first field, so each entity's
    id always serializes as one literal `"id": "<value>"` line. Matched
    on that exact substring (itself escaped the same way the surrounding
    text is, so the two stay comparable after escaping) and replaced at
    most once per entity_id, so an id that happens to be a substring of
    another id can never cause an extra, mismatched highlight.

    Returns a complete, self-contained HTML fragment (a styled <pre> plus
    a small inline <style> block) -- safe to hand directly to
    st.markdown(..., unsafe_allow_html=True), the same
    "returns ready-to-render HTML" contract html_diff_table() already
    established, applied here to one JSON file instead of a two-sided
    diff.
    """
    escaped_text = html.escape(mutated_json_text)
    for entity_id in sorted(highlighted_entity_ids):
        target = html.escape(f'"id": {json.dumps(entity_id)}')
        escaped_text = escaped_text.replace(target, f"<mark>{target}</mark>", 1)
    return _MUTATION_HIGHLIGHT_CSS + f'<pre class="mutation-highlight-json">{escaped_text}</pre>'
