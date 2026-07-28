"""
Cross-report experiment analysis — Milestone 6.5.

This module introduces NO new metrics, scoring rules, or matching
behavior. It is a pure consumer of already-assembled `EvaluationReport`
objects (Milestone 6.4) — it never reads a ground-truth estate, a
mutation ledger, or a rendering manifest directly, and it never imports
`ark.adapters` at all. Its only job is to aggregate what many individual
reports already measured, to answer Ark's founding research questions:

1. Does agent performance degrade as controlled synthetic drift increases?
   -> ComplexityAnalysis: performance averaged per complexity band, plus
      a correlation coefficient between complexity_score and each metric.
2. Which transformation operators produce the largest degradation?
   -> TransformationImpactAnalysis.by_transformation_type, sorted
      worst-first, each with its own observed/baseline/degradation triple.
3. Does confidence calibration worsen as complexity increases?
   -> CalibrationDriftAnalysis: average stated confidence vs. average
      accuracy per complexity band, plus the same complexity/ECE
      correlation surfaced in ComplexityAnalysis.
4. Are certain transformation COMBINATIONS disproportionately difficult?
   -> TransformationImpactAnalysis.by_transformation_combination, grouped
      by the exact set of transformation types realized in a trajectory
      (not just single types).

Everything here measures OBSERVED association over the reports actually
provided, never causation — every CorrelationStatistic carries its own
sample size and a fixed disclaimer, and every average is null (never a
misleading zero) when the reports on hand don't have enough non-null
values to support it, mirroring the same discipline calibration.py's ECE
already established at the single-report level.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ark.evaluator.report import EvaluationReport, report_from_dict

ANALYSIS_SCHEMA_VERSION = "0.1.0"

DEFAULT_COMPLEXITY_BUCKET_COUNT = 5
"""Fixed, equal-width bands across complexity_score's guaranteed [0, 1]
range (a weighted average of already-normalized terms -- see
complexity.py). Deliberately fixed/absolute rather than derived from the
min/max complexity_score actually observed in a given batch: "the
0.4-0.6 band" should mean the same thing across different experiment
runs, not be redefined by whatever range happens to be in one batch."""

DEFAULT_MIN_SAMPLE_SIZE_FOR_CORRELATION = 5
"""Same discipline as calibration.py's DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE:
a correlation coefficient computed from a handful of reports is noisy and
easy to over-read. Below this many (non-null) data points, correlation
fields are None, not a real-looking number."""

CORRELATION_DISCLAIMER = (
    "Pearson correlation coefficient over the reports actually provided in "
    "this batch. This is an observed association, not a causal claim, and "
    "is not adjusted for confounding factors (e.g. mutation_count and "
    "severity tend to rise together, so either could be 'the' driver of "
    "any association seen here)."
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExperimentSummary:
    trajectory_count: int
    average_complexity_score: float | None
    average_category_f1: float | None
    average_entity_localization_accuracy: float | None
    average_calibration_ece: float | None
    transformation_type_distribution: dict[str, int]
    """transformation_type -> number of trajectories whose realized
    transformation_type_combination included it at least once (a
    trajectory using 3 types contributes to all 3 counts)."""


@dataclass
class ComplexityBucket:
    label: str
    lower_bound: float
    upper_bound: float
    trajectory_count: int
    average_category_f1: float | None
    average_entity_localization_accuracy: float | None
    average_calibration_ece: float | None


@dataclass
class CorrelationStatistic:
    metric_name: str
    correlation_with_complexity_score: float | None
    """Pearson r in [-1, 1]. None if fewer than min_sample_size non-null
    (complexity_score, metric) pairs were available, or if either series
    has zero variance (correlation is mathematically undefined, not 0)."""
    sample_size: int
    min_sample_size: int
    disclaimer: str = CORRELATION_DISCLAIMER


@dataclass
class ComplexityAnalysis:
    buckets: list[ComplexityBucket]
    correlations: list[CorrelationStatistic]
    """One entry per performance metric (category_f1,
    entity_localization_accuracy, calibration_ece), correlated against
    complexity_score."""


@dataclass
class AggregatedPerformance:
    """A plain average-of-reports bundle, reused for both a single
    transformation type/combination's "observed" performance and for the
    clean-baseline comparison point -- the same shape, so comparing them
    is always apples-to-apples."""

    trajectory_count: int
    average_category_f1: float | None
    average_entity_localization_accuracy: float | None
    average_calibration_ece: float | None


@dataclass
class TransformationTypePerformance:
    transformation_type: str
    observed: AggregatedPerformance
    baseline: AggregatedPerformance | None
    """The clean (mutation_count == 0) trajectories' aggregate performance
    from THIS SAME batch. None if the batch contains no clean trajectories
    at all -- degradation is then unmeasurable, not assumed to be zero."""
    category_f1_degradation: float | None
    """baseline.average_category_f1 - observed.average_category_f1.
    POSITIVE means observed is WORSE (lower) than the clean baseline --
    i.e. positive = real degradation. None if baseline is None or either
    average is None."""
    entity_localization_degradation: float | None
    """Same sign convention as above: positive = worse than baseline."""
    calibration_ece_degradation: float | None
    """observed.average_calibration_ece - baseline.average_calibration_ece.
    Note the REVERSED subtraction order vs. the two fields above -- ECE is
    lower-is-better, so positive here also means "worse than baseline"
    (a HIGHER, i.e. worse, calibration error than the clean case),
    matching the same positive-means-degradation convention throughout
    this module."""


@dataclass
class TransformationCombinationPerformance:
    transformation_types: list[str]
    """The exact, sorted set of transformation types realized together in
    this group of trajectories -- answers "are certain COMBINATIONS
    disproportionately difficult," not just single types."""
    observed: AggregatedPerformance
    baseline: AggregatedPerformance | None
    category_f1_degradation: float | None
    entity_localization_degradation: float | None
    calibration_ece_degradation: float | None


@dataclass
class TransformationImpactAnalysis:
    baseline: AggregatedPerformance | None
    """The clean-baseline aggregate used for every degradation field
    below -- surfaced once here too, so it's never implicit."""
    by_transformation_type: list[TransformationTypePerformance]
    """Sorted worst-first (largest category_f1_degradation first; entries
    with no measurable degradation, because there's no baseline or no
    data, sort last) -- directly answers "which transformation operators
    produce the largest degradation."""
    by_transformation_combination: list[TransformationCombinationPerformance]
    """Same sort order, over exact transformation-type combinations
    rather than single types -- answers "are some combinations
    disproportionately difficult," distinct from the single-type
    breakdown above."""


@dataclass
class CalibrationDriftPoint:
    complexity_bucket_label: str
    trajectory_count: int
    average_confidence: float | None
    """Approximated from each report's confidence_distribution histogram
    (bin-midpoint weighted average) -- not a new calibration metric, a
    plain descriptive statistic over data calibration.py already produced
    per report."""
    average_category_f1: float | None
    """Used here as the "actual correctness" side of the confidence-vs-
    correctness comparison -- the same number ComplexityAnalysis already
    reports per bucket, repeated here for direct side-by-side reading."""
    average_brier_score: float | None
    average_ece: float | None
    confidence_minus_accuracy_gap: float | None
    """average_confidence - average_category_f1. Positive = agents in this
    complexity band are, on average, more confident than they are
    correct (overconfidence); negative = underconfident. None if either
    side is unavailable."""


@dataclass
class CalibrationDriftAnalysis:
    points: list[CalibrationDriftPoint]
    """Ordered by increasing complexity band -- read top-to-bottom to see
    whether the confidence/accuracy gap widens as complexity rises."""
    correlation_between_complexity_and_ece: CorrelationStatistic
    """The same calibration_ece correlation already computed in
    ComplexityAnalysis.correlations -- referenced here, not recomputed,
    so there is exactly one number for this relationship, not two that
    could quietly drift apart."""


@dataclass
class ExperimentAnalysis:
    analysis_schema_version: str
    generated_at: str
    """ISO8601 UTC timestamp -- the one field expected to differ between
    two otherwise-identical analysis runs, same convention as
    EvaluationReport.metadata.generated_at."""
    report_count: int
    skipped_report_count: int
    """How many report files load_reports_from_files() couldn't load
    (missing, unreadable, malformed) -- 0 if analyze_reports() was called
    directly with an in-memory report list."""
    experiment_summary: ExperimentSummary
    complexity_analysis: ComplexityAnalysis
    transformation_impact_analysis: TransformationImpactAnalysis
    calibration_drift_analysis: CalibrationDriftAnalysis


# ---------------------------------------------------------------------------
# Small numeric helpers (no dependency beyond stdlib)
# ---------------------------------------------------------------------------


def _average(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Hand-rolled rather than statistics.correlation (Python 3.10+ only,
    and this project stays zero-dependency and broadly version-portable).
    Returns None (never 0.0) when undefined: fewer than 2 points, or
    either series has zero variance."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x == 0 or variance_y == 0:
        return None
    return covariance / (variance_x * variance_y) ** 0.5


def _bucket_boundaries(bucket_count: int) -> list[tuple[float, float]]:
    width = 1.0 / bucket_count
    return [(round(i * width, 4), round((i + 1) * width, 4)) for i in range(bucket_count)]


def _bucket_index_for_score(score: float, bucket_count: int) -> int:
    """Same clamp-into-range convention as calibration.py's ECE binning:
    a score of exactly 1.0 lands in the last bucket rather than
    overflowing; a score outside [0, 1] (possible only with unusual,
    user-overridden negative ComplexityWeights) clamps to the nearest
    valid bucket rather than raising."""
    index = int(score * bucket_count)
    return max(0, min(index, bucket_count - 1))


def _group_reports_by_complexity_bucket(
    reports: list[EvaluationReport], bucket_count: int
) -> list[list[EvaluationReport]]:
    buckets: list[list[EvaluationReport]] = [[] for _ in range(bucket_count)]
    for report in reports:
        index = _bucket_index_for_score(report.research_hooks.complexity_score, bucket_count)
        buckets[index].append(report)
    return buckets


def _confidence_distribution_average(confidence_distribution: dict[str, int]) -> float | None:
    """Bin-midpoint weighted average over a report's confidence_distribution
    histogram (e.g. "0.3-0.4" -> midpoint 0.35). A plain descriptive
    statistic, not a new calibration metric."""
    total = sum(confidence_distribution.values())
    if total == 0:
        return None
    weighted_sum = 0.0
    for label, count in confidence_distribution.items():
        lower_str, upper_str = label.split("-")
        midpoint = (float(lower_str) + float(upper_str)) / 2
        weighted_sum += midpoint * count
    return weighted_sum / total


def _degradation_lower_is_worse(baseline_value: float | None, observed_value: float | None) -> float | None:
    """positive = observed is WORSE (lower) than baseline -- for metrics
    where higher is better (category_f1, entity_localization_accuracy)."""
    if baseline_value is None or observed_value is None:
        return None
    return baseline_value - observed_value


def _degradation_higher_is_worse(baseline_value: float | None, observed_value: float | None) -> float | None:
    """positive = observed is WORSE (higher) than baseline -- for metrics
    where lower is better (calibration_ece)."""
    if baseline_value is None or observed_value is None:
        return None
    return observed_value - baseline_value


def _aggregate_performance(reports: list[EvaluationReport]) -> AggregatedPerformance:
    f1_values = [r.research_hooks.category_f1 for r in reports if r.research_hooks.category_f1 is not None]
    localization_values = [
        r.research_hooks.entity_localization_accuracy
        for r in reports
        if r.research_hooks.entity_localization_accuracy is not None
    ]
    ece_values = [
        r.research_hooks.calibration_ece for r in reports if r.research_hooks.calibration_ece is not None
    ]
    return AggregatedPerformance(
        trajectory_count=len(reports),
        average_category_f1=_average(f1_values),
        average_entity_localization_accuracy=_average(localization_values),
        average_calibration_ece=_average(ece_values),
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _experiment_summary(reports: list[EvaluationReport]) -> ExperimentSummary:
    complexity_scores = [r.research_hooks.complexity_score for r in reports]
    f1_values = [r.research_hooks.category_f1 for r in reports if r.research_hooks.category_f1 is not None]
    localization_values = [
        r.research_hooks.entity_localization_accuracy
        for r in reports
        if r.research_hooks.entity_localization_accuracy is not None
    ]
    ece_values = [
        r.research_hooks.calibration_ece for r in reports if r.research_hooks.calibration_ece is not None
    ]

    distribution: dict[str, int] = {}
    for report in reports:
        for transformation_type in report.research_hooks.transformation_type_combination:
            distribution[transformation_type] = distribution.get(transformation_type, 0) + 1

    return ExperimentSummary(
        trajectory_count=len(reports),
        average_complexity_score=_average(complexity_scores),
        average_category_f1=_average(f1_values),
        average_entity_localization_accuracy=_average(localization_values),
        average_calibration_ece=_average(ece_values),
        transformation_type_distribution=distribution,
    )


def _complexity_analysis(
    reports: list[EvaluationReport], bucket_count: int, min_sample_size_for_correlation: int
) -> ComplexityAnalysis:
    boundaries = _bucket_boundaries(bucket_count)
    grouped = _group_reports_by_complexity_bucket(reports, bucket_count)

    buckets: list[ComplexityBucket] = []
    for (lower, upper), bucket_reports in zip(boundaries, grouped):
        buckets.append(
            ComplexityBucket(
                label=f"{lower:.2f}-{upper:.2f}",
                lower_bound=lower,
                upper_bound=upper,
                trajectory_count=len(bucket_reports),
                average_category_f1=_average(
                    [r.research_hooks.category_f1 for r in bucket_reports if r.research_hooks.category_f1 is not None]
                ),
                average_entity_localization_accuracy=_average(
                    [
                        r.research_hooks.entity_localization_accuracy
                        for r in bucket_reports
                        if r.research_hooks.entity_localization_accuracy is not None
                    ]
                ),
                average_calibration_ece=_average(
                    [
                        r.research_hooks.calibration_ece
                        for r in bucket_reports
                        if r.research_hooks.calibration_ece is not None
                    ]
                ),
            )
        )

    correlations: list[CorrelationStatistic] = []
    metric_extractors = (
        ("category_f1", lambda r: r.research_hooks.category_f1),
        ("entity_localization_accuracy", lambda r: r.research_hooks.entity_localization_accuracy),
        ("calibration_ece", lambda r: r.research_hooks.calibration_ece),
    )
    for metric_name, extractor in metric_extractors:
        xs: list[float] = []
        ys: list[float] = []
        for report in reports:
            value = extractor(report)
            if value is not None:
                xs.append(report.research_hooks.complexity_score)
                ys.append(value)

        correlation = (
            _pearson_correlation(xs, ys) if len(xs) >= min_sample_size_for_correlation else None
        )
        correlations.append(
            CorrelationStatistic(
                metric_name=metric_name,
                correlation_with_complexity_score=correlation,
                sample_size=len(xs),
                min_sample_size=min_sample_size_for_correlation,
            )
        )

    return ComplexityAnalysis(buckets=buckets, correlations=correlations)


def _calibration_drift_analysis(
    reports: list[EvaluationReport],
    bucket_count: int,
    complexity_analysis: ComplexityAnalysis,
) -> CalibrationDriftAnalysis:
    boundaries = _bucket_boundaries(bucket_count)
    grouped = _group_reports_by_complexity_bucket(reports, bucket_count)

    points: list[CalibrationDriftPoint] = []
    for (lower, upper), bucket_reports in zip(boundaries, grouped):
        confidences = [
            value
            for value in (
                _confidence_distribution_average(r.agent_performance.confidence_distribution)
                for r in bucket_reports
            )
            if value is not None
        ]
        f1_values = [
            r.research_hooks.category_f1 for r in bucket_reports if r.research_hooks.category_f1 is not None
        ]
        brier_values = [
            r.agent_performance.calibration.brier_score
            for r in bucket_reports
            if r.agent_performance.calibration.brier_score is not None
        ]
        ece_values = [
            r.research_hooks.calibration_ece
            for r in bucket_reports
            if r.research_hooks.calibration_ece is not None
        ]

        average_confidence = _average(confidences)
        average_f1 = _average(f1_values)
        gap = (
            average_confidence - average_f1
            if average_confidence is not None and average_f1 is not None
            else None
        )

        points.append(
            CalibrationDriftPoint(
                complexity_bucket_label=f"{lower:.2f}-{upper:.2f}",
                trajectory_count=len(bucket_reports),
                average_confidence=average_confidence,
                average_category_f1=average_f1,
                average_brier_score=_average(brier_values),
                average_ece=_average(ece_values),
                confidence_minus_accuracy_gap=gap,
            )
        )

    ece_correlation = next(
        c for c in complexity_analysis.correlations if c.metric_name == "calibration_ece"
    )
    return CalibrationDriftAnalysis(points=points, correlation_between_complexity_and_ece=ece_correlation)


def _degradation_sort_key(degradation: float | None) -> tuple[bool, float]:
    """None (unmeasurable degradation) sorts last; otherwise largest
    degradation first."""
    return (degradation is None, -(degradation if degradation is not None else 0.0))


def _transformation_impact_analysis(reports: list[EvaluationReport]) -> TransformationImpactAnalysis:
    clean_reports = [r for r in reports if r.transformation_summary.total_mutations == 0]
    baseline = _aggregate_performance(clean_reports) if clean_reports else None

    by_type_reports: dict[str, list[EvaluationReport]] = {}
    for report in reports:
        for transformation_type in report.research_hooks.transformation_type_combination:
            by_type_reports.setdefault(transformation_type, []).append(report)

    by_transformation_type: list[TransformationTypePerformance] = []
    for transformation_type in sorted(by_type_reports):
        observed = _aggregate_performance(by_type_reports[transformation_type])
        by_transformation_type.append(
            TransformationTypePerformance(
                transformation_type=transformation_type,
                observed=observed,
                baseline=baseline,
                category_f1_degradation=(
                    _degradation_lower_is_worse(baseline.average_category_f1, observed.average_category_f1)
                    if baseline
                    else None
                ),
                entity_localization_degradation=(
                    _degradation_lower_is_worse(
                        baseline.average_entity_localization_accuracy,
                        observed.average_entity_localization_accuracy,
                    )
                    if baseline
                    else None
                ),
                calibration_ece_degradation=(
                    _degradation_higher_is_worse(
                        baseline.average_calibration_ece, observed.average_calibration_ece
                    )
                    if baseline
                    else None
                ),
            )
        )
    by_transformation_type.sort(key=lambda p: _degradation_sort_key(p.category_f1_degradation))

    by_combo_reports: dict[tuple[str, ...], list[EvaluationReport]] = {}
    for report in reports:
        combo = tuple(sorted(report.research_hooks.transformation_type_combination))
        by_combo_reports.setdefault(combo, []).append(report)

    by_transformation_combination: list[TransformationCombinationPerformance] = []
    for combo in sorted(by_combo_reports):
        observed = _aggregate_performance(by_combo_reports[combo])
        by_transformation_combination.append(
            TransformationCombinationPerformance(
                transformation_types=list(combo),
                observed=observed,
                baseline=baseline,
                category_f1_degradation=(
                    _degradation_lower_is_worse(baseline.average_category_f1, observed.average_category_f1)
                    if baseline
                    else None
                ),
                entity_localization_degradation=(
                    _degradation_lower_is_worse(
                        baseline.average_entity_localization_accuracy,
                        observed.average_entity_localization_accuracy,
                    )
                    if baseline
                    else None
                ),
                calibration_ece_degradation=(
                    _degradation_higher_is_worse(
                        baseline.average_calibration_ece, observed.average_calibration_ece
                    )
                    if baseline
                    else None
                ),
            )
        )
    by_transformation_combination.sort(key=lambda p: _degradation_sort_key(p.category_f1_degradation))

    return TransformationImpactAnalysis(
        baseline=baseline,
        by_transformation_type=by_transformation_type,
        by_transformation_combination=by_transformation_combination,
    )


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------


def load_reports_from_files(paths: list[str | Path]) -> tuple[list[EvaluationReport], list[dict]]:
    """Load multiple committed EvaluationReport JSON files (as produced by
    report_to_json()). A path that's missing, unreadable, not valid JSON,
    or not a well-formed report is skipped and recorded in the second
    return value -- never raises and aborts the whole batch over one bad
    file, matching this being an experiment-analysis tool that should
    degrade gracefully as a report set grows over time."""
    reports: list[EvaluationReport] = []
    skipped: list[dict] = []
    for path in paths:
        path_obj = Path(path)
        try:
            raw = json.loads(path_obj.read_text(encoding="utf-8"))
            reports.append(report_from_dict(raw))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            skipped.append({"path": str(path_obj), "reason": f"{type(exc).__name__}: {exc}"})
    return reports, skipped


def analyze_reports(
    reports: list[EvaluationReport],
    *,
    bucket_count: int = DEFAULT_COMPLEXITY_BUCKET_COUNT,
    min_sample_size_for_correlation: int = DEFAULT_MIN_SAMPLE_SIZE_FOR_CORRELATION,
    skipped_report_count: int = 0,
    generated_at: str | None = None,
) -> ExperimentAnalysis:
    """Aggregate a list of already-loaded EvaluationReports into one
    ExperimentAnalysis. Pure function of its inputs (plus the current
    time, for generated_at) -- never reads a file itself (that's
    load_reports_from_files' job) and never mutates any report it's
    given.

    An empty `reports` list is valid input: every average/correlation
    comes back None with trajectory_count 0, rather than raising.
    """
    experiment_summary = _experiment_summary(reports)
    complexity_analysis = _complexity_analysis(reports, bucket_count, min_sample_size_for_correlation)
    transformation_impact_analysis = _transformation_impact_analysis(reports)
    calibration_drift_analysis = _calibration_drift_analysis(reports, bucket_count, complexity_analysis)

    return ExperimentAnalysis(
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        report_count=len(reports),
        skipped_report_count=skipped_report_count,
        experiment_summary=experiment_summary,
        complexity_analysis=complexity_analysis,
        transformation_impact_analysis=transformation_impact_analysis,
        calibration_drift_analysis=calibration_drift_analysis,
    )


def analysis_to_dict(analysis: ExperimentAnalysis) -> dict:
    return dataclasses.asdict(analysis)


def analysis_to_json(analysis: ExperimentAnalysis, indent: int | None = 2) -> str:
    return json.dumps(analysis_to_dict(analysis), indent=indent)
