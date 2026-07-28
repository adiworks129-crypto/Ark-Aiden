"""
Milestone 4 tests: the mutation/transformation engine (ark/mutation/)
produces reproducible, ledger-documented, always-structurally-valid
transformed estates from a clean baseline, without ever mutating the
baseline itself.

Written as unittest.TestCase for the same zero-dependency reason as the
earlier milestone tests.
"""

from __future__ import annotations

import copy
import dataclasses
import random
import tempfile
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.serialize import estate_to_json
from ark.core.validate import validate_estate_object, validate_ground_truth
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES
from ark.mutation.registry import OPERATOR_REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"
MILESTONE0_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone0" / "ground_truth.json"


def _load_estate(path):
    return validate_ground_truth(path)


class TestBaselinePreservation(unittest.TestCase):
    def test_baseline_estate_is_never_mutated(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        baseline_copy = copy.deepcopy(baseline)

        run_trajectory(baseline, PROFILES["level_3_legacy"], seed=1)

        self.assertEqual(baseline, baseline_copy, "run_trajectory mutated its input estate in place")

    def test_operators_do_not_mutate_their_input_estate(self):
        estate = _load_estate(MILESTONE1_GROUND_TRUTH)
        estate_copy = copy.deepcopy(estate)
        rng = random.Random(0)

        for operator in OPERATOR_REGISTRY.values():
            candidates = operator.find_candidates(estate)
            if candidates:
                operator.apply(estate, candidates[0], 0.5, rng, mutation_ordinal=0)

        self.assertEqual(estate, estate_copy, "an operator mutated its input estate in place")


class TestReproducibility(unittest.TestCase):
    def test_same_seed_and_profile_produce_identical_transformed_estate(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result_1 = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=42)
        result_2 = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=42)
        self.assertEqual(result_1.transformed_estate, result_2.transformed_estate)

    def test_same_seed_and_profile_produce_identical_ledger_ignoring_timestamp(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result_1 = run_trajectory(baseline, PROFILES["level_2_structural"], seed=5)
        result_2 = run_trajectory(baseline, PROFILES["level_2_structural"], seed=5)

        def strip_timestamp(records):
            return [
                (r.mutation_id, r.transformation_type, r.severity, tuple(r.affected_entity_ids), r.rationale)
                for r in records
            ]

        self.assertEqual(strip_timestamp(result_1.ledger.records), strip_timestamp(result_2.ledger.records))

    def test_different_seed_produces_a_different_trajectory(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result_1 = run_trajectory(baseline, PROFILES["level_2_structural"], seed=1)
        result_2 = run_trajectory(baseline, PROFILES["level_2_structural"], seed=2)
        self.assertNotEqual(result_1.transformed_estate, result_2.transformed_estate)

    def test_determinism_survives_unrelated_global_random_state(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        random.seed(999)
        result_1 = run_trajectory(baseline, PROFILES["level_2_structural"], seed=3)
        for _ in range(500):
            random.random()
        result_2 = run_trajectory(baseline, PROFILES["level_2_structural"], seed=3)
        self.assertEqual(result_1.transformed_estate, result_2.transformed_estate)


class TestLedgerCompleteness(unittest.TestCase):
    def test_no_record_is_a_no_op_across_many_seeds(self):
        """Every record must reflect a genuine change (original_state !=
        transformed_state). The engine enforces this as a hard invariant
        (MutationEngineError otherwise) — this test exercises many seeds
        specifically to catch operator-level no-op bugs like the two
        found during development: naming_drift silently no-opping on
        space-separated API names, and documentation_decay landing on an
        already-identical decayed string when applied twice to the same
        step within one trajectory."""
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        for seed in range(20):
            result = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=seed)
            for record in result.ledger.records:
                self.assertNotEqual(
                    record.original_state,
                    record.transformed_state,
                    f"no-op mutation slipped through at seed={seed}, record={record.mutation_id}",
                )

    def test_ledger_records_have_all_required_fields_populated(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=1)

        self.assertGreater(len(result.ledger.records), 0)
        for record in result.ledger.records:
            self.assertTrue(record.mutation_id)
            self.assertIn(record.transformation_type, PROFILES["level_3_legacy"].operator_types)
            self.assertGreater(len(record.affected_entity_ids), 0)
            self.assertTrue(record.rationale)
            self.assertGreaterEqual(record.severity, 0.0)
            self.assertLessEqual(record.severity, 1.0)
            self.assertEqual(record.seed, 1)
            self.assertTrue(record.timestamp)
            # Every affected entity must appear as a key in both state dicts.
            for entity_id in record.affected_entity_ids:
                self.assertIn(entity_id, record.original_state)
                self.assertIn(entity_id, record.transformed_state)

    def test_sequence_index_matches_ledger_order(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=1)
        for i, record in enumerate(result.ledger.records):
            self.assertEqual(record.sequence_index, i)

    def test_ledger_records_estate_and_schema_provenance(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_1_minor"], seed=1)
        self.assertEqual(result.ledger.baseline_estate_id, baseline.estate_id)
        self.assertEqual(result.ledger.baseline_schema_version, baseline.schema_version)
        self.assertEqual(result.ledger.profile_name, "level_1_minor")
        self.assertEqual(result.ledger.trajectory_seed, 1)


class TestTransformedEstateValidity(unittest.TestCase):
    def test_transformed_estate_passes_full_validation_round_trip(self):
        """Not an in-memory shortcut: serialize to JSON and run the exact
        same validate_ground_truth() path a hand-authored file goes through."""
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=1)

        tmp_path = Path(tempfile.mkdtemp()) / "transformed.json"
        tmp_path.write_text(estate_to_json(result.transformed_estate), encoding="utf-8")
        validated = validate_ground_truth(tmp_path)
        self.assertEqual(validated.estate_id, result.transformed_estate.estate_id)

    def test_transformed_estate_exports_via_mulesoft_adapter(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_3_legacy"], seed=1)
        rendered = MuleSoftAdapter().render(result.transformed_estate)
        self.assertGreater(len(rendered.artifacts), 0)

    def test_level_0_profile_produces_no_changes(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_0_clean"], seed=1)
        self.assertEqual(len(result.ledger.records), 0)
        self.assertEqual(result.transformed_estate, baseline)

    def test_engine_degrades_gracefully_when_estate_is_too_small_for_profile(self):
        """Milestone 0's example has no ApiCallSteps at all, so
        dependency_change and legacy_version_introduction quickly run out
        of candidates as other operators consume the small estate's few
        entities. The engine must stop early, not crash."""
        tiny_baseline = _load_estate(MILESTONE0_GROUND_TRUTH)
        result = run_trajectory(tiny_baseline, PROFILES["level_3_legacy"], seed=1)
        self.assertLessEqual(len(result.ledger.records), PROFILES["level_3_legacy"].num_mutations)
        # Whatever it did manage must still be fully valid.
        errors = validate_estate_object(result.transformed_estate)
        self.assertEqual(errors, [])


class TestOperatorsWorkIndependently(unittest.TestCase):
    """Every operator must be individually invocable, without the engine,
    against a plain baseline, and must (a) find at least one candidate on
    a rich-enough estate, (b) leave the result valid, (c) not touch the
    input.

    Feature 2's DomainComponentInjectionOperator ("domain_implausible_
    component") is a deliberate, documented exception to (a) on the plain
    Milestone 1 estate specifically: that estate has no `domain` set, and
    this operator's whole precondition IS an assigned domain (see its own
    find_candidates() docstring — no domain means nothing to be
    implausible relative to, by design, not a bug). It's tested
    separately below, on a domain-tagged copy of the same estate, rather
    than folded into the generic "every operator" loops here."""

    _DOMAIN_DEPENDENT_OPERATOR = "domain_implausible_component"

    def test_every_operator_has_candidates_on_milestone1_estate(self):
        estate = _load_estate(MILESTONE1_GROUND_TRUTH)
        for name, operator in OPERATOR_REGISTRY.items():
            if name == self._DOMAIN_DEPENDENT_OPERATOR:
                continue
            candidates = operator.find_candidates(estate)
            self.assertGreater(len(candidates), 0, f"{name} found no candidates on a rich estate")

    def test_domain_dependent_operator_has_no_candidates_without_a_domain_but_does_with_one(self):
        estate = _load_estate(MILESTONE1_GROUND_TRUTH)
        operator = OPERATOR_REGISTRY[self._DOMAIN_DEPENDENT_OPERATOR]

        self.assertEqual(operator.find_candidates(estate), [])

        domain_tagged = dataclasses.replace(estate, domain="finance")
        candidates = operator.find_candidates(domain_tagged)
        self.assertGreater(len(candidates), 0, "found no candidates once a domain was assigned")

    def test_every_operator_produces_a_valid_estate_in_isolation(self):
        estate = _load_estate(MILESTONE1_GROUND_TRUTH)
        domain_tagged = dataclasses.replace(estate, domain="finance")
        rng = random.Random(0)
        for name, operator in OPERATOR_REGISTRY.items():
            # The domain-dependent operator needs the domain-tagged copy
            # (see class docstring); every other operator is domain-
            # agnostic and behaves identically either way, so using the
            # domain-tagged estate uniformly here doesn't weaken this
            # test for them.
            candidates = operator.find_candidates(domain_tagged)
            new_estate, draft = operator.apply(domain_tagged, candidates[0], 0.8, rng, mutation_ordinal=1)
            errors = validate_estate_object(new_estate)
            self.assertEqual(errors, [], f"{name} produced an invalid estate: {errors}")
            self.assertIsNot(new_estate, domain_tagged)

    def test_naming_drift_never_changes_entity_ids(self):
        estate = _load_estate(MILESTONE1_GROUND_TRUTH)
        operator = OPERATOR_REGISTRY["naming_drift"]
        rng = random.Random(0)
        candidates = operator.find_candidates(estate)
        new_estate, draft = operator.apply(estate, candidates[0], 1.0, rng, mutation_ordinal=1)

        original_ids = {
            app.id for app in estate.applications
        } | {api.id for app in estate.applications for api in app.apis} | {
            flow.id for app in estate.applications for flow in app.flows
        }
        new_ids = {
            app.id for app in new_estate.applications
        } | {api.id for app in new_estate.applications for api in app.apis} | {
            flow.id for app in new_estate.applications for flow in app.flows
        }
        self.assertEqual(original_ids, new_ids, "naming_drift changed an id, not just a name")

    def test_duplicate_processing_adds_a_flow_without_removing_the_original(self):
        estate = _load_estate(MILESTONE1_GROUND_TRUTH)
        operator = OPERATOR_REGISTRY["duplicate_processing"]
        rng = random.Random(0)
        candidates = operator.find_candidates(estate)
        target = candidates[0]
        new_estate, draft = operator.apply(estate, target, 0.9, rng, mutation_ordinal=1)

        app = next(a for a in new_estate.applications if a.id == target["app_id"])
        flow_ids = {f.id for f in app.flows}
        self.assertIn(target["target_flow_id"], flow_ids, "original flow was removed")
        new_flow_id = draft.affected_entity_ids[0]
        self.assertIn(new_flow_id, flow_ids, "duplicate flow was not added")
        self.assertNotEqual(new_flow_id, target["target_flow_id"])


class TestMutationProfiles(unittest.TestCase):
    def test_profiles_have_increasing_mutation_counts(self):
        counts = [PROFILES[name].num_mutations for name in
                  ["level_0_clean", "level_1_minor", "level_2_structural", "level_3_legacy"]]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[1])
        self.assertLess(counts[1], counts[2])
        self.assertLess(counts[2], counts[3])

    def test_profiles_have_additive_operator_sets(self):
        level1 = set(PROFILES["level_1_minor"].operator_types)
        level2 = set(PROFILES["level_2_structural"].operator_types)
        level3 = set(PROFILES["level_3_legacy"].operator_types)
        self.assertTrue(level1.issubset(level2))
        self.assertTrue(level2.issubset(level3))
        self.assertLess(level1, level2)  # strictly more, not just equal
        self.assertLess(level2, level3)

    def test_higher_profiles_tend_to_use_higher_severity_ranges(self):
        for lower, higher in [
            ("level_1_minor", "level_2_structural"),
            ("level_2_structural", "level_3_legacy"),
        ]:
            self.assertLessEqual(PROFILES[lower].severity_range[0], PROFILES[higher].severity_range[0])
            self.assertLessEqual(PROFILES[lower].severity_range[1], PROFILES[higher].severity_range[1])


class TestLedgerSupportsEvaluation(unittest.TestCase):
    """Proves the ledger's shape is usable for scoring an agent's output —
    not a full evaluator (that's a later milestone), just confirmation
    that the data needed for precision/recall scoring is actually present
    and correctly keyed."""

    def test_ledger_enables_precision_recall_style_scoring(self):
        baseline = _load_estate(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=1)

        actual_affected = {eid for r in result.ledger.records for eid in r.affected_entity_ids}
        self.assertGreater(len(actual_affected), 0)

        # Simulate an agent that found most of the real issues plus one
        # false positive it imagined.
        found_count = max(1, len(actual_affected) - 1)
        simulated_agent_claims = set(list(actual_affected)[:found_count]) | {"entity-that-was-never-mutated"}

        true_positives = simulated_agent_claims & actual_affected
        false_positives = simulated_agent_claims - actual_affected
        false_negatives = actual_affected - simulated_agent_claims

        precision = len(true_positives) / len(simulated_agent_claims)
        recall = len(true_positives) / len(actual_affected)

        self.assertGreater(precision, 0.0)
        self.assertGreater(recall, 0.0)
        self.assertEqual(len(false_positives), 1)
        self.assertEqual(true_positives | false_negatives, actual_affected)


if __name__ == "__main__":
    unittest.main()
