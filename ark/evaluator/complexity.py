"""
Dynamic complexity model — Milestone 6.1 (foundation only).

Per your decision, complexity is derived from the REALIZED mutation
ledger, not from the fixed profile label (level_0_clean .. level_3_legacy)
that generated the trajectory. Those labels describe generation-time
intent (which operators were eligible, roughly how many mutations to
try); the engine can stop early (graceful degradation — see
ark/mutation/engine.py and the Milestone 4 tiny-estate test) or have
operators compound in seed-dependent ways, so the same profile can
realize meaningfully different trajectories. This module computes
complexity as a pure function of what actually happened.

This is the agent-INDEPENDENT half of complexity.py described in
Ark_Evaluator_Design.md Section 4.4 — compute_trajectory_complexity() runs
per estate/ledger, needs no agent output at all. The agent-dependent half
(correlate_with_reports(): binning many EvaluationReports by complexity to
answer "how does accuracy degrade as complexity increases") depends on
EvaluationReport, which doesn't exist until Milestone 6.4 — it is
deliberately NOT implemented here, and is still Milestone 6.5's job. See
the implementation plan in Ark_Evaluator_Design.md Section 8.

Milestone 6.3 adds one small, agent-DEPENDENT addition to this file:
TrajectoryPerformanceRecord and build_trajectory_performance_record(),
the "complexity-performance tracking hook" your Milestone 6.3 spec asked
for — a data structure only, pairing one trajectory's ComplexityProfile
with the (already-computed, already-separate) metrics/calibration results
for one agent's run against it. It does NOT do any cross-trajectory
correlation or binning itself — that's still 6.5's job, once enough of
these records exist to make binning statistically meaningful.

Every factor below is documented with the judgment calls it makes,
consistent with Ark's "no undocumented assumptions" principle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ark.core.models import ApiCallStep, FlowRefStep, GroundTruthEstate
from ark.evaluator.calibration import CalibrationResult
from ark.evaluator.issues import Issue, derive_issues
from ark.evaluator.metrics import ClassificationMetrics, EntityLocalizationMetrics
from ark.mutation.ledger import MutationLedger
from ark.mutation.registry import OPERATOR_REGISTRY

PERFORMANCE_RECORD_SCHEMA_VERSION = "0.1.0"

COMPLEXITY_SCHEMA_VERSION = "0.1.0"

# Reasonable-range normalization caps for the scalar rollup (Section 4.4).
# These are heuristic, documented defaults, not empirically tuned — flagged
# as a known, overridable judgment call (Ark_Evaluator_Design.md Section 9).
_MUTATION_COUNT_NORM_CAP = 15.0
_DEPENDENCY_IMPACT_NORM_CAP = 5.0
_COMPOUNDING_NORM_CAP = 5.0


@dataclass(frozen=True)
class ComplexityWeights:
    """Overridable weights for the scalar complexity_score rollup. Defaults
    are equal (1.0 each, then divided by their sum) rather than a hidden
    constant favoring any one factor. Pass a custom instance to
    compute_trajectory_complexity() to re-weight for a specific analysis
    (e.g. weighting dependency_impact higher when specifically studying
    blast-radius effects)."""

    mutation_count: float = 1.0
    severity: float = 1.0
    diversity: float = 1.0
    dependency_impact: float = 1.0
    interaction: float = 1.0
    compounding: float = 1.0

    def total(self) -> float:
        return (
            self.mutation_count
            + self.severity
            + self.diversity
            + self.dependency_impact
            + self.interaction
            + self.compounding
        )


@dataclass
class ComplexityProfile:
    """The full, agent-independent complexity vector for one realized
    trajectory, plus a single scalar rollup for plotting a performance-vs-
    complexity curve (Milestone 6.5). The vector is always reported
    alongside the scalar — the scalar is a convenience projection, never
    the only representation (Ark_Evaluator_Design.md Section 4.4)."""

    baseline_estate_id: str
    trajectory_seed: int
    profile_name: str
    """The nominal generation-time profile (e.g. "level_2_structural") —
    provenance only. Never used to compute anything below; every other
    field here is derived solely from the realized ledger + transformed
    estate."""

    mutation_count: int
    """len(ledger.records) — raw count, before dedup."""
    distinct_issue_count: int
    """len(derive_issues(ledger)) — deduplicated, observable issues. Can be
    lower than mutation_count when records compound or net out to zero."""
    compounding_count: int
    """Number of distinct raw affected_entity_ids hit by more than one
    ledger record."""
    max_compounding_depth: int
    """The highest number of records that hit any single entity."""
    severity_mean: float
    severity_max: float
    affected_entity_count: int
    """Size of the union of affected_entity_ids across all RAW ledger
    records (not filtered by the net-zero consolidation in issues.py) —
    "how much of the estate the trajectory touched," independent of
    whether every touch survived to be observable."""
    transformation_diversity: float
    """distinct transformation_type values used, divided by the total
    number of registered operators (ark.mutation.registry.OPERATOR_REGISTRY) —
    not a hardcoded "6", so this tracks automatically if a 7th operator is
    ever added."""
    transformation_types_used: tuple[str, ...]
    dependency_impact_mean: float
    dependency_impact_max: float
    """"Blast radius": for each Issue-level affected entity, its in-degree
    in the transformed estate's Flow/API dependency graph (how many other
    Flow/API entities reference it via FlowRefStep/ApiCallStep), aggregated.
    Computed at Flow/API granularity, matching the existing rendering
    manifest's dependency-edge convention (ark/adapters/mulesoft/manifest.py) —
    an affected entity that is itself a Step (not a call target) contributes
    0, which is a defensible floor, not an error; see _in_degree_index()."""
    interaction_score: float
    """0.0-1.0: whether Issue-level affected entities are structurally
    connected to each other (same Flow, or a direct FlowRefStep/ApiCallStep
    edge between their owning Flows), vs. isolated from one another. See
    _interaction_score() for the exact, deliberately conservative
    (direct-adjacency-only, not transitive) definition."""
    complexity_score: float
    """Single scalar rollup — weighted average of the normalized factors
    above, for plotting one performance-vs-complexity curve. Never the
    only number reported."""
    weights_used: ComplexityWeights
    complexity_schema_version: str = COMPLEXITY_SCHEMA_VERSION


def _owning_flow_id(estate: GroundTruthEstate, entity_id: str) -> str:
    """Resolve an entity id to the Flow id that best represents its
    structural position, for dependency/interaction analysis:
    - a Flow resolves to itself
    - a Step resolves to its containing Flow
    - an API resolves to its entry Flow
    Falls back to the raw entity_id (as its own isolated node) if it can't
    be resolved this way (e.g. an Application id, or an id no longer
    present in this estate) — a documented simplification, not a crash."""
    for app in estate.applications:
        for flow in app.flows:
            if flow.id == entity_id:
                return flow.id
            for step in flow.steps:
                if step.id == entity_id:
                    return flow.id
        for api in app.apis:
            if api.id == entity_id:
                return api.entry_flow_id
    return entity_id


def _flow_dependency_edges(estate: GroundTruthEstate) -> set[frozenset]:
    """Undirected structural edges between Flow/API ids, mirroring
    ark/adapters/mulesoft/manifest.py's dependency convention (flow-level
    source, not step-level) — computed directly from the estate, since the
    complexity model must work without requiring an adapter to render
    anything first."""
    edges: set[frozenset] = set()
    for app in estate.applications:
        for flow in app.flows:
            for step in flow.steps:
                if isinstance(step, FlowRefStep):
                    edges.add(frozenset({flow.id, step.target_flow_id}))
                elif isinstance(step, ApiCallStep):
                    edges.add(frozenset({flow.id, step.target_api_id}))
    return edges


def _in_degree_index(estate: GroundTruthEstate) -> dict[str, int]:
    """id -> number of distinct FlowRefStep/ApiCallStep references
    targeting it — its "blast radius" if mutated."""
    counts: dict[str, int] = {}
    for app in estate.applications:
        for flow in app.flows:
            for step in flow.steps:
                target = None
                if isinstance(step, FlowRefStep):
                    target = step.target_flow_id
                elif isinstance(step, ApiCallStep):
                    target = step.target_api_id
                if target is not None:
                    counts[target] = counts.get(target, 0) + 1
    return counts


def _interaction_score(nodes: list[str], edges: set[frozenset]) -> float:
    """1 - (num_connected_components - 1) / max(1, unique_node_count - 1),
    over the distinct resolved nodes in `nodes`. 0.0 if there are 0 or 1
    distinct nodes (nothing to interact with). Interaction here means
    DIRECT structural adjacency (same node, or a direct dependency edge)
    between two mutated nodes — deliberately not transitive reachability
    through unmutated intermediaries, a conservative and easy-to-explain
    choice flagged here rather than silently generalized."""
    unique_nodes = list(dict.fromkeys(nodes))
    if len(unique_nodes) <= 1:
        return 0.0

    parent = {n: n for n in unique_nodes}

    def find(n: str) -> str:
        while parent[n] != n:
            n = parent[n]
        return n

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for edge in edges:
        pair = list(edge)
        if len(pair) == 2:
            a, b = pair
            if a in parent and b in parent:
                union(a, b)
        elif len(pair) == 1:
            # A self-loop-shaped edge (flow calling itself) - no effect on
            # connectivity between distinct nodes.
            continue

    num_components = len({find(n) for n in unique_nodes})
    denominator = max(1, len(unique_nodes) - 1)
    return 1.0 - (num_components - 1) / denominator


def _normalize(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return min(1.0, value / cap)


def compute_trajectory_complexity(
    transformed_estate: GroundTruthEstate,
    ledger: MutationLedger,
    *,
    weights: ComplexityWeights | None = None,
) -> ComplexityProfile:
    """Compute the full complexity vector + scalar rollup for one realized
    trajectory. Agent-independent — takes no agent output. A pure function
    of (transformed_estate, ledger); does not require the baseline estate
    object itself, since every factor below is derivable from the ledger's
    diffs and the transformed estate alone (baseline_estate_id is already
    on the ledger for provenance, which is all identifying the baseline is
    needed for here)."""
    weights = weights or ComplexityWeights()
    issues: list[Issue] = derive_issues(ledger)

    mutation_count = len(ledger.records)
    distinct_issue_count = len(issues)

    entity_hit_counts: dict[str, int] = {}
    for record in ledger.records:
        for entity_id in record.affected_entity_ids:
            entity_hit_counts[entity_id] = entity_hit_counts.get(entity_id, 0) + 1
    compounding_count = sum(1 for count in entity_hit_counts.values() if count > 1)
    max_compounding_depth = max(entity_hit_counts.values(), default=0)
    affected_entity_count = len(entity_hit_counts)

    severities = [r.severity for r in ledger.records]
    severity_mean = (sum(severities) / len(severities)) if severities else 0.0
    severity_max = max(severities, default=0.0)

    types_used = tuple(sorted({r.transformation_type for r in ledger.records}))
    total_operator_types = len(OPERATOR_REGISTRY) or 1
    transformation_diversity = len(types_used) / total_operator_types

    issue_entity_ids = sorted({eid for issue in issues for eid in issue.affected_entity_ids})
    in_degree = _in_degree_index(transformed_estate)
    impacts = [in_degree.get(eid, 0) for eid in issue_entity_ids]
    dependency_impact_mean = (sum(impacts) / len(impacts)) if impacts else 0.0
    dependency_impact_max = float(max(impacts, default=0))

    resolved_nodes = [_owning_flow_id(transformed_estate, eid) for eid in issue_entity_ids]
    edges = _flow_dependency_edges(transformed_estate)
    interaction = _interaction_score(resolved_nodes, edges)

    weight_total = weights.total() or 1.0
    complexity_score = (
        weights.mutation_count * _normalize(mutation_count, _MUTATION_COUNT_NORM_CAP)
        + weights.severity * severity_mean
        + weights.diversity * transformation_diversity
        + weights.dependency_impact * _normalize(dependency_impact_mean, _DEPENDENCY_IMPACT_NORM_CAP)
        + weights.interaction * interaction
        + weights.compounding * _normalize(compounding_count, _COMPOUNDING_NORM_CAP)
    ) / weight_total

    return ComplexityProfile(
        baseline_estate_id=ledger.baseline_estate_id,
        trajectory_seed=ledger.trajectory_seed,
        profile_name=ledger.profile_name,
        mutation_count=mutation_count,
        distinct_issue_count=distinct_issue_count,
        compounding_count=compounding_count,
        max_compounding_depth=max_compounding_depth,
        severity_mean=severity_mean,
        severity_max=severity_max,
        affected_entity_count=affected_entity_count,
        transformation_diversity=transformation_diversity,
        transformation_types_used=types_used,
        dependency_impact_mean=dependency_impact_mean,
        dependency_impact_max=dependency_impact_max,
        interaction_score=interaction,
        complexity_score=complexity_score,
        weights_used=weights,
    )


@dataclass
class TrajectoryPerformanceRecord:
    """One row for the future cross-report complexity-vs-performance
    analysis (Milestone 6.5's correlate_with_reports). Pairs one
    trajectory's ComplexityProfile with one agent run's metrics/
    calibration results.

    Deliberately includes the FULL, itemized metrics objects, not just
    the two convenience scalars named after your example
    ("agent_accuracy", "calibration_error") -- so this record is never
    the single opaque score your Milestone 6.3 instructions explicitly
    warned against. The two scalars exist only so a future correlation
    pass has something simple to sort/bin by; every number they're
    derived from is also present in full below.
    """

    trajectory_id: str
    complexity_score: float
    mutation_count: int
    agent_accuracy: float | None
    """Convenience scalar matching your example's field name: literally
    category_metrics.f1 (the strict, both-axes-correct detection F1).
    Not a new metric -- a pointer to one already computed below."""
    calibration_error: float | None
    """Convenience scalar: literally calibration.ece (None below the
    sample-size threshold, same as everywhere else ECE appears)."""
    complexity_profile: ComplexityProfile
    category_metrics: ClassificationMetrics
    category_metrics_by_type: dict[str, ClassificationMetrics]
    entity_metrics: EntityLocalizationMetrics
    calibration: CalibrationResult
    performance_record_schema_version: str = PERFORMANCE_RECORD_SCHEMA_VERSION


def build_trajectory_performance_record(
    trajectory_id: str,
    complexity_profile: ComplexityProfile,
    category_metrics: ClassificationMetrics,
    category_metrics_by_type: dict[str, ClassificationMetrics],
    entity_metrics: EntityLocalizationMetrics,
    calibration_result: CalibrationResult,
) -> TrajectoryPerformanceRecord:
    """Assemble one TrajectoryPerformanceRecord from already-computed
    pieces. Performs no computation of its own beyond reading two
    convenience scalars off its inputs -- everything meaningful was
    already computed by compute_trajectory_complexity() (6.1) and
    metrics.py/calibration.py (6.3)."""
    return TrajectoryPerformanceRecord(
        trajectory_id=trajectory_id,
        complexity_score=complexity_profile.complexity_score,
        mutation_count=complexity_profile.mutation_count,
        agent_accuracy=category_metrics.f1,
        calibration_error=calibration_result.ece,
        complexity_profile=complexity_profile,
        category_metrics=category_metrics,
        category_metrics_by_type=category_metrics_by_type,
        entity_metrics=entity_metrics,
        calibration=calibration_result,
    )
