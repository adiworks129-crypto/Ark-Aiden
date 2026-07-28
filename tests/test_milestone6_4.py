"""
Milestone 6.4 tests: report assembly (ark/evaluator/report.py) and the
single pipeline entry point (ark/evaluator/orchestrator.py).

Milestone 6.4 introduces no new metrics, scoring, or matching behavior --
these tests check assembly/reproducibility/serialization/isolation
properties, not new scoring correctness (that's already covered by
tests/test_milestone6_3.py).

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
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
from ark.evaluator.issues import derive_issue_diagnostics, derive_issues
from ark.evaluator.orchestrator import evaluate
from ark.evaluator.report import report_from_dict, report_to_dict, report_to_json
from ark.generator.config import GeneratorConfig
from ark.generator.generator import generate_estate
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"

MIXED_AGENT_OUTPUT = {
    "findings": [
        {
            # Correct.
            "artifact_reference": "order-status-experience/src/main/resources/api-order-status-experience-v1.yaml",
            "entity_reference": "OrderStatusE",
            "issue_type": "naming_drift",
            "explanation": "The API title changed to an abbreviated form.",
            "confidence": 0.9,
        },
        {
            # Pure hallucination: real artifact/entity, nothing mutated there.
            "artifact_reference": "order-processing-process/src/main/mule/order-processing-process.xml",
            "entity_reference": "Build Processing Result",
            "issue_type": "schema_inconsistency",
            "explanation": "Fields look inconsistent.",
            "confidence": 0.95,
        },
    ]
}


def _baseline_result(seed: int = 7, profile_name: str = "level_2_structural"):
    baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
    return run_trajectory(baseline, PROFILES[profile_name], seed=seed)


def _rendered_manifest(estate):
    return MuleSoftAdapter().render(estate).manifest


class TestReportDeterminism(unittest.TestCase):
    def test_identical_inputs_produce_identical_reports_except_timestamp(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)

        report_1 = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)
        report_2 = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)

        dict_1 = report_to_dict(report_1)
        dict_2 = report_to_dict(report_2)

        self.assertNotEqual(
            dict_1["metadata"]["generated_at"], dict_2["metadata"]["generated_at"],
            "test setup assumption failed: timestamps should differ across two real calls",
        )

        dict_1["metadata"]["generated_at"] = None
        dict_2["metadata"]["generated_at"] = None
        self.assertEqual(dict_1, dict_2)

    def test_generated_at_can_be_pinned_for_full_equality(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        fixed_timestamp = "2026-01-01T00:00:00+00:00"

        report_1 = evaluate(
            result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT, generated_at=fixed_timestamp
        )
        report_2 = evaluate(
            result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT, generated_at=fixed_timestamp
        )
        self.assertEqual(report_to_dict(report_1), report_to_dict(report_2))

    def test_different_seeds_produce_different_reports(self):
        result_a = _baseline_result(seed=1)
        result_b = _baseline_result(seed=2)
        report_a = evaluate(
            result_a.transformed_estate, result_a.ledger, _rendered_manifest(result_a.transformed_estate), {"findings": []}
        )
        report_b = evaluate(
            result_b.transformed_estate, result_b.ledger, _rendered_manifest(result_b.transformed_estate), {"findings": []}
        )
        self.assertNotEqual(
            report_a.transformation_summary.complexity_score, report_b.transformation_summary.complexity_score
        )


class TestMissingAndEmptyAgentOutput(unittest.TestCase):
    def test_empty_findings_list_is_handled_correctly(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, {"findings": []})

        self.assertEqual(report.agent_performance.total_findings, 0)
        self.assertIsNone(report.agent_performance.category_metrics.precision)
        real_issue_count = len(derive_issues(result.ledger))
        self.assertEqual(len(report.failure_analysis.missed_issues), real_issue_count)
        self.assertEqual(report.failure_analysis.hallucinated_findings, [])

    def test_clean_level_0_estate_with_findings_is_all_hallucination(self):
        baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_0_clean"], seed=1)
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)

        self.assertEqual(report.issue_summary.total_observable_issues, 0)
        self.assertEqual(len(report.failure_analysis.hallucinated_findings), len(MIXED_AGENT_OUTPUT["findings"]))
        self.assertEqual(report.failure_analysis.missed_issues, [])


class TestFailureAnalysisContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = _baseline_result()
        cls.manifest = _rendered_manifest(cls.result.transformed_estate)
        cls.report = evaluate(cls.result.transformed_estate, cls.result.ledger, cls.manifest, MIXED_AGENT_OUTPUT)

    def test_hallucinated_finding_appears_in_failure_analysis(self):
        hallucinated = self.report.failure_analysis.hallucinated_findings
        self.assertEqual(len(hallucinated), 1)
        self.assertEqual(hallucinated[0].claimed_issue_type, "schema_inconsistency")
        self.assertIsNone(hallucinated[0].issue_id)

    def test_missed_issues_are_the_real_issues_never_correctly_claimed(self):
        real_issues = derive_issues(self.result.ledger)
        # Only one finding was correct (naming_drift), so every OTHER real
        # issue must show up as missed.
        missed_ids = {entry.issue_id for entry in self.report.failure_analysis.missed_issues}
        expected_missed = {i.issue_id for i in real_issues} - {
            m for m in [
                mr.matched_issue_id for mr in matcher_module.match_findings(
                    parser_module.parse_and_resolve_findings(
                        schema_module.parse_agent_output(MIXED_AGENT_OUTPUT).findings, self.manifest
                    ),
                    real_issues,
                )
                if mr.category_correct and mr.entity_correct
            ]
        }
        self.assertEqual(missed_ids, expected_missed)

    def test_wrong_category_and_correct_location_buckets_are_distinct_lists(self):
        # Structural check: both lists exist and are independently
        # queryable, even if empty for this particular agent output.
        self.assertIsInstance(self.report.failure_analysis.wrong_category_predictions, list)
        self.assertIsInstance(self.report.failure_analysis.correct_location_incorrect_diagnosis, list)

    def test_overconfidence_pattern_flags_the_high_confidence_hallucination(self):
        overconfident_finding_ids = {e.finding_id for e in self.report.failure_analysis.overconfidence_patterns}
        hallucinated_finding_id = self.report.failure_analysis.hallucinated_findings[0].finding_id
        self.assertIn(hallucinated_finding_id, overconfident_finding_ids)

    def test_correct_location_wrong_diagnosis_bucket_populates_on_a_crafted_case(self):
        """Build a case specifically for this bucket: right entity, wrong
        issue_type."""
        real_issues = derive_issues(self.result.ledger)
        target_issue = next(i for i in real_issues if i.issue_type == "naming_drift")
        entity_id = target_issue.affected_entity_ids[0]
        entity_entry = self.manifest["entity_index"][entity_id]

        wrong_diagnosis_output = {
            "findings": [
                {
                    "artifact_reference": entity_entry["artifact_path"],
                    "entity_reference": entity_entry["name"],
                    "issue_type": "documentation_decay",  # deliberately wrong
                    "explanation": "looks undocumented",
                    "confidence": 0.5,
                }
            ]
        }
        report = evaluate(self.result.transformed_estate, self.result.ledger, self.manifest, wrong_diagnosis_output)
        bucket = report.failure_analysis.correct_location_incorrect_diagnosis
        self.assertEqual(len(bucket), 1)
        self.assertEqual(bucket[0].issue_id, target_issue.issue_id)
        self.assertEqual(bucket[0].actual_issue_type, "naming_drift")
        self.assertEqual(bucket[0].claimed_issue_type, "documentation_decay")


class TestSerialization(unittest.TestCase):
    def test_report_round_trips_through_json(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)

        as_json = report_to_json(report)
        reloaded_dict = json.loads(as_json)
        reconstructed = report_from_dict(reloaded_dict)

        self.assertEqual(report_to_dict(reconstructed), report_to_dict(report))

    def test_to_dict_output_is_plain_json_serializable(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)
        # Should not raise.
        json.dumps(report_to_dict(report))

    def test_reconstructed_report_is_a_real_evaluation_report_with_typed_sections(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)
        reconstructed = report_from_dict(json.loads(report_to_json(report)))

        self.assertIsInstance(reconstructed, report_module.EvaluationReport)
        self.assertIsInstance(reconstructed.transformation_summary.complexity_profile, complexity_module.ComplexityProfile)
        self.assertIsInstance(reconstructed.agent_performance.calibration, calibration_module.CalibrationResult)
        self.assertIsInstance(reconstructed.issues[0], issues_module.Issue) if reconstructed.issues else None


class TestNoMutationRegression(unittest.TestCase):
    def test_report_generation_does_not_mutate_estate_ledger_or_manifest(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)

        estate_copy = copy.deepcopy(result.transformed_estate)
        ledger_copy = copy.deepcopy(result.ledger)
        manifest_copy = copy.deepcopy(manifest)
        agent_output_copy = copy.deepcopy(MIXED_AGENT_OUTPUT)

        evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)

        self.assertEqual(result.transformed_estate, estate_copy)
        self.assertEqual(result.ledger, ledger_copy)
        self.assertEqual(manifest, manifest_copy)
        self.assertEqual(MIXED_AGENT_OUTPUT, agent_output_copy)

    def test_report_generation_does_not_mutate_metrics_objects_reused_elsewhere(self):
        """A caller might compute metrics once and pass the SAME objects
        into assemble_report() for more than one report (e.g. comparing
        overconfidence_threshold values) -- assembly must not mutate them."""
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        issues = derive_issues(result.ledger)
        diagnostics = derive_issue_diagnostics(result.ledger)

        parsed = schema_module.parse_agent_output(MIXED_AGENT_OUTPUT)
        resolved = parser_module.parse_and_resolve_findings(parsed.findings, manifest)
        matches = matcher_module.match_findings(resolved, issues)
        category = metrics_module.compute_category_metrics(matches, issues)
        by_type = metrics_module.compute_category_metrics_by_type(matches, issues)
        entity = metrics_module.compute_entity_localization_metrics(matches, issues)
        calibration = calibration_module.compute_calibration(matches, issues)
        signals = explanation_module.extract_explanation_signals_for_matches(matches, issues)
        complexity_profile = complexity_module.compute_trajectory_complexity(result.transformed_estate, result.ledger)

        category_copy = copy.deepcopy(category)
        matches_copy = copy.deepcopy(matches)
        issues_copy = copy.deepcopy(issues)

        report_module.assemble_report(
            transformed_estate=result.transformed_estate,
            ledger=result.ledger,
            manifest=manifest,
            raw_agent_output=MIXED_AGENT_OUTPUT,
            issues=issues,
            issue_diagnostics=diagnostics,
            matches=matches,
            category_metrics=category,
            category_metrics_by_type=by_type,
            entity_metrics=entity,
            calibration_result=calibration,
            explanation_signals=signals,
            complexity_profile=complexity_profile,
            trajectory_id="t1",
        )

        self.assertEqual(category, category_copy)
        self.assertEqual(matches, matches_copy)
        self.assertEqual(issues, issues_copy)


class TestTechnologyIndependence(unittest.TestCase):
    """No evaluator module -- including the new report.py/orchestrator.py
    -- may import ark.adapters at all, generic or MuleSoft-specific."""

    _EVALUATOR_MODULES = (
        issues_module, schema_module, parser_module, matcher_module,
        metrics_module, calibration_module, explanation_module, complexity_module,
        report_module, orchestrator_module,
    )

    def test_no_evaluator_module_imports_ark_adapters(self):
        for module in self._EVALUATOR_MODULES:
            tree = ast.parse(inspect.getsource(module))
            imported_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_names.add(node.module)
            for name in imported_names:
                self.assertFalse(
                    name == "ark.adapters" or name.startswith("ark.adapters."),
                    f"{module.__name__} imports {name}",
                )


class TestNoHiddenAgentInformation(unittest.TestCase):
    def test_raw_agent_output_is_an_exact_unmodified_passthrough(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, MIXED_AGENT_OUTPUT)
        self.assertEqual(report.raw_agent_output, MIXED_AGENT_OUTPUT)

    def test_report_contains_only_fields_derived_from_its_four_declared_inputs(self):
        """evaluate() only accepts (estate, ledger, manifest, agent_output)
        plus documented optional overrides -- there is no hidden fifth
        channel for agent context to enter the report through."""
        signature = inspect.signature(evaluate)
        required_params = [
            name for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty
        ]
        self.assertEqual(
            required_params, ["transformed_estate", "mutation_ledger", "rendered_manifest", "agent_output"]
        )

    def test_generator_version_is_none_when_no_generation_manifest_given(self):
        result = _baseline_result()
        manifest = _rendered_manifest(result.transformed_estate)
        report = evaluate(result.transformed_estate, result.ledger, manifest, {"findings": []})
        self.assertIsNone(report.metadata.generator_version)

    def test_generator_version_is_populated_when_generation_manifest_is_provided(self):
        generated = generate_estate(GeneratorConfig(seed=1))
        gen_result = run_trajectory(generated.estate, PROFILES["level_1_minor"], seed=1)
        manifest = _rendered_manifest(gen_result.transformed_estate)
        report = evaluate(
            gen_result.transformed_estate, gen_result.ledger, manifest, {"findings": []},
            generation_manifest=generated.manifest,
        )
        self.assertEqual(report.metadata.generator_version, generated.manifest.generator_version)


if __name__ == "__main__":
    unittest.main()
