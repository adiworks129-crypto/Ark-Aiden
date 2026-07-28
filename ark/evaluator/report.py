"""
Evaluation report assembly — Milestone 6.4.

This module introduces NO new metrics, scoring rules, or matching
behavior. Its only job is to combine the outputs Milestones 6.1-6.3
already compute into one coherent, reproducible, serializable
`EvaluationReport` — the artifact that turns Ark from a collection of
evaluation utilities into an experiment framework (see
Ark_Evaluator_Design.md's Milestone 6.4 note for the philosophy this is
built on: Ark isn't just asking "did the agent find the bug," it's
measuring how an agent behaves as controlled synthetic complexity and
drift increase, and this report is the unit of data that measurement is
built from).

Isolation boundary (unchanged from every prior evaluator milestone): the
report generator (this module, plus orchestrator.py) has full access to
ground truth, the mutation ledger, the derived Issues, and every
evaluation result -- because it runs entirely on Ark's side, after the
evaluated agent has already produced its output. The agent itself never
sees any of this. Nothing here relaxes that boundary in either direction.

Technology independence: this module reads a rendering manifest as a
plain dict via `.get(...)`, never assuming a specific adapter's shape
beyond the couple of keys MuleSoft's adapter happens to expose today
(`artifacts`, `dependencies`, `adapter`, `adapter_version`) -- and it never
imports `ark.adapters` (of any kind, generic or MuleSoft) at all. A future
adapter that doesn't expose those keys degrades this report's
Environment/Metadata fields to `None` rather than crashing; flagged here
rather than silently assumed to generalize.

`rendering_validation` (added when the HTTP connector validator was wired
into the pipeline) is the one exception worth calling out explicitly: it
DOES import a concrete type, `RenderingValidationSummary`, from
`ark.validation.pipeline` -- still never `ark.adapters` (the promise above
is unbroken), but no longer purely generic either, since that type's
existence is currently tied to one specific connector's validator. This is
a deliberate, additive, sibling field -- see its own field docstring below
-- kept structurally separate from `agent_performance` for exactly the
reason the isolation boundary above exists: rendering-quality data must
never be able to influence agent-performance metrics, so it is never read
by anything in this module beyond being stored and serialized verbatim.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ark.core.models import GroundTruthEstate
from ark.evaluator.calibration import CALIBRATION_SCHEMA_VERSION, CalibrationResult
from ark.evaluator.complexity import (
    COMPLEXITY_SCHEMA_VERSION,
    ComplexityProfile,
    ComplexityWeights,
)
from ark.evaluator.explanation import EXPLANATION_SIGNALS_SCHEMA_VERSION, ExplanationSignals
from ark.evaluator.issues import (
    ISSUE_SCHEMA_VERSION,
    Issue,
    IssueDerivationDiagnostics,
    TransformationHistoryEntry,
)
from ark.evaluator.matcher import FindingMatchResult
from ark.evaluator.metrics import (
    METRICS_SCHEMA_VERSION,
    ClassificationMetrics,
    EntityLocalizationMetrics,
    is_true_positive,
)
from ark.evaluator.schema import AGENT_OUTPUT_SCHEMA_VERSION
from ark.mutation.ledger import MutationLedger
from ark.validation.pipeline import RenderingValidationSummary

REPORT_SCHEMA_VERSION = "0.1.0"

DEFAULT_OVERCONFIDENCE_THRESHOLD = 0.7
"""Confidence at or above this, on a finding that is NOT a true positive
(metrics.is_true_positive), is flagged in Failure Analysis as an
overconfidence pattern. A documented, overridable default -- not a hidden
constant -- matching the same discipline ComplexityWeights already uses."""

_SEVERITY_LOW_MAX = 0.34
_SEVERITY_MEDIUM_MAX = 0.67
"""Descriptive bucketing only, for the Issue Summary's severity
breakdown -- not a scoring rule, and not used anywhere in matching or
metrics. Thresholds split the 0-1 severity range into three roughly equal
bands; documented here so the bucketing is auditable, not implicit."""


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------


@dataclass
class EvaluationMetadata:
    report_schema_version: str
    generated_at: str
    """ISO8601 UTC timestamp. The ONE field expected to differ between two
    otherwise-identical evaluation runs -- see the Milestone 6.4 test
    proving every other field is deterministic given the same inputs."""
    trajectory_id: str
    baseline_estate_id: str
    profile_name: str
    trajectory_seed: int
    engine_version: str
    baseline_schema_version: str
    ledger_schema_version: str
    issue_schema_version: str
    agent_output_schema_version: str
    metrics_schema_version: str
    calibration_schema_version: str
    explanation_signals_schema_version: str
    complexity_schema_version: str
    generator_version: str | None = None
    """Only known if a GenerationManifest was passed to evaluate() -- None
    for hand-authored ground truth (e.g. Milestone 1's example estate),
    which has no generator provenance at all."""
    adapter_name: str | None = None
    adapter_version: str | None = None
    """Read defensively from the manifest dict (`.get("adapter")` /
    `.get("adapter_version")`) -- MuleSoft's adapter's own convention, not
    a guaranteed contract; None if absent."""


@dataclass
class EnvironmentSummary:
    application_count: int
    api_count: int
    flow_count: int
    dependency_count: int | None
    """None if the manifest doesn't expose a "dependencies" list -- see
    module docstring's technology-independence note."""
    artifact_count: int | None
    """None if the manifest doesn't expose an "artifacts" list."""


@dataclass
class TransformationSummary:
    total_mutations: int
    distinct_transformation_types: list[str]
    severity_mean: float
    severity_max: float
    affected_entity_count: int
    complexity_score: float
    complexity_profile: ComplexityProfile
    """The full, itemized complexity vector -- included in addition to the
    convenience scalars above so nothing about how complexity_score was
    derived is hidden behind one number."""


@dataclass
class IssueSummary:
    total_observable_issues: int
    issues_by_type: dict[str, int]
    issues_by_severity_bucket: dict[str, int]
    """Counts under "low" (< 0.34) / "medium" (< 0.67) / "high" (>= 0.67)
    -- a descriptive bucketing for readability, not a new scoring rule."""
    compounding_issue_count: int
    """Issues whose mutation_count > 1 -- i.e. more than one raw ledger
    record compounded into this single observable Issue."""
    net_zero_transformation_count: int
    """From issues.py's derive_issue_diagnostics(): raw mutation groups
    that cancelled out to no observable difference (e.g. a
    dependency_change reverted later in the same trajectory) and so never
    became a scoreable Issue at all."""
    net_zero_groups: list[dict] = field(default_factory=list)
    """One {"transformation_type", "affected_entity_ids"} entry per
    cancelled-out group, for audit."""


@dataclass
class AgentPerformanceSummary:
    total_findings: int
    category_metrics: ClassificationMetrics
    category_metrics_by_type: dict[str, ClassificationMetrics]
    entity_localization_metrics: EntityLocalizationMetrics
    calibration: CalibrationResult
    explanation_signals: list[ExplanationSignals]
    confidence_distribution: dict[str, int]
    """Ten confidence bins ("0.0-0.1" .. "0.9-1.0") -> finding count. A
    plain descriptive histogram, computed independently of (and never
    substituting for) calibration.py's ECE binning."""


@dataclass
class FailureAnalysisEntry:
    finding_id: str | None
    """None for missed_issues -- there was no finding to reference at
    all."""
    issue_id: str | None
    claimed_issue_type: str | None
    actual_issue_type: str | None
    confidence: float | None
    detail: str


@dataclass
class FailureAnalysis:
    missed_issues: list[FailureAnalysisEntry]
    """Real Issues with no true-positive claim against them (metrics.py's
    is_true_positive) -- false negatives, explained."""
    hallucinated_findings: list[FailureAnalysisEntry]
    """Findings whose entity never resolved to a real, affected entity at
    all (matched_issue_id is None)."""
    wrong_category_predictions: list[FailureAnalysisEntry]
    """Findings whose claimed issue_type doesn't correspond to ANY real
    issue anywhere in this estate (matcher.py's coarse category_correct is
    False) -- a "made up a category" error."""
    correct_location_incorrect_diagnosis: list[FailureAnalysisEntry]
    """A narrower, distinct bucket from the one above: findings that DID
    resolve to the exact right entity (entity_correct True) but named the
    wrong issue_type for it -- "right place, wrong diagnosis.\""""
    overconfidence_patterns: list[FailureAnalysisEntry]
    """Findings with confidence >= overconfidence_threshold that are NOT
    true positives -- confident mistakes, the case Ark_Evaluator_Design.md
    Section 4.3 specifically calls out as worse than uncertain ones."""
    overconfidence_threshold: float


@dataclass
class ResearchAnalysisHooks:
    """Convenience pointers for future cross-report analysis (Milestone
    6.5). No correlation is computed here -- every value is already
    present in full elsewhere in this same report; this section exists so
    a future batch script can pull exactly what it needs from one place
    without re-deriving it from the nested sections above."""

    complexity_score: float
    mutation_count: int
    transformation_type_combination: list[str]
    """Answers "are some mutation combinations disproportionately
    difficult" -- the exact set of transformation types this one
    trajectory realized, for grouping reports by combination later."""
    category_f1: float | None
    entity_localization_accuracy: float | None
    calibration_ece: float | None
    category_metrics_by_type: dict[str, ClassificationMetrics]
    """Answers "which transformation operators are hardest" once binned
    across many reports."""


@dataclass
class EvaluationReport:
    metadata: EvaluationMetadata
    environment_summary: EnvironmentSummary
    transformation_summary: TransformationSummary
    issue_summary: IssueSummary
    agent_performance: AgentPerformanceSummary
    failure_analysis: FailureAnalysis
    research_hooks: ResearchAnalysisHooks
    issues: list[Issue]
    """The full derived Issue list this report scored against, verbatim --
    for audit (Ark_Evaluator_Design.md Section 5.4's "ledger_issues,"
    renamed here to match this module's terminology)."""
    raw_agent_output: dict
    """The agent's output exactly as given to evaluate() -- verbatim, for
    audit. Never embellished, never re-derived; a straight passthrough."""
    rendering_validation: RenderingValidationSummary | None = None
    """Additive, sibling field to `agent_performance` -- NOT part of it,
    and never read by anything that computes `agent_performance`,
    `research_hooks`, or `failure_analysis`. Whether the rendered estate
    is documentation-valid (per ark.validation.mulesoft_http_connector) is
    a property of the generation/rendering step, not of the agent being
    evaluated -- mixing the two would corrupt hand-verified-correct agent
    metrics with a concern that has nothing to do with agent performance.
    None if the caller running evaluate() didn't pass a
    RenderingValidationSummary (e.g. an adapter with no wired validator,
    or a historical report from before this field existed) -- never
    silently defaulted to some other value that could be mistaken for a
    real "no issues found" result."""


# ---------------------------------------------------------------------------
# Section builders (pure functions -- no computation beyond bucketing/
# reading fields already computed by 6.1-6.3)
# ---------------------------------------------------------------------------


def _metadata(
    ledger: MutationLedger,
    trajectory_id: str,
    manifest: dict,
    generation_manifest: Any | None,
    generated_at: str | None,
) -> EvaluationMetadata:
    return EvaluationMetadata(
        report_schema_version=REPORT_SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        trajectory_id=trajectory_id,
        baseline_estate_id=ledger.baseline_estate_id,
        profile_name=ledger.profile_name,
        trajectory_seed=ledger.trajectory_seed,
        engine_version=ledger.engine_version,
        baseline_schema_version=ledger.baseline_schema_version,
        ledger_schema_version=ledger.ledger_schema_version,
        issue_schema_version=ISSUE_SCHEMA_VERSION,
        agent_output_schema_version=AGENT_OUTPUT_SCHEMA_VERSION,
        metrics_schema_version=METRICS_SCHEMA_VERSION,
        calibration_schema_version=CALIBRATION_SCHEMA_VERSION,
        explanation_signals_schema_version=EXPLANATION_SIGNALS_SCHEMA_VERSION,
        complexity_schema_version=COMPLEXITY_SCHEMA_VERSION,
        generator_version=getattr(generation_manifest, "generator_version", None),
        adapter_name=manifest.get("adapter"),
        adapter_version=manifest.get("adapter_version"),
    )


def _environment_summary(estate: GroundTruthEstate, manifest: dict) -> EnvironmentSummary:
    artifacts = manifest.get("artifacts")
    dependencies = manifest.get("dependencies")
    return EnvironmentSummary(
        application_count=len(estate.applications),
        api_count=sum(len(app.apis) for app in estate.applications),
        flow_count=sum(len(app.flows) for app in estate.applications),
        artifact_count=len(artifacts) if isinstance(artifacts, list) else None,
        dependency_count=len(dependencies) if isinstance(dependencies, list) else None,
    )


def _transformation_summary(complexity_profile: ComplexityProfile) -> TransformationSummary:
    return TransformationSummary(
        total_mutations=complexity_profile.mutation_count,
        distinct_transformation_types=list(complexity_profile.transformation_types_used),
        severity_mean=complexity_profile.severity_mean,
        severity_max=complexity_profile.severity_max,
        affected_entity_count=complexity_profile.affected_entity_count,
        complexity_score=complexity_profile.complexity_score,
        complexity_profile=complexity_profile,
    )


def _severity_bucket(severity: float) -> str:
    if severity < _SEVERITY_LOW_MAX:
        return "low"
    if severity < _SEVERITY_MEDIUM_MAX:
        return "medium"
    return "high"


def _issue_summary(issues: list[Issue], diagnostics: IssueDerivationDiagnostics) -> IssueSummary:
    issues_by_type: dict[str, int] = {}
    issues_by_severity_bucket = {"low": 0, "medium": 0, "high": 0}
    compounding_issue_count = 0
    for issue in issues:
        issues_by_type[issue.issue_type] = issues_by_type.get(issue.issue_type, 0) + 1
        issues_by_severity_bucket[_severity_bucket(issue.severity)] += 1
        if issue.mutation_count > 1:
            compounding_issue_count += 1

    return IssueSummary(
        total_observable_issues=len(issues),
        issues_by_type=issues_by_type,
        issues_by_severity_bucket=issues_by_severity_bucket,
        compounding_issue_count=compounding_issue_count,
        net_zero_transformation_count=diagnostics.net_zero_group_count,
        net_zero_groups=diagnostics.net_zero_groups,
    )


def _confidence_distribution(matches: list[FindingMatchResult]) -> dict[str, int]:
    bin_labels = [f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10)]
    bins = {label: 0 for label in bin_labels}
    for match in matches:
        index = min(int(match.confidence * 10), 9)
        bins[bin_labels[index]] += 1
    return bins


def _agent_performance_summary(
    matches: list[FindingMatchResult],
    category_metrics: ClassificationMetrics,
    category_metrics_by_type: dict[str, ClassificationMetrics],
    entity_metrics: EntityLocalizationMetrics,
    calibration_result: CalibrationResult,
    explanation_signals: list[ExplanationSignals],
) -> AgentPerformanceSummary:
    return AgentPerformanceSummary(
        total_findings=len(matches),
        category_metrics=category_metrics,
        category_metrics_by_type=category_metrics_by_type,
        entity_localization_metrics=entity_metrics,
        calibration=calibration_result,
        explanation_signals=explanation_signals,
        confidence_distribution=_confidence_distribution(matches),
    )


def _failure_analysis(
    matches: list[FindingMatchResult], issues: list[Issue], overconfidence_threshold: float
) -> FailureAnalysis:
    issues_by_id = {issue.issue_id: issue for issue in issues}

    found_true_positive_ids = {
        match.matched_issue_id for match in matches if is_true_positive(match, issues_by_id)
    }
    missed_issues = [
        FailureAnalysisEntry(
            finding_id=None,
            issue_id=issue.issue_id,
            claimed_issue_type=None,
            actual_issue_type=issue.issue_type,
            confidence=None,
            detail=(
                f"No finding correctly identified this '{issue.issue_type}' issue "
                f"on {issue.affected_entity_ids}."
            ),
        )
        for issue in issues
        if issue.issue_id not in found_true_positive_ids
    ]

    hallucinated_findings = [
        FailureAnalysisEntry(
            finding_id=match.finding_id,
            issue_id=None,
            claimed_issue_type=match.claimed_issue_type,
            actual_issue_type=None,
            confidence=match.confidence,
            detail=(
                f"Claimed '{match.claimed_issue_type}' on entity_reference="
                f"'{match.entity_reference}' in '{match.artifact_reference}', but no real "
                f"issue exists there."
            ),
        )
        for match in matches
        if match.matched_issue_id is None
    ]

    wrong_category_predictions = [
        FailureAnalysisEntry(
            finding_id=match.finding_id,
            issue_id=match.matched_issue_id,
            claimed_issue_type=match.claimed_issue_type,
            actual_issue_type=(
                issues_by_id[match.matched_issue_id].issue_type if match.matched_issue_id else None
            ),
            confidence=match.confidence,
            detail=(
                f"Claimed issue_type '{match.claimed_issue_type}' does not match any real "
                f"issue type present in this estate."
            ),
        )
        for match in matches
        if not match.category_correct
    ]

    correct_location_incorrect_diagnosis = [
        FailureAnalysisEntry(
            finding_id=match.finding_id,
            issue_id=match.matched_issue_id,
            claimed_issue_type=match.claimed_issue_type,
            actual_issue_type=issues_by_id[match.matched_issue_id].issue_type,
            confidence=match.confidence,
            detail=(
                f"Correctly localized '{match.matched_issue_id}' but claimed "
                f"'{match.claimed_issue_type}' instead of the real issue_type "
                f"'{issues_by_id[match.matched_issue_id].issue_type}'."
            ),
        )
        for match in matches
        if match.entity_correct
        and match.matched_issue_id is not None
        and not is_true_positive(match, issues_by_id)
    ]

    overconfidence_patterns = [
        FailureAnalysisEntry(
            finding_id=match.finding_id,
            issue_id=match.matched_issue_id,
            claimed_issue_type=match.claimed_issue_type,
            actual_issue_type=(
                issues_by_id[match.matched_issue_id].issue_type if match.matched_issue_id else None
            ),
            confidence=match.confidence,
            detail=(
                f"Confidence {match.confidence:.2f} on an incorrect finding "
                f"(>= {overconfidence_threshold} threshold)."
            ),
        )
        for match in matches
        if match.confidence >= overconfidence_threshold and not is_true_positive(match, issues_by_id)
    ]

    return FailureAnalysis(
        missed_issues=missed_issues,
        hallucinated_findings=hallucinated_findings,
        wrong_category_predictions=wrong_category_predictions,
        correct_location_incorrect_diagnosis=correct_location_incorrect_diagnosis,
        overconfidence_patterns=overconfidence_patterns,
        overconfidence_threshold=overconfidence_threshold,
    )


def _research_hooks(
    complexity_profile: ComplexityProfile,
    category_metrics: ClassificationMetrics,
    entity_metrics: EntityLocalizationMetrics,
    calibration_result: CalibrationResult,
    category_metrics_by_type: dict[str, ClassificationMetrics],
) -> ResearchAnalysisHooks:
    return ResearchAnalysisHooks(
        complexity_score=complexity_profile.complexity_score,
        mutation_count=complexity_profile.mutation_count,
        transformation_type_combination=list(complexity_profile.transformation_types_used),
        category_f1=category_metrics.f1,
        entity_localization_accuracy=entity_metrics.localization_accuracy,
        calibration_ece=calibration_result.ece,
        category_metrics_by_type=category_metrics_by_type,
    )


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def assemble_report(
    *,
    transformed_estate: GroundTruthEstate,
    ledger: MutationLedger,
    manifest: dict,
    raw_agent_output: dict,
    issues: list[Issue],
    issue_diagnostics: IssueDerivationDiagnostics,
    matches: list[FindingMatchResult],
    category_metrics: ClassificationMetrics,
    category_metrics_by_type: dict[str, ClassificationMetrics],
    entity_metrics: EntityLocalizationMetrics,
    calibration_result: CalibrationResult,
    explanation_signals: list[ExplanationSignals],
    complexity_profile: ComplexityProfile,
    trajectory_id: str,
    generation_manifest: Any | None = None,
    overconfidence_threshold: float = DEFAULT_OVERCONFIDENCE_THRESHOLD,
    generated_at: str | None = None,
    rendering_validation: RenderingValidationSummary | None = None,
) -> EvaluationReport:
    """Assemble one EvaluationReport from already-computed pieces.
    Performs no scoring of its own -- every number here was already
    produced by ark.evaluator.{issues,metrics,calibration,explanation,
    complexity}; this function only buckets/labels/packages them.

    `rendering_validation`, if given, is stored on the report verbatim as
    its own sibling field -- never read here, never folded into any of the
    sections built above. See EvaluationReport.rendering_validation's own
    docstring for why that separation matters.

    Called by orchestrator.py's evaluate(), which is responsible for
    actually running the 6.1-6.3 pipeline to produce these arguments in
    the first place -- see that module for the single end-to-end entry
    point most callers should use instead of calling this directly.
    """
    return EvaluationReport(
        metadata=_metadata(ledger, trajectory_id, manifest, generation_manifest, generated_at),
        environment_summary=_environment_summary(transformed_estate, manifest),
        transformation_summary=_transformation_summary(complexity_profile),
        issue_summary=_issue_summary(issues, issue_diagnostics),
        agent_performance=_agent_performance_summary(
            matches, category_metrics, category_metrics_by_type, entity_metrics,
            calibration_result, explanation_signals,
        ),
        failure_analysis=_failure_analysis(matches, issues, overconfidence_threshold),
        research_hooks=_research_hooks(
            complexity_profile, category_metrics, entity_metrics, calibration_result,
            category_metrics_by_type,
        ),
        rendering_validation=rendering_validation,
        issues=issues,
        raw_agent_output=raw_agent_output,
    )


# ---------------------------------------------------------------------------
# Serialization (one-directional to_dict/to_json, matching
# ark/mutation/ledger.py's existing precedent) plus a lightweight
# from_dict reconstructor -- new for Milestone 6.4, since Ark is now
# expected to reload historical reports as an experiment framework, not
# just write them out for a human to read once.
# ---------------------------------------------------------------------------


def report_to_dict(report: EvaluationReport) -> dict:
    return dataclasses.asdict(report)


def report_to_json(report: EvaluationReport, indent: int | None = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent)


def _transformation_history_entry_from_dict(data: dict) -> TransformationHistoryEntry:
    return TransformationHistoryEntry(**data)


def _issue_from_dict(data: dict) -> Issue:
    return Issue(
        issue_id=data["issue_id"],
        issue_type=data["issue_type"],
        affected_entity_ids=data["affected_entity_ids"],
        observable_symptom=data["observable_symptom"],
        severity=data["severity"],
        expected_detection_target=data["expected_detection_target"],
        transformation_history=[
            _transformation_history_entry_from_dict(h) for h in data.get("transformation_history", [])
        ],
        issue_schema_version=data.get("issue_schema_version", ISSUE_SCHEMA_VERSION),
    )


def _calibration_result_from_dict(data: dict) -> CalibrationResult:
    restored = dict(data)
    restored["calibration_pairs"] = [tuple(pair) for pair in data.get("calibration_pairs", [])]
    return CalibrationResult(**restored)


def _complexity_profile_from_dict(data: dict) -> ComplexityProfile:
    restored = dict(data)
    restored["transformation_types_used"] = tuple(data.get("transformation_types_used", []))
    restored["weights_used"] = ComplexityWeights(**data["weights_used"])
    return ComplexityProfile(**restored)


def _transformation_summary_from_dict(data: dict) -> TransformationSummary:
    restored = dict(data)
    restored["complexity_profile"] = _complexity_profile_from_dict(data["complexity_profile"])
    return TransformationSummary(**restored)


def _agent_performance_from_dict(data: dict) -> AgentPerformanceSummary:
    restored = dict(data)
    restored["category_metrics"] = ClassificationMetrics(**data["category_metrics"])
    restored["category_metrics_by_type"] = {
        issue_type: ClassificationMetrics(**metrics)
        for issue_type, metrics in data["category_metrics_by_type"].items()
    }
    restored["entity_localization_metrics"] = EntityLocalizationMetrics(**data["entity_localization_metrics"])
    restored["calibration"] = _calibration_result_from_dict(data["calibration"])
    restored["explanation_signals"] = [ExplanationSignals(**s) for s in data["explanation_signals"]]
    return AgentPerformanceSummary(**restored)


def _failure_analysis_from_dict(data: dict) -> FailureAnalysis:
    restored = dict(data)
    for key in (
        "missed_issues", "hallucinated_findings", "wrong_category_predictions",
        "correct_location_incorrect_diagnosis", "overconfidence_patterns",
    ):
        restored[key] = [FailureAnalysisEntry(**entry) for entry in data[key]]
    return FailureAnalysis(**restored)


def _research_hooks_from_dict(data: dict) -> ResearchAnalysisHooks:
    restored = dict(data)
    restored["category_metrics_by_type"] = {
        issue_type: ClassificationMetrics(**metrics)
        for issue_type, metrics in data["category_metrics_by_type"].items()
    }
    return ResearchAnalysisHooks(**restored)


def _rendering_validation_from_dict(data: dict | None) -> RenderingValidationSummary | None:
    if data is None:
        return None
    return RenderingValidationSummary(**data)


def report_from_dict(data: dict) -> EvaluationReport:
    """Reconstruct an EvaluationReport from a dict produced by
    report_to_dict()/json.loads(report_to_json(...)).

    Deliberately lightweight: assumes the input has exactly the shape
    report_to_dict() produces. No schema migration across
    report_schema_version changes, no validation beyond what plain
    dataclass construction already gives you (a missing/extra key raises
    a normal TypeError) -- if that's ever needed, it's a deliberately
    separate, larger piece of work, not something this function silently
    grows into.

    One deliberate exception to that "assumes exactly the shape" rule:
    `rendering_validation` is read with `.get(...)`, not `data[...]`, since
    it's a new, additive field -- a report serialized before this field
    existed simply has no such key, and should still reconstruct cleanly
    with `rendering_validation=None`, exactly like a report from a caller
    that never passed one to assemble_report() in the first place.
    """
    return EvaluationReport(
        metadata=EvaluationMetadata(**data["metadata"]),
        environment_summary=EnvironmentSummary(**data["environment_summary"]),
        transformation_summary=_transformation_summary_from_dict(data["transformation_summary"]),
        issue_summary=IssueSummary(**data["issue_summary"]),
        agent_performance=_agent_performance_from_dict(data["agent_performance"]),
        failure_analysis=_failure_analysis_from_dict(data["failure_analysis"]),
        research_hooks=_research_hooks_from_dict(data["research_hooks"]),
        issues=[_issue_from_dict(i) for i in data["issues"]],
        raw_agent_output=data["raw_agent_output"],
        rendering_validation=_rendering_validation_from_dict(data.get("rendering_validation")),
    )
