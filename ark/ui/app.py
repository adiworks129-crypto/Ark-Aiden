"""
Ark Interactive UI — Milestone 8 (+ scatter-plot / agent-model / educational-text /
Experiment Summary card pass).

A thin Streamlit dashboard over the existing experiment runner. Run with:

    streamlit run ark/ui/app.py

This module contains NO scoring, matching, or pipeline logic of its own —
every number displayed here was already computed by `ark.experiment` /
`ark.evaluator` / `ark.harness` (via `ark.ui.logic`, which does the
non-widget work so it can be unit-tested without Streamlit at all — see
that module's docstring). This file's only job is turning already-computed
values into `st.*` calls, plus (this pass) adding plain explanatory text so
the page is self-explanatory to someone who has never seen Ark before. The
explanatory text describes what the pipeline already does; it does not
change what the pipeline does.

Architecture, restated: this module imports directly from `ark.experiment`,
`ark.evaluator`, and `ark.harness` (for the pieces `logic.py` exposes
one-to-one, like `report_to_json`/`analysis_to_json`), plus `ark.ui.logic`
for everything else. It never imports `ark.mutation.engine`,
`ark.mutation.operators`, or `ark.mutation.ledger` — the pipeline itself
is always reached through `ark.experiment.run_experiment()`, never
re-implemented or re-invoked piecemeal here. `altair` is used for the one
scatter chart below; it ships as a hard dependency of `streamlit` itself
(see the `ui` extra), so this adds no new required dependency.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from ark.evaluator.analysis import analysis_to_json
from ark.evaluator.report import report_to_json
from ark.ui import logic

st.set_page_config(page_title="Ark — Interactive Experiment Runner", layout="wide")

from ark.ui.theme import apply_theme  # styling only -- no data, layout or behaviour
apply_theme()

st.title("Ark — Interactive Experiment Runner")
st.caption(
    "A local, single-user demo of Ark's full pipeline: Generator → Mutation Engine → "
    "Renderer → Agent Harness → Evaluator → Analysis. No database, no auth, local "
    "execution only."
)

with st.expander("ℹ️ What is Ark, in 30 seconds?"):
    st.markdown(
        "Ark builds small, synthetic \"enterprise integration estates\" (fake but "
        "realistic API/flow systems, e.g. Mule applications) **with a known answer "
        "key** — every real issue in the estate is recorded before anything is shown "
        "to an agent. An AI agent is then given only the rendered files (XML/YAML) "
        "and asked to find integration problems, with no access to that answer key. "
        "Ark's evaluator compares the agent's findings against the real answer key to "
        "produce precision/recall/calibration metrics — a way to measure how good an "
        "AI agent actually is at this kind of work, and whether its confidence is "
        "trustworthy."
    )

# ---------------------------------------------------------------------------
# 1. Experiment Configuration
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("1. Experiment Configuration")
    st.caption(
        "These controls decide what gets built and who evaluates it — nothing runs "
        "until you click **Run Experiment** below."
    )

    if "suggested_run_name" not in st.session_state:
        st.session_state["suggested_run_name"] = logic.suggest_run_name()
    estate_name = st.text_input(
        "Estate name",
        value=st.session_state["suggested_run_name"],
        help=(
            "Every trajectory this run produces is saved to disk under this name — "
            "find it later in the Project Browser page's saved-estates list. Doesn't "
            "need to be unique forever, just intuitive to you right now."
        ),
    )
    run_name_slug = logic.slugify_run_name(estate_name)

    st.markdown("---")

    agent_choice = st.selectbox(
        "Agent",
        logic.available_agent_choices(),
        help=(
            "ScriptedAgentClient runs entirely offline, no setup needed. The "
            "Anthropic Claude Agent makes a real API call (model: "
            f"{logic.ANTHROPIC_DEMO_MODEL}) and needs `pip install -e \".[llm]\"` "
            "plus an ANTHROPIC_API_KEY environment variable. The Gemini Agent "
            f"makes a real API call (model: {logic.GEMINI_DEMO_MODEL}) and needs "
            "`pip install -e \".[llm]\"` plus a GEMINI_API_KEY environment "
            "variable — see the README if you haven't set either up yet."
        ),
    )
    st.caption(
        "This is the system being evaluated. Whichever option you pick, it will see "
        "**only** the rendered artifact files below — never the ground truth, the "
        "mutation ledger, or any internal ids."
    )

    _missing_requirements_by_choice = {
        logic.AGENT_CHOICE_ANTHROPIC: logic.anthropic_missing_requirements,
        logic.AGENT_CHOICE_GEMINI: logic.gemini_missing_requirements,
    }
    if agent_choice in _missing_requirements_by_choice:
        for problem in _missing_requirements_by_choice[agent_choice]():
            st.warning(problem)

    estate_source = st.radio("Estate source", logic.ESTATE_SOURCE_CHOICES)
    st.caption(
        f"**{logic.ESTATE_SOURCE_MILESTONE1}**: one small, hand-written, "
        "human-reviewed estate used throughout this project, good for reproducible "
        "comparisons. **{}**: `ark.generator` procedurally builds a fresh estate "
        "from a seed — a layered graph of applications/APIs/flows with realistic "
        "naming, sized and shaped by the seed alone (no LLM involved in generation "
        "itself).".format(logic.ESTATE_SOURCE_GENERATOR)
    )

    profile_name = st.selectbox(
        "Complexity / profile", logic.PROFILE_CHOICES, index=len(logic.PROFILE_CHOICES) - 2,
    )
    st.caption(logic.profile_description(profile_name))
    with st.expander("ℹ️ What does the mutation engine actually do?"):
        st.markdown(
            "Once an estate exists (hand-authored or generated), `ark.mutation` "
            "applies this profile's set of transformation operators to it — "
            "realistic changes like renaming things inconsistently, breaking a "
            "dependency, or introducing dead/legacy artifacts. Every change it makes "
            "is recorded in a **mutation ledger**, which becomes the answer key "
            "(`ark.evaluator.issues` turns ledger entries into scoreable `Issue`s). "
            "Higher profile levels apply more, and more severe, transformations — "
            "this is the \"difficulty\" knob."
        )

    # domain_injection_preview's operator needs GroundTruthEstate.domain set
    # (finance/retail) to find any candidates at all -- every other profile
    # ignores domain entirely, so this control only appears for this one
    # profile and never changes what any other profile does.
    domain = None
    if profile_name == logic.DOMAIN_PROFILE_NAME:
        if estate_source == logic.ESTATE_SOURCE_GENERATOR:
            domain = st.selectbox(
                "Domain (for domain_injection_preview)",
                logic.DOMAIN_CHOICES,
                help=(
                    "This profile injects one component that's realistic on its own "
                    "terms but implausible for THIS domain (e.g. an SAP Retail/Supply "
                    "Chain integration turning up in a finance-domain estate) -- see "
                    "ark/generator/domain_plausibility.json. Without a domain assigned, "
                    "this profile correctly finds zero eligible candidates and realizes "
                    "zero mutations (documented behavior, not a bug) -- this selector is "
                    "what actually gives it one."
                ),
            )
        else:
            st.warning(
                f"**{logic.ESTATE_SOURCE_MILESTONE1}** has no domain assigned, and this "
                "UI has no way to tag it with one after the fact. Running "
                f"`{logic.DOMAIN_PROFILE_NAME}` against it will correctly realize **zero "
                "mutations** for every trajectory (documented behavior, not a bug — see "
                "DomainComponentInjectionOperator.find_candidates()). Switch \"Estate "
                f"source\" above to **{logic.ESTATE_SOURCE_GENERATOR}** to actually pick "
                "a domain and exercise this profile."
            )

    seed = st.number_input("Starting random seed", min_value=0, value=1, step=1)
    num_trajectories = st.number_input(
        "Number of trajectories", min_value=1, max_value=20, value=3, step=1,
        help="Runs this many trajectories at the selected profile, one per consecutive seed.",
    )
    st.caption(
        "A **trajectory** is one full run: one estate, mutated once, rendered, shown "
        "to the agent, and scored. Running several trajectories (varying only the "
        "seed) is what gives the Research Visualization charts below more than a "
        "single point to plot."
    )

    st.markdown("---")
    st.header("2. Run Experiment")
    st.caption(
        "Runs the real pipeline end to end: Generator/hand-authored estate → Mutation "
        "Engine → Renderer → Agent Harness → Evaluator → Analysis. Every step below is "
        "just a display of what that pipeline already computed."
    )
    run_clicked = st.button("Run Experiment", type="primary", use_container_width=True)

if run_clicked:
    _blocking_problems = (
        _missing_requirements_by_choice[agent_choice]()
        if agent_choice in _missing_requirements_by_choice
        else []
    )
    if _blocking_problems:
        # Checked proactively so the failure mode is one clear, friendly
        # message instead of an exception surfacing mid-run. The offline
        # agent is unaffected by any of this -- selecting it never reaches
        # this branch at all.
        st.error(
            f"Can't run the {agent_choice} yet:\n\n"
            + "\n".join(f"- {problem}" for problem in _blocking_problems)
        )
    else:
        try:
            agent_client = logic.build_agent_client(agent_choice)
            specs = logic.build_trajectory_specs(
                estate_source, profile_name, int(seed), int(num_trajectories),
                domain=domain, run_name=run_name_slug,
            )
            output_dir = f"{logic.UI_RUNS_ROOT}/{run_name_slug}"
            with st.spinner(
                f"Running {len(specs)} trajectories: Generator → Mutation Engine → Renderer → "
                "Agent Harness → Evaluator → Analysis..."
            ):
                st.session_state["run_result"] = logic.run_ui_experiment(
                    specs, agent_client, output_dir=output_dir, save_estates=True,
                )
                st.session_state["agent_choice"] = agent_choice
                st.session_state["estate_source"] = estate_source
                st.session_state["profile_name"] = profile_name
                st.session_state["saved_estates_dir"] = f"{output_dir}/estates"
                # Read off the constructed client itself (never a hardcoded
                # per-choice string), so this stays accurate even if the demo
                # model constants change later -- see agent_model_label()'s
                # own docstring in ark/ui/logic.py.
                st.session_state["agent_model_used"] = logic.agent_model_label(agent_client)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # failure building or running a real agent (bad key, network
            # error, rate limit, an unexpected SDK exception type, ...)
            # must be shown as a friendly message, never an unhandled
            # traceback that crashes the page. The offline agent path
            # above has no such external failure mode to guard against.
            st.error(f"The experiment run failed: {exc}")

if "run_result" not in st.session_state:
    st.info("Configure an experiment in the sidebar, then click **Run Experiment**.")
    st.stop()

run_result = st.session_state["run_result"]
analysis = run_result.analysis
labels = logic.trajectory_labels(run_result)

st.success(
    f"Ran {len(labels)} trajectories with **{st.session_state.get('agent_choice', '?')}**."
)
if st.session_state.get("saved_estates_dir"):
    st.caption(
        f"Estates saved to `{st.session_state['saved_estates_dir']}` — open the **Project "
        "Browser** page in the sidebar to browse them."
    )
selected_label = st.selectbox("Select trajectory to inspect", labels)
report = logic.report_for_label(run_result, selected_label)

# ---------------------------------------------------------------------------
# 3. Results Dashboard
# ---------------------------------------------------------------------------

st.header("3. Results Dashboard")

st.subheader("Experiment Summary")
st.caption(
    "The whole experiment, at a glance: what ran it and what was tested (top row), "
    "and top-line averages across every trajectory in this run (bottom row) — all "
    "already computed by `ark.evaluator.analysis.analyze_reports()`, just displayed "
    "here. Per-trajectory detail (for the one trajectory selected above) is in "
    "Environment Summary / Agent Performance below."
)
_summary = logic.experiment_summary_rows(
    analysis,
    agent_model_used=st.session_state.get("agent_model_used", "Unknown"),
    estate_source=st.session_state.get("estate_source", "?"),
    profile_name=st.session_state.get("profile_name", "?"),
)


def _format_summary_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


_config_cols = st.columns(4)
for _col, _key in zip(_config_cols, ("Agent model used", "Estate source", "Mutation profile", "Trajectory count")):
    with _col:
        st.metric(_key, _format_summary_value(_summary[_key]))

_metric_cols = st.columns(4)
for _col, _key in zip(
    _metric_cols,
    (
        "Average complexity score",
        "Average category F1",
        "Average localization accuracy",
        "Average calibration error (ECE)",
    ),
):
    with _col:
        st.metric(f"{_key}{logic.metric_direction_hint(_key)}", _format_summary_value(_summary[_key]))
st.caption(
    "\"—\" means not enough data to compute this average (e.g. no trajectory in this "
    "run had at least the minimum sample size ECE needs — see "
    "`ark.evaluator.calibration`'s own threshold), never a silently-substituted 0."
)

st.markdown("---")
st.caption(
    "The two panels below are for the **one trajectory selected above** — switch "
    "trajectories with the dropdown to see a different run's numbers."
)
col_env, col_perf = st.columns(2)

with col_env:
    st.subheader("Environment Summary")
    st.caption(
        "The shape and difficulty of the estate this trajectory scored the agent "
        "against — applications, flows, artifacts, and how much the mutation engine "
        "changed (mutation count, complexity score). This describes the *test*, not "
        "the agent's performance on it."
    )
    st.table(
        pd.DataFrame(logic.environment_summary_rows(report).items(), columns=["Metric", "Value"])
        .set_index("Metric")
    )

with col_perf:
    st.subheader("Agent Performance")
    st.caption(
        "How well the agent did on this one trajectory, as computed by "
        "`ark.evaluator` — never modified here. Each row's label already says which "
        "direction is good."
    )
    _perf_rows = logic.agent_performance_rows(report)
    _labeled_perf_rows = {f"{k}{logic.metric_direction_hint(k)}": v for k, v in _perf_rows.items()}
    st.table(
        pd.DataFrame(_labeled_perf_rows.items(), columns=["Metric", "Value"])
        .set_index("Metric")
    )

st.subheader("Failure Analysis")
st.caption(
    "The specific ways the agent's findings diverged from the real answer key: real "
    "issues it never mentioned (missed), findings matching no real issue anywhere "
    "(hallucinations), findings at the right location but the wrong diagnosis, cases "
    "where it was confident but wrong (overconfidence), and claims of an issue type "
    "that doesn't match anything real (wrong category). Expand a bucket below for the "
    "actual entries."
)
failure_rows = logic.failure_analysis_rows(report)
failure_cols = st.columns(len(failure_rows))
for column, (bucket_label, entries) in zip(failure_cols, failure_rows.items()):
    with column:
        st.metric(bucket_label, len(entries))
for bucket_label, entries in failure_rows.items():
    if entries:
        with st.expander(f"{bucket_label} ({len(entries)})"):
            st.dataframe(pd.DataFrame(entries), use_container_width=True)

# ---------------------------------------------------------------------------
# 4. Research Visualization
# ---------------------------------------------------------------------------

st.header("4. Research Visualization")
st.caption(
    "These charts summarize every trajectory run in this experiment (not just the "
    "one selected above) — an observed association, never a causal claim (see every "
    "CorrelationStatistic's own disclaimer in the exported ExperimentAnalysis JSON). "
    "Every chart below plots one point per trajectory whenever the underlying data "
    "supports it (a single trajectory still renders as a single point, never a forced "
    "line), with an optional trendline layered on top of the real data -- the "
    "trendline never changes what's plotted, only summarizes it."
)

st.subheader("Complexity vs. Performance (per trajectory)")
with st.expander("ℹ️ How to read this chart"):
    st.markdown(
        "Each point is **one trajectory** from this experiment: its x-position is "
        "that trajectory's complexity score (how much the mutation engine changed "
        "it), and its y-position is the agent's category F1 on it. A downward-sloping "
        "trendline suggests the agent struggles more as complexity rises; a flat or "
        "upward line suggests it doesn't. Trajectories with no real issues (an "
        "undefined F1) are left out rather than plotted as a fabricated zero."
    )
scatter_rows = logic.complexity_scatter_rows(run_result)
if not scatter_rows:
    st.info(
        "No trajectories in this run have a defined category F1 (every report had "
        "zero real issues to score against) — nothing to plot."
    )
else:
    scatter_df = pd.DataFrame(scatter_rows)
    points_chart = (
        alt.Chart(scatter_df)
        .mark_circle(size=90, opacity=0.75, color="#b68235")
        .encode(
            x=alt.X("complexity_score:Q", title="Complexity score"),
            y=alt.Y("category_f1:Q", title="Category F1", scale=alt.Scale(domain=[0, 1])),
            tooltip=[
                alt.Tooltip("trajectory_label:N", title="Trajectory"),
                alt.Tooltip("complexity_score:Q", title="Complexity"),
                alt.Tooltip("category_f1:Q", title="Category F1"),
            ],
        )
    )
    combined_chart = points_chart
    trend = logic.linear_trendline(scatter_rows)
    if trend is not None:
        trend_df = pd.DataFrame(
            [
                {
                    "complexity_score": trend["min_x"],
                    "category_f1": trend["slope"] * trend["min_x"] + trend["intercept"],
                },
                {
                    "complexity_score": trend["max_x"],
                    "category_f1": trend["slope"] * trend["max_x"] + trend["intercept"],
                },
            ]
        )
        trend_chart = (
            alt.Chart(trend_df)
            .mark_line(color="#7d5411", strokeDash=[6, 3])
            .encode(x="complexity_score:Q", y="category_f1:Q")
        )
        combined_chart = combined_chart + trend_chart
    else:
        st.caption(
            "Not enough spread to draw a trendline (fewer than 2 scoreable "
            "trajectories, or they all share the same complexity score)."
        )
    st.altair_chart(combined_chart, use_container_width=True)

with st.expander("Bucketed averages (superseded by the scatter plot above)"):
    st.caption(
        "The complexity-bucket averages this scatter plot replaced as the primary "
        "view -- kept here for anyone who wants the coarser, bucketed summary "
        "alongside the per-trajectory detail above. Same underlying data, just "
        "grouped."
    )
    st.dataframe(
        pd.DataFrame(logic.complexity_vs_performance_rows(analysis)), use_container_width=True
    )

st.subheader("Transformation Type Impact")
st.caption(
    "For each transformation type the mutation engine used anywhere in this "
    "experiment, how much it degrades performance relative to the run's own clean "
    "baseline — sorted worst-first, so the top bars are the transformation types the "
    "agent handles least well. **Higher bars = more degradation = worse** (these are "
    "already degradation values, baseline minus observed, so 0 means no measurable "
    "impact and there's no natural per-trajectory point to scatter here — a "
    "transformation type is a category applied across many trajectories at once, not "
    "a single trajectory's own score)."
)
transformation_rows = logic.transformation_impact_rows(analysis)
if not transformation_rows:
    # Zero realized transformation types across every trajectory in this
    # run -- e.g. a profile whose operator(s) found no eligible candidates
    # anywhere (domain_injection_preview against a domain-less estate is
    # one way this happens, but this guard holds for ANY cause, same as
    # every other "nothing to plot" guard on this page: never assume the
    # rows list is non-empty just because SOME experiment ran.
    st.info(
        "No transformation types were realized in this experiment — every "
        "trajectory's mutation profile found zero eligible candidates (or ran with "
        "num_mutations=0) — nothing to chart here."
    )
else:
    transformation_df = pd.DataFrame(transformation_rows).set_index("transformation_type")
    st.bar_chart(transformation_df)

st.subheader("Calibration Drift (per trajectory)")
with st.expander("ℹ️ How to read this chart"):
    st.markdown(
        "Each point is **one trajectory**: its x-position is that trajectory's "
        "complexity score, and its y-position is its calibration Brier score (mean "
        "squared error between the agent's stated confidence and whether it was "
        "actually right — **lower is better**, 0 is perfect). An upward-sloping "
        "trendline suggests the agent's confidence becomes less trustworthy as "
        "complexity rises. Trajectories with no scored claims at all (nothing to "
        "compute a Brier score from) are left out rather than plotted as a "
        "fabricated 0."
    )
calibration_rows = logic.calibration_scatter_rows(run_result)
if not calibration_rows:
    st.info(
        "No trajectories in this run have a defined Brier score (no findings were "
        "scored against a real issue in any of them) — nothing to plot."
    )
else:
    calibration_df = pd.DataFrame(calibration_rows)
    calibration_points = (
        alt.Chart(calibration_df)
        .mark_circle(size=90, opacity=0.75, color="#b68235")
        .encode(
            x=alt.X("complexity_score:Q", title="Complexity score"),
            y=alt.Y("brier_score:Q", title="Brier score (lower is better)", scale=alt.Scale(domain=[0, 1])),
            tooltip=[
                alt.Tooltip("trajectory_label:N", title="Trajectory"),
                alt.Tooltip("complexity_score:Q", title="Complexity"),
                alt.Tooltip("brier_score:Q", title="Brier score"),
                alt.Tooltip("ece:Q", title="ECE (if enough samples)"),
            ],
        )
    )
    combined_calibration_chart = calibration_points
    calibration_trend = logic.linear_trendline(calibration_rows, x_key="complexity_score", y_key="brier_score")
    if calibration_trend is not None:
        calibration_trend_df = pd.DataFrame(
            [
                {
                    "complexity_score": calibration_trend["min_x"],
                    "brier_score": calibration_trend["slope"] * calibration_trend["min_x"]
                    + calibration_trend["intercept"],
                },
                {
                    "complexity_score": calibration_trend["max_x"],
                    "brier_score": calibration_trend["slope"] * calibration_trend["max_x"]
                    + calibration_trend["intercept"],
                },
            ]
        )
        calibration_trend_chart = (
            alt.Chart(calibration_trend_df)
            .mark_line(color="#7d5411", strokeDash=[6, 3])
            .encode(x="complexity_score:Q", y="brier_score:Q")
        )
        combined_calibration_chart = combined_calibration_chart + calibration_trend_chart
    else:
        st.caption(
            "Not enough spread to draw a trendline (fewer than 2 scoreable "
            "trajectories, or they all share the same complexity score)."
        )
    st.altair_chart(combined_calibration_chart, use_container_width=True)

with st.expander("Bucketed averages (superseded by the scatter plot above)"):
    st.caption(
        "The complexity-bucket confidence-vs-accuracy averages this scatter plot "
        "replaced as the primary view -- same underlying data, just grouped."
    )
    st.dataframe(
        pd.DataFrame(logic.calibration_drift_rows(analysis)), use_container_width=True
    )

with st.expander("Raw correlation coefficients"):
    st.caption(
        "Pearson correlation between complexity score and each metric, across all "
        "trajectories in this run. These are associations only, not causal claims — "
        "see each row's own disclaimer in the exported ExperimentAnalysis JSON. "
        "**Direction can flip entirely between batches at these sample sizes** — "
        "clearing the minimum-sample-size gate below means the number is defined, "
        "not that it's stable. Treat any single batch's correlation as provisional "
        "until the same sign holds up across multiple, larger batches."
    )
    st.dataframe(pd.DataFrame(logic.complexity_correlation_rows(analysis)), use_container_width=True)

# ---------------------------------------------------------------------------
# 5. Artifact Viewer -- Visible to Agent vs. Hidden, kept clearly separate
# ---------------------------------------------------------------------------

st.header("5. Artifact Viewer")
st.caption(
    "This section exists to make the evaluation boundary checkable, not just "
    "claimed: below is a hard split between what the agent actually received and "
    "what only a human researcher is allowed to see."
)

artifacts = logic.artifacts_for_label(run_result, selected_label)
logic.assert_artifacts_contain_no_evaluator_metadata(artifacts)

st.markdown(
    "#### 🟢 Visible to Agent\n"
    "This is the **entire** input the agent received for this trajectory — every "
    "rendered artifact file, and nothing else. No ground truth, no mutation ledger, "
    "no manifest, no internal entity ids."
)
artifact_path = st.selectbox("Artifact file", sorted(artifacts))
language = "xml" if artifact_path.endswith(".xml") else "yaml" if artifact_path.endswith(".yaml") else "text"
st.code(artifacts[artifact_path], language=language)

st.markdown("#### 🔒 Hidden from Agent (research view only)")
with st.expander("Ground truth / mutation ledger / evaluator metadata for this trajectory"):
    st.caption(
        "The agent never saw any of this while producing its findings above — it is "
        "shown here, after the fact, only because a human researcher (unlike the "
        "agent) is allowed to see both sides of the evaluation at once. Showing it "
        "before scoring would defeat the point of the evaluation; showing it now is "
        "how you audit whether the evaluator scored fairly."
    )
    st.markdown("**Real issues (the answer key this trajectory was scored against):**")
    issue_rows = logic.issue_rows(report)
    st.dataframe(pd.DataFrame(issue_rows) if issue_rows else pd.DataFrame(), use_container_width=True)
    st.markdown("**Raw agent output (as parsed):**")
    st.json(report.raw_agent_output)

with st.expander("Original ground truth estate (before mutation)"):
    st.caption(
        "The same estate the 'Visible to Agent' artifacts above were derived from, "
        "rendered through the same adapter but BEFORE this trajectory's mutation "
        "profile ran. The agent never saw this either -- it's here so you can pick "
        "the same file name in both selectors and diff them to see exactly what "
        "this trajectory's mutation profile changed."
    )
    original_artifacts = logic.original_artifacts_for_label(run_result, selected_label)
    original_artifact_path = st.selectbox(
        "Original artifact file", sorted(original_artifacts), key="original_artifact_path"
    )
    original_language = (
        "xml"
        if original_artifact_path.endswith(".xml")
        else "yaml"
        if original_artifact_path.endswith(".yaml")
        else "text"
    )
    st.code(original_artifacts[original_artifact_path], language=original_language)

# ---------------------------------------------------------------------------
# 6. Export
# ---------------------------------------------------------------------------

st.header("6. Export")
st.caption(
    "Download the exact objects this page was built from — useful for archiving a "
    "run, diffing two runs, or feeding a result into some other tool."
)
col_export_report, col_export_analysis = st.columns(2)
with col_export_report:
    st.download_button(
        "Download EvaluationReport JSON",
        report_to_json(report),
        file_name=f"{selected_label}.report.json",
        mime="application/json",
        use_container_width=True,
    )
    st.caption("The full scored result for the one trajectory selected above.")
with col_export_analysis:
    st.download_button(
        "Download ExperimentAnalysis JSON",
        analysis_to_json(analysis),
        file_name="experiment.analysis.json",
        mime="application/json",
        use_container_width=True,
    )
    st.caption("The cross-trajectory analysis behind every chart in section 4.")
