"""
Ark's interactive UI — Milestone 8.

A thin, local-only, single-user Streamlit dashboard for demonstrating and
running Ark experiments interactively — no new pipeline logic, no new
scoring, no database, no auth, no cloud deployment. Every number this UI
ever displays was already computed by `ark.experiment` / `ark.evaluator` /
`ark.harness`; this package only builds requests into that pipeline
(`TrajectorySpec`s, `AgentClient`s) and extracts already-computed values
into plain, display-ready structures.

Two modules:
- `logic.py`: pure Python, zero UI-framework dependency. Everything that
  isn't literally a Streamlit widget call lives here, specifically so it
  can be unit-tested (`tests/test_milestone8.py`) without Streamlit
  installed at all.
- `app.py`: the actual Streamlit page (`streamlit run ark/ui/app.py`).
  Imports `streamlit` plus `logic.py`, and — per the architecture
  requirement this milestone was built under — imports directly from
  `ark.experiment`, `ark.evaluator`, and `ark.harness` for the pieces
  `logic.py` doesn't already wrap (e.g. `report_to_json`/`analysis_to_json`
  for the Export buttons).

Isolation boundary, restated because this is the one place a human
researcher can see BOTH sides at once (unlike the agent itself, which
never can): the UI's "Artifact Viewer" section must show only
`rendered_artifacts` — the plain `dict[str, str]` the agent was actually
handed — and nothing manifest/ledger/ground-truth-shaped. Everything else
the evaluator computed (issues, the mutation ledger's contents, the
manifest) is shown only in a section explicitly labeled as hidden from the
agent, never mixed into the artifact view. See `logic.py`'s
`assert_artifacts_contain_no_evaluator_metadata()` for the checked, not
just documented, version of this guarantee.
"""

from __future__ import annotations
