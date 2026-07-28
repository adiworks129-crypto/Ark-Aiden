"""
Milestone 6.3 tests: evaluation metrics (ark/evaluator/metrics.py),
confidence calibration (ark/evaluator/calibration.py), explanation-quality
signals (ark/evaluator/explanation.py), and the complexity-performance
tracking hook (ark/evaluator/complexity.py's TrajectoryPerformanceRecord).

Covers, per the milestone's requirements: classification metrics (perfect,
missing, extra, mixed), localization metrics kept separate from category
metrics (wrong entity/wrong category/ambiguous), calibration (perfectly
calibrated, overconfident, underconfident), and regression guards proving
none of this mutates ground truth, the mutation ledger, the rendering
manifest, or the transformed estate.

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
from ark.evaluator.calibration import compute_calibration
from ark.evaluator.complexity import (
    build_trajectory_performance_record,
    compute_trajectory_complexity,
)
from ark.evaluator.explanation import extract_explanation_signals_for_matches
from ark.evaluator.issues import Issue, derive_issues
from ark.evaluator.matcher import FindingMatchResult, match_findings
from ark.evaluator.metrics import (
    compute_category_metrics,
    compute_category_metrics_by_type,
    compute_entity_localization_metrics,
)
from ark.evaluator.parser import parse_and_resolve_findings
from ark.evaluator.schema import parse_agent_output
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"


def _issue(issue_id: str, issue_type: str, affected_entity_ids: list[str]) -> Issue:
    return Issue(
        issue_id=issue_id,
        issue_type=issue_type,
        affected_entity_ids=affected_entity_ids,
        observable_symptom={eid: {"description": "changed value"} for eid in affected_entity_ids},
        severity=0.5,
        expected_detection_target="test fixture issue",
        transformation_history=[],
    )


def _match(
    finding_id: str,
    *,
    matched_issue_id: str | None,
    claimed_issue_type: str,
    confidence: float = 0.8,
    entity_correct: bool | None = None,
    category_correct: bool = True,
    artifact_reference_correct: bool = True,
    artifact_matches_entity: bool = True,
    entity_resolution_status: str = "resolved",
    explanation: str = "a test explanation",
    artifact_reference: str = "some-file.xml",
    entity_reference: str = "Some Entity",
) -> FindingMatchResult:
    if entity_correct is None:
        entity_correct = matched_issue_id is not None
    return FindingMatchResult(
        finding_id=finding_id,
        matched_issue_id=matched_issue_id,
        category_correct=category_correct,
        entity_correct=entity_correct,
        artifact_reference_correct=artifact_reference_correct,
        explanation_score_input=explanation,
        confidence=confidence,
        artifact_matches_entity=artifact_matches_entity,
        entity_resolution_status=entity_resolution_status,
        claimed_issue_type=claimed_issue_type,
        artifact_reference=artifact_reference,
        entity_reference=entity_reference,
    )


class TestCategoryClassificationMetrics(unittest.TestCase):
    def test_perfect_predictions(self):
        issues = [_issue("i1", "naming_drift", ["e1"]), _issue("i2", "documentation_decay", ["e2"])]
        matches = [
            _match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift"),
            _match("f2", matched_issue_id="i2", claimed_issue_type="documentation_decay"),
        ]
        result = compute_category_metrics(matches, issues)

        self.assertEqual(result.true_positives, 2)
        self.assertEqual(result.false_positives, 0)
        self.assertEqual(result.false_negatives, 0)
        self.assertEqual(result.precision, 1.0)
        self.assertEqual(result.recall, 1.0)
        self.assertEqual(result.f1, 1.0)

    def test_missing_findings_reduce_recall_not_precision(self):
        issues = [_issue("i1", "naming_drift", ["e1"]), _issue("i2", "documentation_decay", ["e2"])]
        matches = [_match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift")]
        result = compute_category_metrics(matches, issues)

        self.assertEqual(result.true_positives, 1)
        self.assertEqual(result.false_negatives, 1)
        self.assertEqual(result.precision, 1.0)
        self.assertEqual(result.recall, 0.5)
        self.assertAlmostEqual(result.f1, 2 / 3, places=9)

    def test_extra_false_positive_findings_reduce_precision_not_recall(self):
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [
            _match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift"),
            _match("f2", matched_issue_id=None, claimed_issue_type="naming_drift"),  # hallucination
        ]
        result = compute_category_metrics(matches, issues)

        self.assertEqual(result.true_positives, 1)
        self.assertEqual(result.false_positives, 1)
        self.assertEqual(result.false_negatives, 0)
        self.assertEqual(result.precision, 0.5)
        self.assertEqual(result.recall, 1.0)

    def test_mixed_correct_and_incorrect_findings(self):
        issues = [_issue("i1", "naming_drift", ["e1"]), _issue("i2", "documentation_decay", ["e2"])]
        matches = [
            _match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift"),  # correct
            _match("f2", matched_issue_id="i2", claimed_issue_type="dependency_change"),  # wrong type
            _match("f3", matched_issue_id=None, claimed_issue_type="naming_drift"),  # hallucination
        ]
        result = compute_category_metrics(matches, issues)

        self.assertEqual(result.true_positives, 1)
        self.assertEqual(result.false_positives, 2)
        self.assertEqual(result.false_negatives, 1)  # i2 never correctly identified
        self.assertAlmostEqual(result.precision, 1 / 3, places=9)
        self.assertEqual(result.recall, 0.5)

    def test_no_findings_at_all_gives_null_precision_not_zero(self):
        issues = [_issue("i1", "naming_drift", ["e1"])]
        result = compute_category_metrics([], issues)
        self.assertIsNone(result.precision)
        self.assertEqual(result.recall, 0.0)
        self.assertIsNone(result.f1)

    def test_clean_estate_gives_null_recall_not_zero(self):
        matches = [_match("f1", matched_issue_id=None, claimed_issue_type="naming_drift")]
        result = compute_category_metrics(matches, [])
        self.assertIsNone(result.recall)
        self.assertEqual(result.precision, 0.0)

    def test_per_category_breakdown_isolates_each_type(self):
        issues = [_issue("i1", "naming_drift", ["e1"]), _issue("i2", "documentation_decay", ["e2"])]
        matches = [
            _match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift"),
            _match("f2", matched_issue_id="i2", claimed_issue_type="dependency_change"),
        ]
        by_type = compute_category_metrics_by_type(matches, issues)
        self.assertEqual(by_type["naming_drift"].recall, 1.0)
        self.assertEqual(by_type["documentation_decay"].recall, 0.0)


class TestEntityLocalizationMetrics(unittest.TestCase):
    """Deliberately separate from category metrics -- these tests exist to
    prove the two axes can disagree on the exact same match set."""

    def test_correct_issue_type_but_wrong_entity_scores_as_a_miss_on_both_axes(self):
        """The claimed issue_type happens to be real (naming_drift exists
        somewhere in this estate), but the entity never resolved to a real
        affected entity -- matched_issue_id is None. Both category and
        entity metrics must treat this as a miss: category detection
        REQUIRES correct location too (Ark_Evaluator_Design.md Section 3 —
        a finding is only a true positive if BOTH axes are right), so a
        plausible-sounding but unlocated claim gets no credit anywhere."""
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [
            _match(
                "f1", matched_issue_id=None, entity_correct=False,
                claimed_issue_type="naming_drift", category_correct=True,
            )
        ]
        category = compute_category_metrics(matches, issues)
        entity = compute_entity_localization_metrics(matches, issues)

        self.assertEqual(category.true_positives, 0)
        self.assertEqual(category.false_negatives, 1)
        self.assertEqual(entity.true_positives, 0)
        self.assertEqual(entity.false_negatives, 1)

    def test_correct_entity_but_wrong_issue_type_scores_as_hit_for_entity_miss_for_category(self):
        """The agent pointed at the exact right entity (matched_issue_id
        set, entity_correct True) but named the wrong issue_type. Entity
        localization metrics must count this as found; category metrics
        must not."""
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [_match("f1", matched_issue_id="i1", claimed_issue_type="dependency_change")]

        category = compute_category_metrics(matches, issues)
        entity = compute_entity_localization_metrics(matches, issues)

        self.assertEqual(category.true_positives, 0)
        self.assertEqual(category.false_negatives, 1)
        self.assertEqual(entity.true_positives, 1)
        self.assertEqual(entity.false_negatives, 0)
        self.assertEqual(entity.recall, 1.0)
        self.assertEqual(category.recall, 0.0)

    def test_ambiguous_entity_resolution_is_scored_as_a_plain_miss_not_partial_credit(self):
        """An ambiguous reference (parser.py couldn't tell which of two
        same-named entities was meant) must score identically to an
        unresolved one here -- no special partial credit for 'it was
        close.'"""
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [
            _match(
                "f1", matched_issue_id=None, entity_correct=False,
                claimed_issue_type="naming_drift", entity_resolution_status="ambiguous",
            )
        ]
        entity = compute_entity_localization_metrics(matches, issues)
        self.assertEqual(entity.true_positives, 0)
        self.assertEqual(entity.false_positives, 1)
        self.assertEqual(entity.false_negatives, 1)

    def test_entity_metrics_are_never_derived_from_category_metrics_object(self):
        """Sanity check that the two dataclasses are independent objects,
        not one wrapping the other."""
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [_match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift")]
        category = compute_category_metrics(matches, issues)
        entity = compute_entity_localization_metrics(matches, issues)
        self.assertIsNot(type(category), type(entity))


class TestCalibration(unittest.TestCase):
    def test_perfectly_calibrated_predictions_have_zero_error(self):
        issues = [_issue(f"i{i}", "naming_drift", [f"e{i}"]) for i in range(6)]
        matches = [
            _match(f"f{i}", matched_issue_id=f"i{i}", claimed_issue_type="naming_drift", confidence=1.0)
            for i in range(3)
        ] + [
            _match(f"g{i}", matched_issue_id=None, claimed_issue_type="naming_drift", confidence=0.0)
            for i in range(3)
        ]
        result = compute_calibration(matches, issues)
        self.assertAlmostEqual(result.brier_score, 0.0, places=9)
        self.assertAlmostEqual(result.ece, 0.0, places=9)

    def test_overconfident_failures_produce_high_calibration_error(self):
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [
            _match(f"f{i}", matched_issue_id=None, claimed_issue_type="naming_drift", confidence=0.95)
            for i in range(6)
        ]
        result = compute_calibration(matches, issues)
        self.assertGreater(result.brier_score, 0.8)
        self.assertGreater(result.ece, 0.8)

    def test_underconfident_correct_predictions_also_produce_calibration_error(self):
        """High ACCURACY does not imply good calibration -- a correct but
        underconfident agent still has a real calibration error, even
        though it would score well on precision/recall."""
        issues = [_issue(f"i{i}", "naming_drift", [f"e{i}"]) for i in range(6)]
        matches = [
            _match(f"f{i}", matched_issue_id=f"i{i}", claimed_issue_type="naming_drift", confidence=0.2)
            for i in range(6)
        ]
        category = compute_category_metrics(matches, issues)
        calibration = compute_calibration(matches, issues)

        self.assertEqual(category.precision, 1.0)  # perfect accuracy
        self.assertGreater(calibration.brier_score, 0.5)  # but poorly calibrated
        self.assertGreater(calibration.ece, 0.5)

    def test_same_accuracy_different_calibration_are_distinguishable(self):
        """The exact scenario your spec calls out: two agents at the same
        90% accuracy must be distinguishable by calibration alone. Agent
        A's confidence (0.9) matches its true hit rate (9/10); Agent B
        gets the same 9/10 right but claims near-certainty (0.99)
        regardless -- classic overconfidence."""
        issues = [_issue(f"i{i}", "naming_drift", [f"e{i}"]) for i in range(10)]

        agent_a = [
            _match(f"a{i}", matched_issue_id=f"i{i}", claimed_issue_type="naming_drift", confidence=0.9)
            for i in range(9)
        ] + [_match("a9", matched_issue_id=None, claimed_issue_type="naming_drift", confidence=0.9)]

        agent_b = [
            _match(f"b{i}", matched_issue_id=f"i{i}", claimed_issue_type="naming_drift", confidence=0.99)
            for i in range(9)
        ] + [_match("b9", matched_issue_id=None, claimed_issue_type="naming_drift", confidence=0.99)]

        accuracy_a = compute_category_metrics(agent_a, issues)
        accuracy_b = compute_category_metrics(agent_b, issues)
        self.assertEqual(accuracy_a.precision, accuracy_b.precision)
        self.assertEqual(accuracy_a.recall, accuracy_b.recall)

        calibration_a = compute_calibration(agent_a, issues)
        calibration_b = compute_calibration(agent_b, issues)
        self.assertLess(calibration_a.brier_score, calibration_b.brier_score)
        self.assertLess(calibration_a.ece, calibration_b.ece)

    def test_ece_is_null_below_minimum_sample_size(self):
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [_match("f1", matched_issue_id="i1", claimed_issue_type="naming_drift", confidence=0.9)]
        result = compute_calibration(matches, issues, min_sample_size_for_ece=5)
        self.assertIsNone(result.ece)
        self.assertIsNotNone(result.brier_score)  # brier is fine even at n=1

    def test_no_claims_gives_null_brier_and_ece_not_zero(self):
        result = compute_calibration([], [_issue("i1", "naming_drift", ["e1"])])
        self.assertEqual(result.sample_size, 0)
        self.assertIsNone(result.brier_score)
        self.assertIsNone(result.ece)


class TestExplanationSignals(unittest.TestCase):
    def test_mentions_artifact_and_symptom_are_detected(self):
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [
            _match(
                "f1", matched_issue_id="i1", claimed_issue_type="naming_drift",
                artifact_reference="customer-api.xml",
                explanation="Looking at customer-api.xml, the description field changed value because of a rename.",
            )
        ]
        [signals] = extract_explanation_signals_for_matches(matches, issues)
        self.assertTrue(signals.mentions_affected_artifact)
        self.assertTrue(signals.references_observable_symptom)
        self.assertTrue(signals.identifies_plausible_cause)
        self.assertFalse(signals.unsupported_assumption_flag)

    def test_ungrounded_explanation_is_flagged_as_an_unsupported_assumption(self):
        issues = [_issue("i1", "naming_drift", ["e1"])]
        matches = [
            _match(
                "f1", matched_issue_id="i1", claimed_issue_type="naming_drift",
                artifact_reference="customer-api.xml",
                explanation="This is probably a problem somewhere in the system.",
            )
        ]
        [signals] = extract_explanation_signals_for_matches(matches, issues)
        self.assertFalse(signals.mentions_affected_artifact)
        self.assertFalse(signals.references_observable_symptom)
        self.assertTrue(signals.unsupported_assumption_flag)


class TestComplexityPerformanceHook(unittest.TestCase):
    def test_record_bundles_convenience_scalars_and_full_metrics(self):
        baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=1)
        issues = derive_issues(result.ledger)
        complexity_profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)

        matches = [_match("f1", matched_issue_id=issues[0].issue_id, claimed_issue_type=issues[0].issue_type)] if issues else []
        category = compute_category_metrics(matches, issues)
        by_type = compute_category_metrics_by_type(matches, issues)
        entity = compute_entity_localization_metrics(matches, issues)
        calibration = compute_calibration(matches, issues)

        record = build_trajectory_performance_record(
            "traj-1", complexity_profile, category, by_type, entity, calibration
        )

        self.assertEqual(record.complexity_score, complexity_profile.complexity_score)
        self.assertEqual(record.mutation_count, complexity_profile.mutation_count)
        self.assertEqual(record.agent_accuracy, category.f1)
        self.assertEqual(record.calibration_error, calibration.ece)
        # Full detail must still be present, not just the two scalars.
        self.assertIs(record.category_metrics, category)
        self.assertIs(record.entity_metrics, entity)
        self.assertIs(record.calibration, calibration)


class TestRegressionNoMutation(unittest.TestCase):
    """The metrics/calibration/explanation layer must never modify ground
    truth, the mutation ledger, the rendering manifest, or the transformed
    estate -- run the full pipeline and confirm all four are untouched."""

    def test_full_pipeline_does_not_mutate_ground_truth_ledger_manifest_or_estate(self):
        baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=1)
        rendered = MuleSoftAdapter().render(result.transformed_estate)

        baseline_copy = copy.deepcopy(baseline)
        estate_copy = copy.deepcopy(result.transformed_estate)
        ledger_copy = copy.deepcopy(result.ledger)
        manifest_copy = copy.deepcopy(rendered.manifest)

        issues = derive_issues(result.ledger)
        raw_output = {
            "findings": [
                {
                    "artifact_reference": "customer-system/src/main/resources/api-customer-system-v1.yaml",
                    "entity_reference": "Customer System API",
                    "issue_type": "naming_drift",
                    "explanation": "test",
                    "confidence": 0.6,
                }
            ]
        }
        agent_output = parse_agent_output(raw_output)
        resolved = parse_and_resolve_findings(agent_output.findings, rendered.manifest)
        matches = match_findings(resolved, issues)

        compute_category_metrics(matches, issues)
        compute_category_metrics_by_type(matches, issues)
        compute_entity_localization_metrics(matches, issues)
        compute_calibration(matches, issues)
        extract_explanation_signals_for_matches(matches, issues)
        complexity_profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)
        build_trajectory_performance_record(
            "traj-1",
            complexity_profile,
            compute_category_metrics(matches, issues),
            compute_category_metrics_by_type(matches, issues),
            compute_entity_localization_metrics(matches, issues),
            compute_calibration(matches, issues),
        )

        self.assertEqual(baseline, baseline_copy)
        self.assertEqual(result.transformed_estate, estate_copy)
        self.assertEqual(result.ledger, ledger_copy)
        self.assertEqual(rendered.manifest, manifest_copy)


if __name__ == "__main__":
    unittest.main()
