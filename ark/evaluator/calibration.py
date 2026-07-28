"""
Confidence calibration — Milestone 6.3.

Measures whether an agent's stated confidence reflects its actual
correctness, kept entirely separate from accuracy: two agents can both
score 90% accuracy while one is well-calibrated and the other is
extremely overconfident, and this module exists specifically so that
distinction is visible rather than averaged away.

Scope: calibration is computed only over the agent's CLAIMS (every
finding that resolved to something, i.e. every element of `matches` — a
finding that never happened at all obviously has no stated confidence to
score, and false negatives — real Issues the agent never mentioned — have
no confidence value to include either). This matches
Ark_Evaluator_Design.md Section 4.3.

"Correct," here, uses the same strict definition metrics.py's
is_true_positive() uses (matched a real Issue AND named its real type) —
imported directly from metrics.py rather than redefined, so calibration
and category-detection accuracy are never silently talking about two
different notions of "right."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ark.evaluator.issues import Issue
from ark.evaluator.matcher import FindingMatchResult
from ark.evaluator.metrics import is_true_positive

CALIBRATION_SCHEMA_VERSION = "0.1.0"

DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE = 5
"""Ark_Evaluator_Design.md Section 4.3's flagged caveat: ECE needs volume
to be meaningful. Below this many claims, ece is reported as None rather
than a noisy number computed from a handful of points."""

DEFAULT_ECE_BIN_COUNT = 10


@dataclass
class CalibrationResult:
    sample_size: int
    """Number of (confidence, correct) pairs this was computed over --
    i.e. len(matches). Always reported, even when brier/ece are None, so
    it's clear WHY they're None (0 claims) rather than looking like a
    silent failure."""
    brier_score: float | None
    """Mean squared error between stated confidence and binary
    correctness (0.0 = perfect, 1.0 = worst). Well-defined even for very
    small sample sizes (unlike ECE), so this is the primary per-report
    calibration number. None only when sample_size == 0."""
    ece: float | None
    """Expected Calibration Error: bins claims by confidence, compares
    average confidence to average accuracy per bin, weighted by bin size.
    None below DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE claims -- reported as null,
    never as a misleading number from too few points."""
    calibration_pairs: list[tuple[float, bool]] = field(default_factory=list)
    """Raw (confidence, correct) pairs, preserved so a later cross-report
    pass (Milestone 6.5) can recompute ECE/reliability diagrams across
    many evaluations without needing to re-run matching."""
    min_sample_size_for_ece: int = DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE


def compute_calibration(
    matches: list[FindingMatchResult],
    issues: list[Issue],
    *,
    min_sample_size_for_ece: int = DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE,
    bin_count: int = DEFAULT_ECE_BIN_COUNT,
) -> CalibrationResult:
    """Compute Brier score and (sample-size-gated) ECE over every finding
    in `matches`, using metrics.py's strict is_true_positive() as
    "correct." Reads only `matches` and `issues` -- no ledger, manifest,
    or ground-truth estate access."""
    issues_by_id = {issue.issue_id: issue for issue in issues}
    pairs: list[tuple[float, bool]] = [
        (match.confidence, is_true_positive(match, issues_by_id)) for match in matches
    ]

    sample_size = len(pairs)
    if sample_size == 0:
        return CalibrationResult(
            sample_size=0,
            brier_score=None,
            ece=None,
            calibration_pairs=[],
            min_sample_size_for_ece=min_sample_size_for_ece,
        )

    brier_score = sum((confidence - float(correct)) ** 2 for confidence, correct in pairs) / sample_size

    ece = None
    if sample_size >= min_sample_size_for_ece:
        ece = _expected_calibration_error(pairs, bin_count)

    return CalibrationResult(
        sample_size=sample_size,
        brier_score=brier_score,
        ece=ece,
        calibration_pairs=pairs,
        min_sample_size_for_ece=min_sample_size_for_ece,
    )


def _expected_calibration_error(pairs: list[tuple[float, bool]], bin_count: int) -> float:
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(bin_count)]
    for confidence, correct in pairs:
        # confidence is defined on [0.0, 1.0]; clamp 1.0 into the last bin
        # rather than overflowing into a (bin_count+1)-th bucket.
        index = min(int(confidence * bin_count), bin_count - 1)
        bins[index].append((confidence, correct))

    total = len(pairs)
    error = 0.0
    for bucket in bins:
        if not bucket:
            continue
        avg_confidence = sum(c for c, _ in bucket) / len(bucket)
        avg_accuracy = sum(1.0 for _, correct in bucket if correct) / len(bucket)
        error += (len(bucket) / total) * abs(avg_confidence - avg_accuracy)
    return error
