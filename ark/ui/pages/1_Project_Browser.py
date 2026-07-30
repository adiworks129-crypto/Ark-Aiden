"""
Ark — Project Browser (Session B).

A second, additive Streamlit page -- lives under ark/ui/pages/, which
Streamlit auto-discovers as sidebar navigation alongside ark/ui/app.py's
existing single page, with zero edits to app.py itself. Run the same way
app.py already is (`streamlit run ark/ui/app.py`); this page appears
automatically once this file exists.

This module contains NO scoring, matching, mutation, or rendering logic of
its own -- same discipline ark/ui/app.py's own docstring already
establishes for that file. Every value shown here was already computed
by ark.generator.persistence (Session A) or ark.ui.browser_logic (this
session's own Streamlit-free logic module, see its docstring); this file's
only job is turning those into st.* widget calls.

Browses estates ark.experiment.run_experiment(..., save_estates=True)
already wrote to disk (see examples/estate_browser_demo/generate_demo_estates.py
for a script that produces a couple, per this session's own prerequisite).
It does not run trajectories or call an agent -- view-only for everything
generation/mutation/evaluation-related, by design (see Session B's own
"out of scope" list).

Session G addition: this page now also supports permanently deleting a
saved estate from disk (with a required confirmation step), viewing the
saved mutated_estate.json sibling of ground_truth.json, and highlighting
-- directly from the saved mutation ledger, not a text diff -- exactly
which entities mutated_estate.json's mutations touched, with a click-to-
expand rationale per entity. Deletion is the one exception to "view-only"
above; everything else remains read-only.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import streamlit as st

from ark.ui import browser_logic as browser

DEFAULT_ESTATES_DIR = "examples/estate_browser_demo/run_output/estates"

st.set_page_config(page_title="Ark — Project Browser", layout="wide")

from ark.ui.theme import apply_theme  # styling only -- no data, layout or behaviour
apply_theme()

st.title("Ark — Project Browser")
st.caption(
    "Browse estates already saved to disk by ark.experiment.run_experiment(..., "
    "save_estates=True) (Session A) -- a searchable project list, a file-tree view "
    "into one saved estate, and a side-by-side ground-truth-vs-mutated diff for "
    "any rendered file. View-only: nothing here edits or re-runs anything."
)

if "expanded_folders" not in st.session_state:
    st.session_state.expanded_folders = set()
if "selected_leaf" not in st.session_state:
    st.session_state.selected_leaf = None
if "opened_estate_id" not in st.session_state:
    st.session_state.opened_estate_id = None
if "pending_delete_id" not in st.session_state:
    st.session_state.pending_delete_id = None
"""Session G: which estate_id (if any) is mid-confirmation for deletion --
None means "no delete in progress." Set the moment "Delete estate" is
first clicked, cleared on either an explicit confirm or an explicit
cancel -- deleting never happens on a single click (see delete_estate()'s
own docstring for why this is the one destructive action in this page)."""

# ---------------------------------------------------------------------------
# 1. List view
# ---------------------------------------------------------------------------

st.header("1. Saved estates")

discovered_roots = browser.discover_estate_roots()

if discovered_roots:
    root_options = [r.path for r in discovered_roots]
    root_labels = {
        r.path: f"{r.path}  —  {r.estate_count} estate(s), most recent {r.most_recent_saved_at}"
        for r in discovered_roots
    }
    st.caption(
        f"Found {len(discovered_roots)} estates/ folder(s) under `examples/` or `ui_runs/` "
        "(sorted newest first). The most recent is pre-selected."
    )
    chosen_root = st.selectbox(
        "Discovered estates folders",
        root_options,
        format_func=lambda p: root_labels[p],
        key="discovered_root",
    )
    use_custom_path = st.checkbox(
        "Use a different path instead (e.g. an output_dir outside examples/ and ui_runs/)",
        value=False,
    )
    if use_custom_path:
        estates_dir = st.text_input(
            "Estates directory (custom)",
            value=DEFAULT_ESTATES_DIR,
            help="The <output_dir>/estates/ folder run_experiment(save_estates=True) wrote to.",
        )
    else:
        estates_dir = chosen_root
else:
    st.caption(
        "No estates/ folders auto-discovered yet under `examples/` or `ui_runs/` -- enter a path "
        "manually below (auto-discovery only looks under those two, since they're the only "
        "conventions this project uses today; an output_dir chosen freely elsewhere won't be "
        "found automatically)."
    )
    estates_dir = st.text_input(
        "Estates directory",
        value=DEFAULT_ESTATES_DIR,
        help="The <output_dir>/estates/ folder run_experiment(save_estates=True) wrote to.",
    )

search_query = st.text_input(
    "Search", placeholder="Filter by estate id, profile, or domain...", key="search_query"
)

all_summaries = browser.discover_saved_estates(estates_dir)
filtered_summaries = browser.filter_saved_estates(all_summaries, search_query)

if not all_summaries:
    st.info(f"No saved estates found under `{estates_dir}`. Run a script with `save_estates=True` first.")
    st.stop()

st.caption(f"{len(filtered_summaries)} of {len(all_summaries)} saved estate(s) match.")
st.dataframe(
    pd.DataFrame([dataclasses.asdict(s) for s in filtered_summaries]),
    use_container_width=True,
    hide_index=True,
)

if not filtered_summaries:
    st.warning("No saved estates match this search.")
    st.stop()

selected_estate_id = st.selectbox(
    "Open an estate", [s.estate_id for s in filtered_summaries], key="estate_picker"
)

open_col, delete_col = st.columns([1, 1])

with open_col:
    if st.button("Open", type="primary"):
        st.session_state.opened_estate_id = selected_estate_id
        st.session_state.selected_leaf = None
        st.session_state.expanded_folders = set()

with delete_col:
    # Session G: the one destructive action on this page. Never deletes on
    # a single click -- "Delete estate" only arms a confirmation step for
    # this exact estate_id; a second, explicit click on "Yes, delete
    # permanently" is required, or "Cancel" disarms it with no effect.
    if st.session_state.pending_delete_id != selected_estate_id:
        if st.button("Delete estate", key="delete_request"):
            st.session_state.pending_delete_id = selected_estate_id
            st.rerun()
    else:
        st.warning(f"Permanently delete `{selected_estate_id}`? This cannot be undone.")
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("Yes, delete permanently", key="delete_confirm", type="primary"):
                browser.delete_estate(selected_estate_id, estates_dir)
                st.session_state.pending_delete_id = None
                if st.session_state.opened_estate_id == selected_estate_id:
                    st.session_state.opened_estate_id = None
                    st.session_state.selected_leaf = None
                st.success(f"Deleted `{selected_estate_id}`.")
                st.rerun()
        with cancel_col:
            if st.button("Cancel", key="delete_cancel"):
                st.session_state.pending_delete_id = None
                st.rerun()

# ---------------------------------------------------------------------------
# 2 & 3. Detail view: file tree + ground-truth vs. mutated diff
# ---------------------------------------------------------------------------

if st.session_state.opened_estate_id is None:
    st.info("Select an estate above and click **Open** to browse it.")
    st.stop()

estate_dir = f"{estates_dir.rstrip('/')}/{st.session_state.opened_estate_id}"
st.header(f"2. Browsing: {st.session_state.opened_estate_id}")

try:
    loaded = browser.open_saved_estate(estate_dir)
except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
    st.error(f"Couldn't open this estate: {exc}")
    st.stop()

tree = browser.build_file_tree(list(loaded.rendered_artifacts))

detail_col, tree_col = st.columns([3, 1])

with tree_col:
    st.markdown("**Files**")

    def _render_tree(node: dict, path_prefix: str, depth: int) -> None:
        indent = "  " * depth
        # Session G: the top level's own 4 entries keep the fixed,
        # deliberate order build_file_tree() already returns them in --
        # ground_truth.json, mutated_estate.json, manifest.json, then
        # rendered/ -- so the mutated JSON sits directly below its
        # ground-truth sibling. A folder's real contents (e.g. rendered/'s
        # nested artifact paths) still sort alphabetically at any deeper
        # level, since there's no such meaningful fixed order for those.
        names = list(node.keys()) if depth == 0 else sorted(node.keys())
        for name in names:
            value = node[name]
            full_path = f"{path_prefix}/{name}" if path_prefix else name
            if isinstance(value, dict):
                expanded = full_path in st.session_state.expanded_folders
                icon = "\U0001f4c2" if expanded else "\U0001f4c1"  # open/closed folder
                if st.button(f"{indent}{icon} {name}", key=f"toggle::{full_path}"):
                    if expanded:
                        st.session_state.expanded_folders.discard(full_path)
                    else:
                        st.session_state.expanded_folders.add(full_path)
                    st.rerun()
                if expanded:
                    _render_tree(value, full_path, depth + 1)
            else:
                is_selected = st.session_state.selected_leaf == value
                marker = "\U0001f449" if is_selected else "\U0001f4c4"  # pointer/page
                if st.button(f"{indent}{marker} {name}", key=f"select::{full_path}"):
                    st.session_state.selected_leaf = value
                    st.rerun()

    _render_tree(tree, "", 0)

with detail_col:
    st.markdown("**Detail**")
    leaf = st.session_state.selected_leaf

    if leaf is None:
        st.info("Click a file in the tree to view it.")
    elif leaf == browser.GROUND_TRUTH_LEAF:
        st.caption("ground_truth.json — the saved baseline (pre-mutation) estate, verbatim from disk.")
        text = browser.read_saved_file_text(estate_dir, "ground_truth.json")
        st.code(text or "(unavailable)", language="json")
    elif leaf == browser.MANIFEST_LEAF:
        st.caption("manifest.json — the mutation ledger + generation manifest, verbatim from disk.")
        text = browser.read_saved_file_text(estate_dir, "manifest.json")
        st.code(text or "(unavailable)", language="json")
    elif leaf == browser.MUTATED_ESTATE_LEAF:
        st.caption(
            "mutated_estate.json — the saved post-mutation estate, structurally identical to "
            "ground_truth.json. Entities the mutation ledger actually changed are highlighted "
            "below; expand one to see why."
        )
        text = browser.read_saved_file_text(estate_dir, browser.MUTATED_ESTATE_FILENAME)
        if text is None:
            st.info(
                "Not available for this estate -- it was saved before mutated_estate.json existed "
                "(or saved without one on purpose). Not backfilled automatically."
            )
        else:
            highlights = browser.mutation_highlights_for_estate(loaded)
            grouped_highlights = browser.highlights_by_entity_id(highlights)
            st.markdown(
                browser.highlighted_mutated_json_html(text, set(grouped_highlights)),
                unsafe_allow_html=True,
            )
            if grouped_highlights:
                st.markdown("**Mutations in this file** — click an entity to see why it changed.")
                for entity_id, entity_highlights in grouped_highlights.items():
                    label = f"{entity_id} — {entity_highlights[0].transformation_type}"
                    with st.expander(label):
                        for highlight in entity_highlights:
                            st.markdown(f"**Rationale:** {highlight.rationale}")
                            st.caption(
                                f"mutation_id: `{highlight.mutation_id}` · severity: {highlight.severity}"
                            )
                            before_state_col, after_state_col = st.columns(2)
                            with before_state_col:
                                st.markdown("*Before*")
                                st.json(highlight.original_state)
                            with after_state_col:
                                st.markdown("*After*")
                                st.json(highlight.transformed_state)
            else:
                st.caption("No mutations recorded against this estate's ledger.")
    else:
        st.caption(f"`rendered/{leaf}`")
        after_text = loaded.rendered_artifacts.get(leaf)
        before_text = browser.baseline_text_for_rendered_path(loaded, leaf)

        if before_text is None:
            st.warning(
                "No ground-truth entry found at this path in the baseline render -- showing the "
                "mutated/rendered file alone."
            )
            st.code(after_text or "", language="xml" if leaf.endswith(".xml") else "yaml")
        elif browser.is_identical(before_text, after_text):
            st.success("No differences between ground truth and the mutated rendering for this file.")
            st.code(after_text or "", language="xml" if leaf.endswith(".xml") else "yaml")
        else:
            st.markdown(browser.html_diff_table(before_text, after_text), unsafe_allow_html=True)
