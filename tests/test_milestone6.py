"""
Milestone 6.1 tests: the evaluator foundation (ark/evaluator/) —
normalized Issue derivation (issues.py), the agent-output contract
(schema.py), and the dynamic complexity model (complexity.py).

This milestone does not implement matching/scoring against an agent's
output — see Ark_Evaluator_Design.md Section 8 for the full 6.1-6.5 plan.
These tests cover only the foundation: issue consolidation (including the
"compounding" and "net-zero" cases the Milestone 4 example ledger actually
exhibits), complexity determinism, agent-output validation, and
regression guarantees that none of this reads-only infrastructure ever
mutates ground truth or the mutation ledger.

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from ark.core.validate import validate_ground_truth
from ark.evaluator.complexity import ComplexityWeights, compute_trajectory_complexity
from ark.evaluator.issues import derive_issues
from ark.evaluator.schema import (
    AgentOutputValidationError,
    ISSUE_TYPE_TAXONOMY,
    parse_agent_output,
)
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES
from ark.mutation.registry import OPERATOR_REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"


def _load_estate(path):
    return validate_ground_truth(path)


def _level_3_result(seed: int = 1):
    baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
    return run_trajectory(baseline, PROFILES["level_3_legacy"], seed=seed)


class TestIssueNormalization(unittest.TestCase):
    """Verified against the real, committed Milestone 4 example trajectory
    (examples/milestone4/ledger_level_3_legacy.json describes the same
    seed=1/level_3_legacy run reproduced here via run_trajectory)."""

    def test_compounding_mutations_on_the_same_entity_consolidate_into_one_issue(self):
        """step-inventory-build-response's description is hit by
        documentation_decay TWICE in this trajectory (truncated, then
        emptied) — see examples/milestone4/README.md. This must collapse
        into exactly one Issue, not two."""
        result = _level_3_result()
        issues = derive_issues(result.ledger)

        matches = [i for i in issues if i.issue_id == "documentation_decay:step-inventory-build-response"]
        self.assertEqual(len(matches), 1, "compounding records did not consolidate into one issue")

        issue = matches[0]
        self.assertEqual(issue.mutation_count, 2)
        self.assertEqual(issue.affected_entity_ids, ["step-inventory-build-response"])
        # Final observable state only -- the truncated intermediate value
        # ("TODO: document this step.") must not appear anywhere.
        self.assertEqual(
            issue.observable_symptom["step-inventory-build-response"]["description"], ""
        )
        # Conservative "worst observed" severity rollup: max, not last/first/mean.
        self.assertAlmostEqual(issue.severity, 0.6951426626096591, places=9)
        self.assertEqual(len(issue.transformation_history), 2)

    def test_mutations_that_net_to_zero_produce_no_issue(self):
        """step-process-verify-customer's target_api_id is changed away
        from api-customer-system-v1 and then changed BACK to it three
        records later in this same trajectory. The final rendered artifact
        is identical to the untouched baseline for that field -- there is
        nothing observable for an agent to find, so this must not surface
        as a scoreable Issue even though the ledger legitimately recorded
        two real mutation events."""
        result = _level_3_result()
        issues = derive_issues(result.ledger)

        for issue in issues:
            self.assertNotIn(
                "step-process-verify-customer",
                issue.affected_entity_ids,
                "a net-zero (reverted) mutation surfaced as an observable issue",
            )

    def test_issue_derivation_is_deterministic(self):
        result_1 = _level_3_result(seed=7)
        result_2 = _level_3_result(seed=7)
        issues_1 = derive_issues(result_1.ledger)
        issues_2 = derive_issues(result_2.ledger)
        self.assertEqual(issues_1, issues_2)

    def test_every_issue_has_required_fields_populated(self):
        result = _level_3_result()
        issues = derive_issues(result.ledger)
        self.assertGreater(len(issues), 0)
        for issue in issues:
            self.assertTrue(issue.issue_id)
            self.assertIn(issue.issue_type, OPERATOR_REGISTRY)
            self.assertGreater(len(issue.affected_entity_ids), 0)
            self.assertGreaterEqual(issue.severity, 0.0)
            self.assertLessEqual(issue.severity, 1.0)
            self.assertTrue(issue.expected_detection_target)
            self.assertGreaterEqual(issue.mutation_count, 1)
            # Every affected entity must have a non-empty observable symptom.
            for entity_id in issue.affected_entity_ids:
                self.assertIn(entity_id, issue.observable_symptom)
                self.assertTrue(issue.observable_symptom[entity_id])

    def test_level_0_clean_estate_has_no_issues(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_0_clean"], seed=1)
        self.assertEqual(derive_issues(result.ledger), [])

    def test_issue_count_matches_hand_verified_level_3_trajectory(self):
        """10 raw ledger records for seed=1/level_3_legacy consolidate into
        7 issues: two documentation_decay records on the same entity
        collapse to 1, two dependency_change records on the same entity
        cancel out to 0, and the remaining 6 records are each their own
        issue (one of which -- legacy_version_introduction -- spans two
        newly-created entities in a single record)."""
        result = _level_3_result()
        issues = derive_issues(result.ledger)
        self.assertEqual(len(result.ledger.records), 10)
        self.assertEqual(len(issues), 7)


class TestEvaluatorDoesNotMutateGroundTruthOrLedger(unittest.TestCase):
    """Regression guard: the evaluator foundation is read-only. It must
    never modify the transformed estate or the ledger it's handed --
    mirroring the same discipline Milestone 4's mutation engine tests
    apply to the mutation engine itself."""

    def test_derive_issues_does_not_mutate_the_ledger(self):
        result = _level_3_result()
        ledger_copy = copy.deepcopy(result.ledger)
        derive_issues(result.ledger)
        self.assertEqual(result.ledger, ledger_copy, "derive_issues mutated its input ledger")

    def test_compute_trajectory_complexity_does_not_mutate_estate_or_ledger(self):
        result = _level_3_result()
        estate_copy = copy.deepcopy(result.transformed_estate)
        ledger_copy = copy.deepcopy(result.ledger)

        compute_trajectory_complexity(result.transformed_estate, result.ledger)

        self.assertEqual(
            result.transformed_estate, estate_copy, "compute_trajectory_complexity mutated the estate"
        )
        self.assertEqual(result.ledger, ledger_copy, "compute_trajectory_complexity mutated the ledger")


class TestComplexityModel(unittest.TestCase):
    def test_complexity_is_deterministic(self):
        result_1 = _level_3_result(seed=3)
        result_2 = _level_3_result(seed=3)
        profile_1 = compute_trajectory_complexity(result_1.transformed_estate, result_1.ledger)
        profile_2 = compute_trajectory_complexity(result_2.transformed_estate, result_2.ledger)
        self.assertEqual(profile_1, profile_2)

    def test_level_0_clean_estate_has_zero_complexity(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_0_clean"], seed=1)
        profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)

        self.assertEqual(profile.mutation_count, 0)
        self.assertEqual(profile.distinct_issue_count, 0)
        self.assertEqual(profile.compounding_count, 0)
        self.assertEqual(profile.severity_mean, 0.0)
        self.assertEqual(profile.transformation_diversity, 0.0)
        self.assertEqual(profile.interaction_score, 0.0)
        self.assertEqual(profile.complexity_score, 0.0)

    def test_complexity_matches_hand_verified_level_3_trajectory(self):
        """Cross-checked directly against examples/milestone4's committed
        seed=1/level_3_legacy ledger.

        transformation_diversity's denominator is deliberately
        `len(OPERATOR_REGISTRY)`, not a hardcoded 6 (see
        ComplexityProfile.transformation_diversity's own docstring in
        ark/evaluator/complexity.py: "tracks automatically if a 7th
        operator is ever added"). Feature 2 registered exactly that 7th
        operator (domain_implausible_component) — level_3_legacy's own
        operator_types tuple is untouched by that (still the same 6
        original operator names, still realizing the same 5 distinct
        types on this fixed seed/ledger), so the numerator here is
        unchanged; only the denominator moved 6 -> 7, exactly as
        documented. This is the live registry doing what it was built to
        do, not a fudged pin."""
        result = _level_3_result(seed=1)
        profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)

        self.assertEqual(profile.mutation_count, 10)
        self.assertEqual(profile.distinct_issue_count, 7)
        self.assertEqual(profile.compounding_count, 2)  # inventory-build-response + process-verify-customer
        self.assertEqual(profile.max_compounding_depth, 2)
        self.assertEqual(profile.affected_entity_count, 9)
        self.assertAlmostEqual(profile.transformation_diversity, 5 / 7, places=9)
        self.assertGreater(profile.complexity_score, 0.0)
        self.assertLessEqual(profile.complexity_score, 1.0)

    def test_higher_profile_level_tends_to_produce_higher_complexity(self):
        """Not a strict guarantee for every seed (that's exactly why
        complexity is computed from the realized ledger, not the profile
        label) -- but on a fixed seed, a strictly-additive profile
        (level_1 subset-of-level_2 subset-of-level_3) should not produce a
        LOWER complexity score than the level below it."""
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        scores = []
        for profile_name in ["level_1_minor", "level_2_structural", "level_3_legacy"]:
            result = run_trajectory(baseline, PROFILES[profile_name], seed=1)
            profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)
            scores.append(profile.complexity_score)
        self.assertEqual(scores, sorted(scores))

    def test_weights_are_overridable_and_affect_the_score(self):
        result = _level_3_result()
        default_profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)

        zero_severity_weights = ComplexityWeights(
            mutation_count=1.0, severity=0.0, diversity=1.0,
            dependency_impact=1.0, interaction=1.0, compounding=1.0,
        )
        reweighted_profile = compute_trajectory_complexity(
            result.transformed_estate, result.ledger, weights=zero_severity_weights
        )
        self.assertNotEqual(default_profile.complexity_score, reweighted_profile.complexity_score)
        self.assertEqual(reweighted_profile.weights_used, zero_severity_weights)


class TestAgentOutputSchema(unittest.TestCase):
    def test_valid_finding_parses_exactly_as_specified(self):
        raw = {
            "findings": [
                {
                    "artifact_reference": "customer-api.xml",
                    "entity_reference": "Customer API",
                    "issue_type": "documentation_decay",
                    "explanation": "The API documentation is incomplete because migration information is missing.",
                    "confidence": 0.87,
                }
            ]
        }
        parsed = parse_agent_output(raw)
        self.assertEqual(len(parsed.findings), 1)
        finding = parsed.findings[0]
        self.assertEqual(finding.artifact_reference, "customer-api.xml")
        self.assertEqual(finding.entity_reference, "Customer API")
        self.assertEqual(finding.issue_type, "documentation_decay")
        self.assertEqual(finding.raw_issue_type, "documentation_decay")
        self.assertEqual(finding.confidence, 0.87)

    def test_missing_required_field_is_rejected(self):
        raw = {"findings": [{"artifact_reference": "x.xml", "issue_type": "naming_drift",
                              "explanation": "e", "confidence": 0.5}]}
        with self.assertRaises(AgentOutputValidationError):
            parse_agent_output(raw)

    def test_confidence_out_of_range_is_rejected(self):
        raw = {
            "findings": [
                {
                    "artifact_reference": "x.xml",
                    "entity_reference": "X",
                    "issue_type": "naming_drift",
                    "explanation": "e",
                    "confidence": 1.5,
                }
            ]
        }
        with self.assertRaises(AgentOutputValidationError):
            parse_agent_output(raw)

    def test_non_taxonomy_issue_type_is_normalized_to_other_not_rejected(self):
        """A technology-specific syntax complaint (never a real Ark issue,
        since Milestone 4's operators guarantee well-formed output) must be
        coercible to 'other' rather than crashing the whole parse -- so it
        can still be scored (as a guaranteed non-match) rather than
        silently dropped."""
        raw = {
            "findings": [
                {
                    "artifact_reference": "x.xml",
                    "entity_reference": "X",
                    "issue_type": "invalid_mule_xml_attribute",
                    "explanation": "e",
                    "confidence": 0.9,
                }
            ]
        }
        parsed = parse_agent_output(raw)
        finding = parsed.findings[0]
        self.assertEqual(finding.issue_type, "other")
        self.assertEqual(finding.raw_issue_type, "invalid_mule_xml_attribute")

    def test_findings_must_be_a_list(self):
        with self.assertRaises(AgentOutputValidationError):
            parse_agent_output({"findings": "not-a-list"})

    def test_top_level_must_have_findings_key(self):
        with self.assertRaises(AgentOutputValidationError):
            parse_agent_output({})

    def test_issue_type_taxonomy_matches_live_operator_registry(self):
        self.assertEqual(ISSUE_TYPE_TAXONOMY, frozenset(OPERATOR_REGISTRY.keys()) | {"other"})


if __name__ == "__main__":
    unittest.main()
