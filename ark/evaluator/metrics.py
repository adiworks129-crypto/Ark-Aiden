"""
Evaluation metrics — Milestone 6.3.

Converts matcher.py's FindingMatchResult list (plus the Issue list it was
matched against) into measurable, DELIBERATELY SEPARATE metrics. Per your
instruction, this module never collapses these into one opaque score --
category detection and entity localization are computed independently,
each with its own precision/recall/(F1 or "localization accuracy"), so a
later report can show exactly *why* an agent scored the way it did (wrong
place vs. wrong diagnosis vs. both vs. neither).

Architecture: Issues -> Matcher Results -> Metrics Engine. This module
reads only `list[FindingMatchResult]` and `list[Issue]` -- never the
mutation ledger, the rendering manifest, or the ground-truth estate, and
never anything MuleSoft-specific (Issue and FindingMatchResult are both
already technology-agnostic, produced upstream). It performs no I/O and
mutates neither of its inputs.

True positive definition (shared with calibration.py via is_true_positive
below, so "correct" means the same thing everywhere it's used): a finding
is a true positive only if it resolved to a specific real Issue
(matched_issue_id is not None) AND the agent's claimed_issue_type equals
that Issue's actual issue_type. This is the STRICT, both-axes-correct
definition from Ark_Evaluator_Design.md Section 3 -- used here as the
basis for the primary category metrics and for calibration, while entity
localization metrics (below) deliberately use a looser, entity-only
notion of "found" so the two axes stay genuinely independent, not two
views of the same number.
"""

from __future__ import annotations

from dataclasses import dataclass

from ark.evaluator.issues import Issue
from ark.evaluator.matcher import FindingMatchResult

METRICS_SCHEMA_VERSION = "0.1.0"


@dataclass
class ClassificationMetrics:
    """Standard precision/recall/F1, computed over a specific TP/FP/FN
    definition — see compute_category_metrics()'s docstring for exactly
    what counts as each here."""

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    """None (not 0.0) when true_positives + false_positives == 0 -- the
    agent made no claims at all, so precision is undefined, not zero.
    Reported as null rather than a misleading number, matching the same
    discipline ECE's sample-size guard uses (Ark_Evaluator_Design.md
    Section 4.3)."""
    recall: float | None
    """None when true_positives + false_negatives == 0 -- there were no
    real issues to find at all (a Level 0 / clean estate), so recall is
    undefined, not zero or one."""
    f1: float | None
    """None whenever precision or recall is None, or both are 0.0
    (harmonic mean of two zeros is 0, defined; but if either input is
    undefined, f1 is undefined too)."""


@dataclass
class EntityLocalizationMetrics:
    """Kept entirely separate from ClassificationMetrics above — an agent
    can localize correctly while naming the wrong category, or vice versa,
    and this module must be able to show that distinction, not average it
    away."""

    true_positives: int
    """Findings whose entity_correct is True -- i.e. matched a real,
    affected entity, REGARDLESS of whether the claimed issue_type was
    right."""
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    localization_accuracy: float | None
    """The harmonic mean of entity precision/recall — named per your spec
    rather than "F1" to keep this axis's terminology distinct from the
    category axis's, even though the formula is the same."""


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def is_true_positive(match: FindingMatchResult, issues_by_id: dict[str, Issue]) -> bool:
    """The strict, both-axes-correct definition of "this finding was
    right": it named a specific real Issue (matched_issue_id set) AND its
    claimed_issue_type equals that Issue's actual issue_type. Shared with
    calibration.py so "correct" means the same thing in both places."""
    if match.matched_issue_id is None:
        return False
    issue = issues_by_id.get(match.matched_issue_id)
    return issue is not None and match.claimed_issue_type == issue.issue_type


def compute_category_metrics(
    matches: list[FindingMatchResult], issues: list[Issue]
) -> ClassificationMetrics:
    """Category-detection metrics: did the agent name the right issue
    TYPE for a real, correctly-located issue?

    - True positive: is_true_positive(match) — matched a real Issue AND
      named its actual type.
    - False positive: every other finding (wrong type on a real issue,
      or no real issue matched at all — a hallucination or a claim
      against an unaffected entity).
    - False negative: an Issue with no finding achieving a true positive
      against it (multiple TP claims on the same Issue don't inflate
      recall past 1 -- recall counts distinct Issues found, not claims;
      see module docstring).
    """
    issues_by_id = {issue.issue_id: issue for issue in issues}

    true_positive_findings = [m for m in matches if is_true_positive(m, issues_by_id)]
    false_positive_count = len(matches) - len(true_positive_findings)

    found_issue_ids = {m.matched_issue_id for m in true_positive_findings}
    false_negative_count = len([i for i in issues if i.issue_id not in found_issue_ids])

    tp_count = len(true_positive_findings)
    precision = tp_count / len(matches) if matches else None
    recall = tp_count / len(issues) if issues else None

    return ClassificationMetrics(
        true_positives=tp_count,
        false_positives=false_positive_count,
        false_negatives=false_negative_count,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
    )


def compute_category_metrics_by_type(
    matches: list[FindingMatchResult], issues: list[Issue]
) -> dict[str, ClassificationMetrics]:
    """Per-issue_type breakdown of compute_category_metrics — answers
    "which transformation types are hardest for this agent," restricted
    to each category's own Issues and the findings claiming that type."""
    types_present = sorted({issue.issue_type for issue in issues})
    result: dict[str, ClassificationMetrics] = {}
    for issue_type in types_present:
        scoped_issues = [i for i in issues if i.issue_type == issue_type]
        scoped_matches = [m for m in matches if m.claimed_issue_type == issue_type]
        result[issue_type] = compute_category_metrics(scoped_matches, scoped_issues)
    return result


def compute_entity_localization_metrics(
    matches: list[FindingMatchResult], issues: list[Issue]
) -> EntityLocalizationMetrics:
    """Entity-localization metrics: did the agent point at a real,
    affected entity at all — independent of whether it named the right
    issue_type for that entity. Deliberately looser than
    is_true_positive(): entity_correct alone (matcher.py already
    established this as "matched_issue_id is not None") is the bar here.
    """
    entity_correct_findings = [m for m in matches if m.entity_correct]
    false_positive_count = len(matches) - len(entity_correct_findings)

    found_issue_ids = {m.matched_issue_id for m in entity_correct_findings}
    false_negative_count = len([i for i in issues if i.issue_id not in found_issue_ids])

    tp_count = len(entity_correct_findings)
    precision = tp_count / len(matches) if matches else None
    recall = tp_count / len(issues) if issues else None

    return EntityLocalizationMetrics(
        true_positives=tp_count,
        false_positives=false_positive_count,
        false_negatives=false_negative_count,
        precision=precision,
        recall=recall,
        localization_accuracy=_f1(precision, recall),
    )
