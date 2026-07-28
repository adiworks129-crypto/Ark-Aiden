"""
ark/ui/logic.py — Milestone 8's Streamlit-free business logic.

Deliberately contains NO `import streamlit` anywhere, and NO scoring,
matching, or metrics logic of its own. Every function here is one of
exactly two kinds:

1. Request-building: turning UI selections (a couple of strings, a seed,
   a count) into the exact objects `ark.experiment`/`ark.harness` already
   accept (`TrajectorySpec`, `AgentClient`) — no new orchestration, just
   argument assembly.
2. Extraction: pulling already-computed values out of an `EvaluationReport`
   or `ExperimentAnalysis` into plain dicts/lists ready for a table or
   chart — no computation of anything those objects don't already contain.

Kept import-light and streamlit-free specifically so `tests/test_milestone8.py`
can exercise all of it without Streamlit installed, matching the same
"business logic is independently testable" discipline every prior
milestone's non-UI modules already followed.

Import boundary: this module imports `ark.experiment`, `ark.evaluator`,
and `ark.harness` (the required architecture), plus `ark.generator.config`
(to build a `GeneratorConfig`) and `ark.mutation.profiles.PROFILES` for
profile *names and descriptions only* (plain config data already exposed
to, and used by, `ark.experiment.spec.TrajectorySpec` itself — not the
mutation engine, operators, or ledger). It never imports
`ark.mutation.engine`, `ark.mutation.operators`, or `ark.mutation.ledger`.
`integrations.anthropic_agent_client` and `integrations.gemini_agent_client`
are each imported lazily, inside `build_agent_client()`, only when that
specific real-LLM option is actually chosen.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from ark.evaluator.analysis import ExperimentAnalysis
from ark.evaluator.analysis import analysis_to_json as _analysis_to_json
from ark.evaluator.report import EvaluationReport
from ark.evaluator.report import report_to_json as _report_to_json
from ark.experiment.runner import ExperimentRunResult, run_experiment
from ark.experiment.spec import TrajectorySpec
from ark.generator.config import GeneratorConfig
from ark.harness.contract import AgentClient
from ark.harness.heuristic_client import HeuristicNamingAgentClient
from ark.harness.scripted_client import ScriptedAgentClient
from ark.mutation.profiles import PROFILES

MILESTONE1_GROUND_TRUTH = "examples/milestone1/ground_truth.json"

AGENT_CHOICE_SCRIPTED = "ScriptedAgentClient (offline)"
AGENT_CHOICE_ANTHROPIC = "Anthropic Claude Agent (API)"
AGENT_CHOICE_GEMINI = "Gemini Agent (API)"

ANTHROPIC_DEMO_MODEL = "claude-haiku-4-5-20251001"
"""The model this UI asks AnthropicAgentClient to use -- a fast, cheap
model appropriate for a single interactive demo run, not
integrations/anthropic_agent_client.py's own DEFAULT_MODEL (left
untouched; that module's default is a separate, general-purpose choice
for callers who construct it directly). Overridden here, at the one
call site that builds it for this UI, rather than by changing the
integration itself."""

GEMINI_DEMO_MODEL = "gemini-3.1-flash-lite"
"""The model this UI asks GeminiAgentClient to use -- Google's cheapest/
fastest current Gemini tier, appropriate for a single interactive demo
run, not integrations/gemini_agent_client.py's own DEFAULT_MODEL (left
untouched, same reasoning as ANTHROPIC_DEMO_MODEL above)."""

ESTATE_SOURCE_MILESTONE1 = "Milestone 1 hand-authored estate"
ESTATE_SOURCE_GENERATOR = "Generator (GeneratorConfig)"

ESTATE_SOURCE_CHOICES = (ESTATE_SOURCE_MILESTONE1, ESTATE_SOURCE_GENERATOR)

PROFILE_CHOICES: list[str] = list(PROFILES.keys())
"""In definition order (level_0_clean .. level_3_legacy, plus Feature 2's
opt-in domain_injection_preview) -- see ark.mutation.profiles.PROFILES.
Plain config data (name, level, description), not mutation engine/
operator/ledger logic."""

DOMAIN_PROFILE_NAME = "domain_injection_preview"
"""The one profile whose operator (DomainComponentInjectionOperator) needs
GroundTruthEstate.domain set to find any candidates at all -- see that
operator's own find_candidates() docstring. Every other profile ignores
domain entirely, exactly as before this constant existed."""

DOMAIN_CHOICES: tuple[str, ...] = ("finance", "retail")
"""A stable, ordered tuple for the UI dropdown -- matches
ark.generator.config.SUPPORTED_DOMAINS' two values, but that's a `set`
(no defined iteration order to build a dropdown from), so this is its own
small, explicit, UI-side constant, same reasoning PROFILE_CHOICES/
ESTATE_SOURCE_CHOICES above already follow for their own dropdowns."""


def profile_description(profile_name: str) -> str:
    return PROFILES[profile_name].description


# ---------------------------------------------------------------------------
# Agent selection
# ---------------------------------------------------------------------------


def available_agent_choices() -> list[str]:
    """All options are always listed -- the offline agent needs no setup
    at all, and the real API-backed agents are shown even when they
    aren't ready to run yet, so a user can discover and select one and be
    told exactly what's missing (see anthropic_missing_requirements() /
    gemini_missing_requirements()) rather than have it silently vanish
    from the dropdown."""
    return [AGENT_CHOICE_SCRIPTED, AGENT_CHOICE_ANTHROPIC, AGENT_CHOICE_GEMINI]


def anthropic_missing_requirements() -> list[str]:
    """Human-readable list of what's preventing the real Anthropic agent
    from running right now -- empty list means it's ready to go.

    Checked proactively, before any API call is attempted, specifically
    so the UI can show one clear, friendly message up front instead of
    letting an SDK-level exception (whose shape differs depending on
    whether the package is even installed) surface as an unhandled
    crash. Checking at call time, not import time, means this module
    never requires `anthropic` to be installed just to be imported.
    """
    problems = []
    try:
        import anthropic  # noqa: F401
    except ImportError:
        problems.append(
            "The 'anthropic' package is not installed. Install it with: "
            'pip install -e ".[llm]"'
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append(
            "The ANTHROPIC_API_KEY environment variable is not set. "
            "See the README's \"Running the interactive UI\" section for how to set it."
        )
    return problems


def gemini_missing_requirements() -> list[str]:
    """Same idea as anthropic_missing_requirements(), for the Gemini
    agent: empty list means it's ready to run right now."""
    problems = []
    try:
        from google import genai  # noqa: F401
    except ImportError:
        problems.append(
            "The 'google-genai' package is not installed. Install it with: "
            'pip install -e ".[llm]"'
        )
    if not os.environ.get("GEMINI_API_KEY"):
        problems.append(
            "The GEMINI_API_KEY environment variable is not set. "
            "See the README's \"Running the interactive UI\" section for how to set it."
        )
    return problems


def build_agent_client(choice: str) -> AgentClient:
    """Build the AgentClient for a UI agent-choice string.

    The "ScriptedAgentClient" option really does return a
    `ark.harness.scripted_client.ScriptedAgentClient` instance (so
    `isinstance(client, ScriptedAgentClient)` holds) -- its responder is
    `HeuristicNamingAgentClient`'s real, offline, ground-truth-blind
    naming-irregularity heuristic (see that module's docstring), rather
    than a trivial always-empty stub, so the demo actually has something
    to show. It is still fully deterministic and makes no network call.

    The "Anthropic Claude Agent" option constructs a real
    `integrations.anthropic_agent_client.AnthropicAgentClient`, requested
    with ANTHROPIC_DEMO_MODEL (a fast/cheap model suitable for a single
    demo run) -- callers should check anthropic_missing_requirements()
    first and handle a non-empty result themselves; this function does
    not swallow the ImportError/API errors constructing or calling it can
    raise, so a caller (ark/ui/app.py) can show its own friendly message
    around it instead.

    The "Gemini Agent" option is the same pattern: a real
    `integrations.gemini_agent_client.GeminiAgentClient`, requested with
    GEMINI_DEMO_MODEL -- callers should check gemini_missing_requirements()
    first, for the same reasons.
    """
    if choice == AGENT_CHOICE_SCRIPTED:
        heuristic = HeuristicNamingAgentClient()
        return ScriptedAgentClient(heuristic.generate)
    if choice == AGENT_CHOICE_ANTHROPIC:
        # Imported lazily so this module never requires `anthropic` to be
        # installed unless this exact option is chosen -- and lives in
        # integrations/, never ark/, per Milestone 7's architecture (see
        # integrations/__init__.py).
        from integrations.anthropic_agent_client import AnthropicAgentClient

        return AnthropicAgentClient(model=ANTHROPIC_DEMO_MODEL)
    if choice == AGENT_CHOICE_GEMINI:
        # Same lazy-import, same reasoning, same integrations/ location.
        from integrations.gemini_agent_client import GeminiAgentClient

        return GeminiAgentClient(model=GEMINI_DEMO_MODEL)
    raise ValueError(f"Unknown agent choice: {choice!r}. Available: {available_agent_choices()}")


def agent_model_label(agent_client: AgentClient) -> str:
    """A human-readable "what actually ran" label for the Results
    Dashboard's "Agent Model Used" card.

    Reads the real, concrete model value off the constructed
    `agent_client` instance itself (its own `.model` property, on
    AnthropicAgentClient/GeminiAgentClient) rather than deriving it from
    the UI choice string -- so it stays accurate even if
    ANTHROPIC_DEMO_MODEL/GEMINI_DEMO_MODEL change, or a caller ever
    constructs one of these clients with a different model directly. The
    offline `ScriptedAgentClient` has no "model" at all (it's not backed
    by any LLM), so it gets its own fixed, honest label instead of a
    fabricated one.
    """
    if isinstance(agent_client, ScriptedAgentClient):
        return AGENT_CHOICE_SCRIPTED
    model = getattr(agent_client, "model", None)
    if model is not None:
        return f"{type(agent_client).__name__} ({model})"
    return type(agent_client).__name__


# ---------------------------------------------------------------------------
# Experiment configuration -> TrajectorySpecs
# ---------------------------------------------------------------------------


def build_trajectory_specs(
    estate_source: str, profile_name: str, seed: int, num_trajectories: int,
    *, domain: str | None = None,
) -> list[TrajectorySpec]:
    """Build `num_trajectories` TrajectorySpecs at one profile, one per
    consecutive seed starting at `seed` (seed, seed+1, ..., seed+n-1) --
    the same "vary the seed, hold the profile fixed" pattern
    `examples/milestone6/generate_analysis_example.py` and
    `examples/milestone7/run_experiment_example.py` already used to get a
    batch with real, non-degenerate signal.

    Raises ValueError for an unrecognized profile_name or estate_source --
    surfaced to the UI as an error, not silently ignored.

    `domain`, if given, is passed straight through to
    `GeneratorConfig(domain=...)` -- and ONLY for the generator estate
    source. For `estate_source == ESTATE_SOURCE_MILESTONE1`, `domain` is
    silently ignored: the hand-authored Milestone 1 estate has no way to
    be tagged with a domain from this UI (its own ground-truth file has no
    "domain" key, and TrajectorySpec.baseline_estate_path has no override
    for it) -- callers should warn the user themselves before calling this
    in that combination (see app.py), rather than have this function raise
    for what is itself correct, documented, zero-mutation behavior (see
    ark.mutation.operators.DomainComponentInjectionOperator.find_candidates).
    Every profile OTHER than `DOMAIN_PROFILE_NAME` ignores `domain`
    entirely regardless of estate source, exactly as before this parameter
    existed -- passing it for e.g. `level_3_legacy` has no effect beyond
    tagging the generated estate (which that profile's operators never
    read).
    """
    if profile_name not in PROFILES:
        raise ValueError(f"Unknown profile: {profile_name!r}. Available: {PROFILE_CHOICES}")
    if estate_source not in ESTATE_SOURCE_CHOICES:
        raise ValueError(f"Unknown estate source: {estate_source!r}. Available: {list(ESTATE_SOURCE_CHOICES)}")
    if num_trajectories < 1:
        raise ValueError(f"num_trajectories must be >= 1, got {num_trajectories}.")

    specs = []
    for offset in range(num_trajectories):
        trajectory_seed = seed + offset
        label = f"{profile_name}-seed{trajectory_seed}"
        if estate_source == ESTATE_SOURCE_MILESTONE1:
            specs.append(
                TrajectorySpec(
                    label=label, profile_name=profile_name, seed=trajectory_seed,
                    baseline_estate_path=MILESTONE1_GROUND_TRUTH,
                )
            )
        else:
            specs.append(
                TrajectorySpec(
                    label=label, profile_name=profile_name, seed=trajectory_seed,
                    generator_config=GeneratorConfig(seed=trajectory_seed, domain=domain),
                )
            )
    return specs


# ---------------------------------------------------------------------------
# Running the experiment -- a thin passthrough, no new orchestration
# ---------------------------------------------------------------------------


def run_ui_experiment(specs: list[TrajectorySpec], agent_client: AgentClient) -> ExperimentRunResult:
    """Exactly ark.experiment.runner.run_experiment(specs, agent_client) --
    no new logic. Exists so ark/ui/app.py has one obvious place to call
    for "run the whole pipeline," even though app.py is equally free to
    (and does, for other calls) import ark.experiment/ark.evaluator/
    ark.harness directly, per the architecture requirement."""
    return run_experiment(specs, agent_client)


def report_for_label(run_result: ExperimentRunResult, label: str) -> EvaluationReport:
    for report in run_result.reports:
        if report.metadata.trajectory_id == label:
            return report
    raise KeyError(f"No report with trajectory_id {label!r} in this run.")


def trajectory_labels(run_result: ExperimentRunResult) -> list[str]:
    return [report.metadata.trajectory_id for report in run_result.reports]


# ---------------------------------------------------------------------------
# Results Dashboard -- plain-data extraction, no computation
# ---------------------------------------------------------------------------


def environment_summary_rows(report: EvaluationReport) -> dict[str, Any]:
    env = report.environment_summary
    txn = report.transformation_summary
    return {
        "Applications": env.application_count,
        "APIs": env.api_count,
        "Flows": env.flow_count,
        "Rendered artifacts": env.artifact_count,
        "Dependencies": env.dependency_count,
        "Mutation count": txn.total_mutations,
        "Complexity score": txn.complexity_score,
    }


def agent_performance_rows(report: EvaluationReport) -> dict[str, Any]:
    perf = report.agent_performance
    return {
        "Total findings": perf.total_findings,
        "Category precision": perf.category_metrics.precision,
        "Category recall": perf.category_metrics.recall,
        "Category F1": perf.category_metrics.f1,
        "Entity localization accuracy": perf.entity_localization_metrics.localization_accuracy,
        "Brier score": perf.calibration.brier_score,
        "Expected Calibration Error (ECE)": perf.calibration.ece,
    }


# ---------------------------------------------------------------------------
# Experiment Summary card -- already-computed aggregates + plain UI labels
# ---------------------------------------------------------------------------

METRIC_DIRECTION_HINTS: dict[str, str] = {
    "Category precision": "higher is better",
    "Category recall": "higher is better",
    "Category F1": "higher is better",
    "Entity localization accuracy": "higher is better",
    "Brier score": "lower is better",
    "Expected Calibration Error (ECE)": "lower is better",
    "Average category F1": "higher is better",
    "Average localization accuracy": "higher is better",
    "Average calibration error (ECE)": "lower is better",
    "Average complexity score": "context only, not a performance score",
}
"""Static, presentation-only lookup: which direction counts as "good" for
a metric label already shown somewhere on the dashboard. Purely a display
label -- adds no computation of any kind and never affects a single
number shown, only how its column/metric header reads."""


def metric_direction_hint(metric_label: str) -> str:
    """" (higher is better)" / " (lower is better)" suffix for a metric
    label, or "" if this label has no direction convention recorded (e.g.
    a plain count like "Total findings" or "Trajectory count"). Looked up
    from METRIC_DIRECTION_HINTS -- see its own docstring."""
    hint = METRIC_DIRECTION_HINTS.get(metric_label)
    return f" ({hint})" if hint else ""


def experiment_summary_rows(
    analysis: ExperimentAnalysis, *, agent_model_used: str, estate_source: str, profile_name: str
) -> dict[str, Any]:
    """The top-of-dashboard "Experiment Summary" card, covering the whole
    experiment (not just one trajectory).

    Every numeric field here is read straight off
    `analysis.experiment_summary` -- an `ExperimentSummary` already
    assembled, experiment-wide, by
    `ark.evaluator.analysis.analyze_reports()`; this function computes
    nothing new of its own, just like every other *_rows() function in
    this module. `agent_model_used`/`estate_source`/`profile_name` are
    plain UI-selection strings threaded through from the sidebar/session
    state (see ark/ui/app.py), not derived from any report.
    """
    summary = analysis.experiment_summary
    return {
        "Agent model used": agent_model_used,
        "Estate source": estate_source,
        "Mutation profile": profile_name,
        "Trajectory count": summary.trajectory_count,
        "Average complexity score": summary.average_complexity_score,
        "Average category F1": summary.average_category_f1,
        "Average localization accuracy": summary.average_entity_localization_accuracy,
        "Average calibration error (ECE)": summary.average_calibration_ece,
    }


# The exact four buckets the milestone spec named, in that order, plus the
# fifth real FailureAnalysis bucket (wrong_category_predictions) included
# too -- omitting a real, already-computed failure mode from the dashboard
# just because it wasn't named in the four-item list would be an honesty
# regression the rest of this project has consistently avoided.
_FAILURE_BUCKET_LABELS: tuple[tuple[str, str], ...] = (
    ("Missed issues", "missed_issues"),
    ("Hallucinations", "hallucinated_findings"),
    ("Wrong diagnosis (right entity, wrong issue type)", "correct_location_incorrect_diagnosis"),
    ("Overconfidence", "overconfidence_patterns"),
    ("Wrong category (claimed a type matching no real issue anywhere)", "wrong_category_predictions"),
)


def failure_analysis_rows(report: EvaluationReport) -> dict[str, list[dict]]:
    fa = report.failure_analysis
    return {
        display_label: [dataclasses.asdict(entry) for entry in getattr(fa, attr_name)]
        for display_label, attr_name in _FAILURE_BUCKET_LABELS
    }


# ---------------------------------------------------------------------------
# Research Visualization -- row-shaping for ExperimentAnalysis, no new stats
# ---------------------------------------------------------------------------


def complexity_vs_performance_rows(analysis: ExperimentAnalysis) -> list[dict]:
    return [
        {
            "complexity_bucket": bucket.label,
            "category_f1": bucket.average_category_f1,
            "localization_accuracy": bucket.average_entity_localization_accuracy,
            "calibration_ece": bucket.average_calibration_ece,
        }
        for bucket in analysis.complexity_analysis.buckets
    ]


def complexity_scatter_rows(run_result: ExperimentRunResult) -> list[dict]:
    """One row per trajectory -- the raw, unbucketed data point behind the
    old bucketed "Complexity vs Performance" chart: this trajectory's real
    complexity_score (ark.mutation.engine's dynamic, agent-independent
    complexity model, already computed per-report) plotted against this
    trajectory's real category_f1 (already computed by
    ark.evaluator.metrics.compute_category_metrics()). No new computation
    -- just reading two already-computed fields off each report.

    Trajectories whose category_f1 is None (a report with zero real Issues,
    for which precision/recall/F1 are undefined -- see
    ark.evaluator.metrics.ClassificationMetrics.f1's own docstring) are
    skipped entirely, per the "ignore trajectories where category_f1 is
    None" requirement, rather than plotted as a fabricated 0 or excluded
    silently without a trace.
    """
    rows = []
    for report in run_result.reports:
        f1 = report.agent_performance.category_metrics.f1
        if f1 is None:
            continue
        rows.append(
            {
                "trajectory_label": report.metadata.trajectory_id,
                "complexity_score": report.transformation_summary.complexity_score,
                "category_f1": f1,
            }
        )
    return rows


def calibration_scatter_rows(run_result: ExperimentRunResult) -> list[dict]:
    """One row per trajectory whose calibration Brier score is defined --
    the per-trajectory data behind the scatter plot that replaced the
    bucketed "Calibration Drift" line chart. Brier score (not ECE) is the
    y-axis because it's defined for any report with at least one scored
    claim (sample_size > 0), while
    ark.evaluator.calibration.CalibrationResult.ece is None below that
    module's own DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE -- ECE is still included
    per row (as `ece`, possibly None) so a tooltip can show it when
    available. No new computation: both values are read straight off
    report.agent_performance.calibration, already computed by
    ark.evaluator.calibration.compute_calibration().
    """
    rows = []
    for report in run_result.reports:
        calibration = report.agent_performance.calibration
        if calibration.brier_score is None:
            continue
        rows.append(
            {
                "trajectory_label": report.metadata.trajectory_id,
                "complexity_score": report.transformation_summary.complexity_score,
                "brier_score": calibration.brier_score,
                "ece": calibration.ece,
            }
        )
    return rows


def linear_trendline(
    rows: list[dict], x_key: str = "complexity_score", y_key: str = "category_f1"
) -> dict[str, float] | None:
    """Ordinary-least-squares slope/intercept over (x_key, y_key) pairs, in
    plain Python (no numpy/scipy -- this is a UI display convenience, not a
    new evaluator metric, so it deliberately doesn't add a dependency or
    live anywhere near ark/evaluator/).

    Returns None -- meaning "don't draw a trendline" -- if there are fewer
    than 2 points (a line needs at least two) or if every point shares the
    same x (a vertical spread of one complexity value has no meaningful
    slope). Callers (ark/ui/app.py) are expected to treat None as "skip the
    trendline," not an error.
    """
    xs = [row[x_key] for row in rows]
    ys = [row[y_key] for row in rows]
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    return {"slope": slope, "intercept": intercept, "min_x": min(xs), "max_x": max(xs)}


def complexity_correlation_rows(analysis: ExperimentAnalysis) -> list[dict]:
    """The Pearson correlation coefficients themselves (association only,
    never causation -- see each CorrelationStatistic.disclaimer), one row
    per metric, for a simple bar/table rather than a line chart."""
    return [
        {
            "metric": correlation.metric_name,
            "correlation_with_complexity": correlation.correlation_with_complexity_score,
            "sample_size": correlation.sample_size,
        }
        for correlation in analysis.complexity_analysis.correlations
    ]


def transformation_impact_rows(analysis: ExperimentAnalysis) -> list[dict]:
    """Per-transformation-type observed performance and baseline
    degradation -- already sorted worst-first by
    ark.evaluator.analysis.analyze_reports()."""
    return [
        {
            "transformation_type": entry.transformation_type,
            "observed_category_f1": entry.observed.average_category_f1,
            "category_f1_degradation": entry.category_f1_degradation,
            "calibration_ece_degradation": entry.calibration_ece_degradation,
        }
        for entry in analysis.transformation_impact_analysis.by_transformation_type
    ]


def calibration_drift_rows(analysis: ExperimentAnalysis) -> list[dict]:
    return [
        {
            "complexity_bucket": point.complexity_bucket_label,
            "average_confidence": point.average_confidence,
            "average_category_f1": point.average_category_f1,
            "confidence_minus_accuracy_gap": point.confidence_minus_accuracy_gap,
        }
        for point in analysis.calibration_drift_analysis.points
    ]


# ---------------------------------------------------------------------------
# Artifact Viewer -- the isolation boundary, made checkable
# ---------------------------------------------------------------------------


def issue_rows(report: EvaluationReport) -> list[dict]:
    """The real Issues this trajectory was scored against, as plain rows
    -- the "answer key" a human researcher (never the agent) can inspect.
    Verbatim from report.issues, just flattened to dicts for a table."""
    return [dataclasses.asdict(issue) for issue in report.issues]


def artifacts_for_label(run_result: ExperimentRunResult, label: str) -> dict[str, str]:
    """Exactly what the agent was shown for this trajectory -- see
    ark.experiment.runner.TrajectoryRunResult.rendered_artifacts."""
    return run_result.artifacts_by_label[label]


_EVALUATOR_ONLY_KEYS = frozenset(
    {
        "manifest", "entity_index", "dependencies", "artifacts",
        "ledger", "records", "issues", "mutation_ledger", "ground_truth",
        "raw_agent_output", "research_hooks", "failure_analysis",
    }
)
"""Key names that only ever appear on evaluator-side structures (a
rendering manifest dict, a MutationLedger, an EvaluationReport section) --
never on a real rendered-artifacts dict, whose keys are always file
paths. If any of these ever showed up as a key in what's about to be
labeled "Visible to Agent," that's a real isolation-boundary bug, not
something to quietly filter out."""


def assert_artifacts_contain_no_evaluator_metadata(artifacts: dict[str, str]) -> None:
    """Defensive, TESTED (not just documented) proof that what the
    Artifact Viewer is about to display really is just rendered file
    content: every key looks like a file path, and every value is plain
    text. Raises AssertionError loudly rather than silently coercing or
    filtering, matching this project's "surface real bugs" ethos.
    """
    for key, value in artifacts.items():
        assert key not in _EVALUATOR_ONLY_KEYS, (
            f"Artifact viewer received evaluator-only key {key!r} -- isolation boundary violated."
        )
        assert isinstance(key, str) and isinstance(value, str), (
            f"Artifact viewer entry {key!r} -> {type(value).__name__} is not a plain "
            "(path: str, content: str) pair -- expected rendered file content only."
        )


# ---------------------------------------------------------------------------
# Export -- thin passthroughs to the existing serializers
# ---------------------------------------------------------------------------


def export_report_json(report: EvaluationReport) -> str:
    return _report_to_json(report)


def export_analysis_json(analysis: ExperimentAnalysis) -> str:
    return _analysis_to_json(analysis)
