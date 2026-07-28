"""
Milestone 6.5 tests: cross-report experiment analysis (ark/evaluator/analysis.py).

Milestone 6.5 introduces no new metrics, scoring, or matching behavior --
analysis.py is a pure consumer of already-assembled EvaluationReports
(Milestone 6.4). These tests check aggregation/determinism/None-semantics/
isolation properties, not new scoring correctness (that's covered by
tests/test_milestone6_3.py and tests/test_milestone6_4.py).

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
from ark.evaluator import analysis as analysis_module
from ark.evaluator import calibration as calibration_module
from ark.evaluator import complexity as complexity_module
from ark.evaluator import explanation as explanation_module
from ark.evaluator import issues as issues_module
from ark.evaluator import matcher as matcher_module
from ark.evaluator import metrics as metrics_module
from ark.evaluator import orchestrator as orchestrator_module
from ark.evaluator import parser as parser_module
from ark.evaluator import report as report_module
from ark.evaluator import schema as schema_module
from ark.evaluator.analysis import (
    DEFAULT_MIN_SAMPLE_SIZE_FOR_CORRELATION,
    analysis_to_dict,
    analysis_to_json,
    analyze_reports,
    load_reports_from_files,
)
from ark.evaluator.orchestrator import evaluate
from ark.evaluator.report import report_to_dict, report_to_json
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"


def _rendered_manifest(estate):
    return MuleSoftAdapter().render(estate).manifest


def _report_for(profile_name: str, seed: int, agent_output: dict, trajectory_id: str | None = None):
    baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
    result = run_trajectory(baseline, PROFILES[profile_name], seed=seed)
    manifest = _rendered_manifest(result.transformed_estate)
    return evaluate(
        result.transformed_estate,
        result.ledger,
        manifest,
        agent_output,
        trajectory_id=trajectory_id or f"{profile_name}-seed{seed}",
        generated_at="2026-01-01T00:00:00+00:00",
    )


def _perfect_output_for(profile_name: str, seed: int):
    """Builds an agent output that correctly names every real issue in this
    trajectory, by reading the manifest -- a test double standing in for a
    perfect agent, same pattern as test_milestone6_4.py's crafted cases."""
    baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
    result = run_trajectory(baseline, PROFILES[profile_name], seed=seed)
    manifest = _rendered_manifest(result.transformed_estate)
    issues = issues_module.derive_issues(result.ledger)

    findings = []
    for issue in issues:
        entity_id = issue.affected_entity_ids[0]
        entry = manifest["entity_index"][entity_id]
        findings.append(
            {
                "artifact_reference": entry["artifact_path"],
                "entity_reference": entry["name"],
                "issue_type": issue.issue_type,
                "explanation": "correct.",
                "confidence": 0.9,
            }
        )
    manifest_2 = _rendered_manifest(result.transformed_estate)
    return evaluate(
        result.transformed_estate,
        result.ledger,
        manifest_2,
        {"findings": findings},
        trajectory_id=f"{profile_name}-seed{seed}-perfect",
        generated_at="2026-01-01T00:00:00+00:00",
    )


class TestDeterminism(unittest.TestCase):
    def test_identical_reports_produce_identical_analysis_except_timestamp(self):
        report_a = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        report_b = _report_for("level_2_structural", seed=2, agent_output={"findings": []})

        analysis_1 = analyze_reports([report_a, report_b])
        analysis_2 = analyze_reports([copy.deepcopy(report_a), copy.deepcopy(report_b)])

        dict_1 = analysis_to_dict(analysis_1)
        dict_2 = analysis_to_dict(analysis_2)
        dict_1["generated_at"] = None
        dict_2["generated_at"] = None
        self.assertEqual(dict_1, dict_2)

    def test_generated_at_can_be_pinned_for_full_equality(self):
        report_a = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        fixed = "2026-01-01T00:00:00+00:00"

        analysis_1 = analyze_reports([report_a], generated_at=fixed)
        analysis_2 = analyze_reports([copy.deepcopy(report_a)], generated_at=fixed)
        self.assertEqual(analysis_to_dict(analysis_1), analysis_to_dict(analysis_2))

    def test_report_order_does_not_affect_aggregate_averages(self):
        report_a = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        report_b = _report_for("level_2_structural", seed=2, agent_output={"findings": []})

        forward = analyze_reports([report_a, report_b], generated_at="x")
        backward = analyze_reports([report_b, report_a], generated_at="x")

        self.assertEqual(
            forward.experiment_summary.average_complexity_score,
            backward.experiment_summary.average_complexity_score,
        )


class TestEmptyAndMissingReports(unittest.TestCase):
    def test_empty_report_list_produces_all_none_averages_not_a_crash(self):
        analysis = analyze_reports([], generated_at="x")
        self.assertEqual(analysis.report_count, 0)
        self.assertIsNone(analysis.experiment_summary.average_complexity_score)
        self.assertIsNone(analysis.experiment_summary.average_category_f1)
        self.assertEqual(analysis.experiment_summary.transformation_type_distribution, {})
        for bucket in analysis.complexity_analysis.buckets:
            self.assertEqual(bucket.trajectory_count, 0)
            self.assertIsNone(bucket.average_category_f1)
        for correlation in analysis.complexity_analysis.correlations:
            self.assertIsNone(correlation.correlation_with_complexity_score)
            self.assertEqual(correlation.sample_size, 0)
        self.assertIsNone(analysis.transformation_impact_analysis.baseline)
        self.assertEqual(analysis.transformation_impact_analysis.by_transformation_type, [])

    def test_load_reports_from_files_skips_missing_and_malformed_files_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            good_path = Path(tmp_dir) / "good.json"
            malformed_path = Path(tmp_dir) / "malformed.json"
            missing_path = Path(tmp_dir) / "does_not_exist.json"

            report = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
            good_path.write_text(report_to_json(report), encoding="utf-8")
            malformed_path.write_text("{not valid json", encoding="utf-8")

            reports, skipped = load_reports_from_files([good_path, malformed_path, missing_path])

            self.assertEqual(len(reports), 1)
            self.assertEqual(len(skipped), 2)
            skipped_paths = {entry["path"] for entry in skipped}
            self.assertIn(str(malformed_path), skipped_paths)
            self.assertIn(str(missing_path), skipped_paths)

    def test_analyze_reports_records_skipped_report_count_when_given(self):
        report = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        analysis = analyze_reports([report], skipped_report_count=3, generated_at="x")
        self.assertEqual(analysis.report_count, 1)
        self.assertEqual(analysis.skipped_report_count, 3)


class TestComplexityAnalysis(unittest.TestCase):
    def test_correlation_is_none_below_minimum_sample_size(self):
        reports = [_report_for("level_1_minor", seed=s, agent_output={"findings": []}) for s in range(1, 3)]
        self.assertLess(len(reports), DEFAULT_MIN_SAMPLE_SIZE_FOR_CORRELATION)

        analysis = analyze_reports(reports, generated_at="x")
        for correlation in analysis.complexity_analysis.correlations:
            # category_f1/localization are None here anyway (empty findings
            # -> undefined precision), but calibration_ece is also None
            # (0 claims), and in all cases sample_size stays below the
            # threshold so correlation must be None regardless.
            self.assertIsNone(correlation.correlation_with_complexity_score)

    def test_perfect_agent_correlation_sign_is_sane(self):
        """A perfect agent across increasingly complex trajectories should
        show performance flat-or-positive with complexity (never a strong
        negative correlation) -- a basic sanity check on the sign
        convention, not a claim about real agents.

        Restricted to level_1_minor/level_2_structural: at level_3_legacy
        this trajectory/seed combination happens to hit the documented
        "one entity, two real issues" matcher limitation (see
        examples/milestone6/README.md's "a real nuance" section), which
        would make a "one finding per issue" agent construction genuinely
        NOT perfect there for reasons unrelated to what this test is
        checking (analysis.py's correlation math, not matcher.py's
        per-entity resolution behavior)."""
        reports = [
            _perfect_output_for("level_1_minor", seed=1),
            _perfect_output_for("level_1_minor", seed=2),
            _perfect_output_for("level_2_structural", seed=1),
            _perfect_output_for("level_2_structural", seed=2),
        ]
        analysis = analyze_reports(reports, generated_at="x")
        f1_correlation = next(
            c for c in analysis.complexity_analysis.correlations if c.metric_name == "category_f1"
        )
        # A perfect agent gets f1 == 1.0 on every trajectory regardless of
        # complexity -- zero variance in f1, so Pearson correlation is
        # mathematically undefined (None), never a spurious negative
        # number. This IS the sign-sanity check: a flat, non-degrading
        # agent must never be reported as "correlated with degradation."
        self.assertIsNone(f1_correlation.correlation_with_complexity_score)

    def test_buckets_partition_reports_by_fixed_absolute_complexity_ranges(self):
        reports = [_report_for("level_3_legacy", seed=s, agent_output={"findings": []}) for s in range(1, 4)]
        analysis = analyze_reports(reports, generated_at="x")
        total_bucketed = sum(bucket.trajectory_count for bucket in analysis.complexity_analysis.buckets)
        self.assertEqual(total_bucketed, len(reports))
        # Bucket boundaries are fixed/absolute (0.0-1.0 split evenly),
        # independent of what min/max complexity this particular batch has.
        self.assertEqual(analysis.complexity_analysis.buckets[0].lower_bound, 0.0)
        self.assertEqual(analysis.complexity_analysis.buckets[-1].upper_bound, 1.0)


class TestTransformationImpactAnalysis(unittest.TestCase):
    def test_baseline_comes_from_clean_zero_mutation_reports(self):
        clean_report = _report_for("level_0_clean", seed=1, agent_output={"findings": []})
        mutated_report = _report_for("level_2_structural", seed=1, agent_output={"findings": []})

        analysis = analyze_reports([clean_report, mutated_report], generated_at="x")
        self.assertIsNotNone(analysis.transformation_impact_analysis.baseline)
        self.assertEqual(analysis.transformation_impact_analysis.baseline.trajectory_count, 1)

    def test_no_baseline_available_when_no_clean_reports_present(self):
        mutated_report = _report_for("level_2_structural", seed=1, agent_output={"findings": []})
        analysis = analyze_reports([mutated_report], generated_at="x")
        self.assertIsNone(analysis.transformation_impact_analysis.baseline)
        for entry in analysis.transformation_impact_analysis.by_transformation_type:
            self.assertIsNone(entry.baseline)
            self.assertIsNone(entry.category_f1_degradation)

    def test_by_type_and_by_combination_breakdowns_are_distinct(self):
        report = _report_for("level_2_structural", seed=1, agent_output={"findings": []})
        analysis = analyze_reports([report], generated_at="x")

        by_type_names = {t.transformation_type for t in analysis.transformation_impact_analysis.by_transformation_type}
        by_combo_sets = [
            tuple(c.transformation_types) for c in analysis.transformation_impact_analysis.by_transformation_combination
        ]
        # level_2_structural realizes more than one transformation type, so
        # the combination view should have at least one entry with more
        # than one type in it -- a real distinction from the single-type view.
        self.assertTrue(any(len(combo) > 1 for combo in by_combo_sets))
        self.assertTrue(by_type_names.issubset(set().union(*[set(c) for c in by_combo_sets])))

    def test_degradation_sign_convention_is_positive_means_worse_than_baseline(self):
        """Construct a scenario where observed f1 is strictly worse than a
        clean baseline's hallucination-based precision is not comparable
        (category_f1 baseline is structurally None for a truly clean
        estate -- see calibration axis instead, which the mock agent can
        populate on both sides)."""
        clean_report = _report_for(
            "level_0_clean",
            seed=1,
            agent_output={
                "findings": [
                    {
                        "artifact_reference": "order-status-experience/src/main/resources/api-order-status-experience-v1.yaml",
                        "entity_reference": "Order Status Experience API",
                        "issue_type": "naming_drift",
                        "explanation": "x",
                        "confidence": conf,
                    }
                    for conf in [0.1, 0.2, 0.9, 0.95, 0.99]
                ]
            },
        )
        mutated_report = _report_for(
            "level_1_minor",
            seed=1,
            agent_output={
                "findings": [
                    {
                        "artifact_reference": "order-status-experience/src/main/resources/api-order-status-experience-v1.yaml",
                        "entity_reference": "Order Status Experience API",
                        "issue_type": "naming_drift",
                        "explanation": "x",
                        "confidence": conf,
                    }
                    for conf in [0.99, 0.99, 0.99, 0.99, 0.99]
                ]
            },
        )
        analysis = analyze_reports([clean_report, mutated_report], generated_at="x")
        self.assertIsNotNone(analysis.transformation_impact_analysis.baseline.average_calibration_ece)
        for entry in analysis.transformation_impact_analysis.by_transformation_type:
            if entry.calibration_ece_degradation is not None:
                observed = entry.observed.average_calibration_ece
                baseline = entry.baseline.average_calibration_ece
                if observed is not None and baseline is not None:
                    expected_sign = observed - baseline
                    self.assertAlmostEqual(entry.calibration_ece_degradation, expected_sign)


class TestSerialization(unittest.TestCase):
    def test_analysis_to_dict_is_plain_json_serializable(self):
        report = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        analysis = analyze_reports([report], generated_at="x")
        json.dumps(analysis_to_dict(analysis))

    def test_analysis_to_json_round_trips_as_plain_data(self):
        report = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        analysis = analyze_reports([report], generated_at="x")
        as_json = analysis_to_json(analysis)
        reloaded = json.loads(as_json)
        self.assertEqual(reloaded, analysis_to_dict(analysis))

    def test_no_analysis_from_dict_function_exists(self):
        """Explicit scope boundary per the approved plan: deserialization
        is out of scope for analysis.py unless a concrete requirement
        appears later."""
        self.assertFalse(hasattr(analysis_module, "analysis_from_dict"))


class TestNoMutationRegression(unittest.TestCase):
    def test_analyze_reports_does_not_mutate_its_input_reports(self):
        report_a = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        report_b = _report_for("level_2_structural", seed=2, agent_output={"findings": []})
        copy_a = copy.deepcopy(report_a)
        copy_b = copy.deepcopy(report_b)

        analyze_reports([report_a, report_b], generated_at="x")

        self.assertEqual(report_to_dict(report_a), report_to_dict(copy_a))
        self.assertEqual(report_to_dict(report_b), report_to_dict(copy_b))

    def test_analysis_to_dict_does_not_mutate_the_analysis_object(self):
        report = _report_for("level_1_minor", seed=1, agent_output={"findings": []})
        analysis = analyze_reports([report], generated_at="x")
        before = analysis_to_dict(analysis)
        analysis_to_dict(analysis)
        after = analysis_to_dict(analysis)
        self.assertEqual(before, after)


class TestTechnologyIndependence(unittest.TestCase):
    """analysis.py must remain a pure consumer of EvaluationReport objects
    -- no import of ark.adapters (generic or MuleSoft-specific), and no
    import of the mutation ledger/engine or the core ground-truth model,
    matching every other evaluator module's isolation boundary."""

    _FORBIDDEN_PREFIXES = ("ark.adapters", "ark.mutation", "ark.core.models")

    def test_analysis_module_does_not_import_adapters_mutation_or_core_models(self):
        tree = ast.parse(inspect.getsource(analysis_module))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)

        for name in imported_names:
            for forbidden in self._FORBIDDEN_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"analysis.py imports {name}, which starts with forbidden prefix {forbidden}",
                )

    def test_analysis_module_only_imports_from_report_module_within_ark_evaluator(self):
        """A stronger, more specific check than the general prefix ban
        above: analysis.py should only ever need ark.evaluator.report --
        the one already-assembled artifact it aggregates -- not reach
        into any other evaluator module directly."""
        tree = ast.parse(inspect.getsource(analysis_module))
        ark_evaluator_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("ark.evaluator"):
                ark_evaluator_imports.add(node.module)

        self.assertEqual(ark_evaluator_imports, {"ark.evaluator.report"})


if __name__ == "__main__":
    unittest.main()
