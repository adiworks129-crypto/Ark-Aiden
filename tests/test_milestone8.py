"""
Milestone 8 tests: the interactive UI (ark/ui/).

Milestone 8 introduces no new pipeline, scoring, or matching logic --
`ark/ui/logic.py` is a thin, Streamlit-free layer over
`ark.experiment`/`ark.evaluator`/`ark.harness`, and these tests exercise it
directly, with no Streamlit installation required at all (that's the whole
point of keeping widget code out of logic.py -- see that module's
docstring). A guarded, `unittest.skipUnless`-gated block at the bottom
additionally drives the real Streamlit page end-to-end via Streamlit's own
`AppTest` headless-testing API, IF Streamlit happens to be installed in the
environment running these tests -- it is not installed in the environment
this milestone was built in (no network access to `pip install` it), so
that block is expected to skip here; it is included so the real page gets
exercised automatically in any environment where the `ui` extra is
installed, rather than only ever being smoke-tested by hand.

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests.
"""

from __future__ import annotations

import ast
import inspect
import json
import unittest
from pathlib import Path

from ark.evaluator.analysis import analysis_to_json
from ark.evaluator.report import report_to_json
from ark.generator.generator import generate_estate
from ark.harness.scripted_client import ScriptedAgentClient
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES
from ark.ui import logic

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "ark" / "ui"


class TestAgentSelection(unittest.TestCase):
    def test_scripted_agent_is_always_available(self):
        self.assertIn(logic.AGENT_CHOICE_SCRIPTED, logic.available_agent_choices())

    def test_anthropic_agent_is_always_selectable_in_the_ui(self):
        """The Anthropic option is always listed -- readiness is
        surfaced separately via anthropic_missing_requirements(), not by
        hiding the option (see test_missing_requirements_* below)."""
        self.assertIn(logic.AGENT_CHOICE_ANTHROPIC, logic.available_agent_choices())

    def test_missing_requirements_is_empty_only_when_fully_configured(self):
        problems = logic.anthropic_missing_requirements()
        try:
            import anthropic  # noqa: F401
            package_installed = True
        except ImportError:
            package_installed = False
        import os

        key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if package_installed and key_present:
            self.assertEqual(problems, [])
        else:
            self.assertGreater(len(problems), 0)

    def test_missing_requirements_mentions_the_api_key_when_absent(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            problems = logic.anthropic_missing_requirements()
        self.assertTrue(any("ANTHROPIC_API_KEY" in p for p in problems))

    def test_gemini_agent_is_always_selectable_in_the_ui(self):
        """Same "always listed, readiness surfaced separately" design as
        the Anthropic option -- see gemini_missing_requirements() below."""
        self.assertIn(logic.AGENT_CHOICE_GEMINI, logic.available_agent_choices())

    def test_gemini_missing_requirements_is_empty_only_when_fully_configured(self):
        import os

        problems = logic.gemini_missing_requirements()
        try:
            from google import genai  # noqa: F401
            package_installed = True
        except ImportError:
            package_installed = False

        key_present = bool(os.environ.get("GEMINI_API_KEY"))
        if package_installed and key_present:
            self.assertEqual(problems, [])
        else:
            self.assertGreater(len(problems), 0)

    def test_gemini_missing_requirements_mentions_the_api_key_when_absent(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            problems = logic.gemini_missing_requirements()
        self.assertTrue(any("GEMINI_API_KEY" in p for p in problems))

    def test_build_agent_client_for_scripted_choice_returns_a_scripted_agent_client(self):
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        self.assertIsInstance(client, ScriptedAgentClient)

    def test_scripted_agent_client_makes_no_network_call_and_needs_no_api_key(self):
        """The whole point of the default demo agent: it must work with
        zero environment configuration. Calling .generate() must not
        raise even with no ANTHROPIC_API_KEY anywhere in the environment."""
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        response = client.generate("## a.yaml\n```\ntitle: Some Normal API\n```")
        parsed = json.loads(response)
        self.assertIn("findings", parsed)

    def test_unknown_agent_choice_raises(self):
        with self.assertRaises(ValueError):
            logic.build_agent_client("not a real choice")


class TestTrajectorySpecBuilding(unittest.TestCase):
    def test_hand_authored_source_produces_correct_labels_and_seeds(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_MILESTONE1, "level_1_minor", seed=5, num_trajectories=3
        )
        self.assertEqual([s.label for s in specs], ["level_1_minor-seed5", "level_1_minor-seed6", "level_1_minor-seed7"])
        self.assertEqual([s.seed for s in specs], [5, 6, 7])
        self.assertTrue(all(s.baseline_estate_path == logic.MILESTONE1_GROUND_TRUTH for s in specs))
        self.assertTrue(all(s.generator_config is None for s in specs))

    def test_generator_source_produces_generator_configs(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, "level_2_structural", seed=1, num_trajectories=2
        )
        self.assertTrue(all(s.generator_config is not None for s in specs))
        self.assertTrue(all(s.baseline_estate_path is None for s in specs))
        self.assertEqual([s.generator_config.seed for s in specs], [1, 2])

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "not_a_real_profile", seed=1, num_trajectories=1)

    def test_unknown_estate_source_raises(self):
        with self.assertRaises(ValueError):
            logic.build_trajectory_specs("not a real source", "level_1_minor", seed=1, num_trajectories=1)

    def test_zero_trajectories_raises(self):
        with self.assertRaises(ValueError):
            logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_1_minor", seed=1, num_trajectories=0)

    def test_domain_is_ignored_for_non_domain_profiles_and_other_estate_source(self):
        """The new `domain` keyword must not change anything for any
        profile/estate-source combination other than
        (domain_injection_preview, generator) -- passing it anyway (e.g. a
        stray value left over from a prior UI selection) should have zero
        effect: hand-authored specs still carry no generator_config at all,
        and other profiles' specs are unaffected."""
        with_domain = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_MILESTONE1, "level_2_structural", seed=1, num_trajectories=2, domain="finance"
        )
        without_domain = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_MILESTONE1, "level_2_structural", seed=1, num_trajectories=2
        )
        self.assertEqual([s.label for s in with_domain], [s.label for s in without_domain])
        self.assertTrue(all(s.generator_config is None for s in with_domain))

    def test_domain_flows_into_generator_config_only_for_generator_source(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, logic.DOMAIN_PROFILE_NAME, seed=1, num_trajectories=1, domain="finance"
        )
        self.assertEqual(specs[0].generator_config.domain, "finance")

    def test_domain_defaults_to_none_when_not_given(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, logic.DOMAIN_PROFILE_NAME, seed=1, num_trajectories=1
        )
        self.assertIsNone(specs[0].generator_config.domain)


class TestDomainInjectionUiWiring(unittest.TestCase):
    """Regression coverage for the "domain_injection_preview produces zero
    mutations because the UI has no way to set GroundTruthEstate.domain"
    bug: confirms the fix in build_trajectory_specs() actually produces a
    non-zero mutation count end-to-end, once a domain is supplied via the
    UI-facing `domain` keyword. Uses ark.mutation.engine.run_trajectory()
    directly on the generated estate -- the engine itself, no agent, no
    experiment batch -- exactly the same "not a trajectory batch" testing
    convention tests/test_domain_component_injection.py already
    established, so this does not count against this task's one-
    confirmation-run budget."""

    def test_domain_set_via_the_ui_wiring_produces_a_nonzero_mutation_count(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, logic.DOMAIN_PROFILE_NAME, seed=1, num_trajectories=1, domain="finance",
        )
        generated = generate_estate(specs[0].generator_config)
        result = run_trajectory(generated.estate, PROFILES[logic.DOMAIN_PROFILE_NAME], seed=specs[0].seed)

        self.assertGreater(len(result.ledger.records), 0)
        self.assertTrue(all(r.transformation_type == "domain_implausible_component" for r in result.ledger.records))

    def test_domain_left_unset_via_the_ui_wiring_still_realizes_zero_mutations(self):
        """The flip side of the fix -- confirms build_trajectory_specs()
        without a domain still reproduces the exact pre-fix (documented,
        correct) zero-candidate behavior, so the fix only ADDS a way to
        set domain, it doesn't change what happens when one isn't set."""
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, logic.DOMAIN_PROFILE_NAME, seed=1, num_trajectories=1,
        )
        generated = generate_estate(specs[0].generator_config)
        result = run_trajectory(generated.estate, PROFILES[logic.DOMAIN_PROFILE_NAME], seed=specs[0].seed)

        self.assertEqual(result.ledger.records, [])


class TestRunUiExperimentWithoutApiKeys(unittest.TestCase):
    """The exact scenario the milestone's own testing requirement names:
    "UI can run with scripted agent without API keys.\""""

    def test_full_experiment_runs_offline_and_produces_reports_and_analysis(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_MILESTONE1, "level_1_minor", seed=1, num_trajectories=2
        )
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        run_result = logic.run_ui_experiment(specs, client)

        self.assertEqual(len(run_result.reports), 2)
        self.assertEqual(run_result.analysis.report_count, 2)
        self.assertEqual(set(logic.trajectory_labels(run_result)), {"level_1_minor-seed1", "level_1_minor-seed2"})
        self.assertEqual(set(run_result.artifacts_by_label.keys()), set(logic.trajectory_labels(run_result)))

    def test_generator_estate_source_also_runs_offline(self):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, "level_2_structural", seed=1, num_trajectories=1
        )
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        run_result = logic.run_ui_experiment(specs, client)
        self.assertEqual(len(run_result.reports), 1)
        self.assertIsNotNone(run_result.reports[0].metadata.generator_version)


class TestResultsDisplayExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_2_structural", seed=7, num_trajectories=1)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        cls.run_result = logic.run_ui_experiment(specs, client)
        cls.report = cls.run_result.reports[0]

    def test_environment_summary_rows_contain_the_required_fields(self):
        rows = logic.environment_summary_rows(self.report)
        for required in ("Applications", "Flows", "Mutation count", "Complexity score"):
            self.assertIn(required, rows)
        self.assertEqual(rows["Applications"], self.report.environment_summary.application_count)
        self.assertEqual(rows["Mutation count"], self.report.transformation_summary.total_mutations)

    def test_agent_performance_rows_contain_the_required_fields(self):
        rows = logic.agent_performance_rows(self.report)
        for required in (
            "Category precision", "Category recall", "Category F1",
            "Entity localization accuracy", "Brier score", "Expected Calibration Error (ECE)",
        ):
            self.assertIn(required, rows)
        self.assertEqual(rows["Category F1"], self.report.agent_performance.category_metrics.f1)

    def test_failure_analysis_rows_contain_the_four_named_buckets(self):
        rows = logic.failure_analysis_rows(self.report)
        joined_labels = " ".join(rows.keys()).lower()
        for required_phrase in ("missed issues", "hallucinations", "wrong diagnosis", "overconfidence"):
            self.assertIn(required_phrase, joined_labels)

    def test_failure_analysis_row_counts_match_the_real_report(self):
        rows = logic.failure_analysis_rows(self.report)
        fa = self.report.failure_analysis
        counts = {len(v) for v in rows.values()}
        real_counts = {
            len(fa.missed_issues), len(fa.hallucinated_findings),
            len(fa.correct_location_incorrect_diagnosis), len(fa.overconfidence_patterns),
            len(fa.wrong_category_predictions),
        }
        self.assertEqual(counts, real_counts)

    def test_issue_rows_matches_report_issues_count(self):
        rows = logic.issue_rows(self.report)
        self.assertEqual(len(rows), len(self.report.issues))


class TestResearchVisualizationRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_2_structural", seed=1, num_trajectories=3)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        cls.run_result = logic.run_ui_experiment(specs, client)
        cls.analysis = cls.run_result.analysis

    def test_complexity_vs_performance_rows_one_per_bucket(self):
        rows = logic.complexity_vs_performance_rows(self.analysis)
        self.assertEqual(len(rows), len(self.analysis.complexity_analysis.buckets))
        for row in rows:
            self.assertIn("complexity_bucket", row)
            self.assertIn("category_f1", row)

    def test_correlation_rows_one_per_metric(self):
        rows = logic.complexity_correlation_rows(self.analysis)
        self.assertEqual(len(rows), len(self.analysis.complexity_analysis.correlations))

    def test_transformation_impact_rows_one_per_transformation_type(self):
        rows = logic.transformation_impact_rows(self.analysis)
        self.assertEqual(len(rows), len(self.analysis.transformation_impact_analysis.by_transformation_type))

    def test_calibration_drift_rows_one_per_point(self):
        rows = logic.calibration_drift_rows(self.analysis)
        self.assertEqual(len(rows), len(self.analysis.calibration_drift_analysis.points))

    def test_complexity_scatter_rows_one_per_scoreable_trajectory(self):
        """One row per trajectory whose category_f1 is defined -- the
        trajectory-level data behind the scatter plot that replaced the
        bucketed "Complexity vs Performance" chart."""
        rows = logic.complexity_scatter_rows(self.run_result)
        scoreable_reports = [
            r for r in self.run_result.reports if r.agent_performance.category_metrics.f1 is not None
        ]
        self.assertEqual(len(rows), len(scoreable_reports))
        for row in rows:
            self.assertIn("trajectory_label", row)
            self.assertIn("complexity_score", row)
            self.assertIn("category_f1", row)
            self.assertIsNotNone(row["category_f1"])

    def test_complexity_scatter_rows_matches_report_fields_exactly(self):
        rows = logic.complexity_scatter_rows(self.run_result)
        by_label = {row["trajectory_label"]: row for row in rows}
        for report in self.run_result.reports:
            f1 = report.agent_performance.category_metrics.f1
            label = report.metadata.trajectory_id
            if f1 is None:
                self.assertNotIn(label, by_label)
            else:
                self.assertEqual(by_label[label]["complexity_score"], report.transformation_summary.complexity_score)
                self.assertEqual(by_label[label]["category_f1"], f1)

    def test_linear_trendline_with_two_or_more_points(self):
        rows = [
            {"complexity_score": 0.0, "category_f1": 0.9},
            {"complexity_score": 1.0, "category_f1": 0.7},
            {"complexity_score": 2.0, "category_f1": 0.5},
        ]
        trend = logic.linear_trendline(rows)
        self.assertIsNotNone(trend)
        self.assertAlmostEqual(trend["slope"], -0.2)
        self.assertAlmostEqual(trend["intercept"], 0.9)
        self.assertEqual(trend["min_x"], 0.0)
        self.assertEqual(trend["max_x"], 2.0)

    def test_linear_trendline_with_fewer_than_two_points_is_none(self):
        self.assertIsNone(logic.linear_trendline([]))
        self.assertIsNone(logic.linear_trendline([{"complexity_score": 1.0, "category_f1": 0.5}]))

    def test_linear_trendline_with_identical_x_values_is_none(self):
        """A vertical spread at one complexity value has no meaningful
        slope -- must not raise a division-by-zero, must return None."""
        rows = [
            {"complexity_score": 1.0, "category_f1": 0.5},
            {"complexity_score": 1.0, "category_f1": 0.9},
        ]
        self.assertIsNone(logic.linear_trendline(rows))

    def test_calibration_scatter_rows_one_per_trajectory_with_a_defined_brier_score(self):
        """One row per trajectory whose Brier score is defined -- the
        trajectory-level data behind the scatter plot that replaced the
        bucketed "Calibration Drift" line chart."""
        rows = logic.calibration_scatter_rows(self.run_result)
        scoreable_reports = [
            r for r in self.run_result.reports if r.agent_performance.calibration.brier_score is not None
        ]
        self.assertEqual(len(rows), len(scoreable_reports))
        for row in rows:
            self.assertIn("trajectory_label", row)
            self.assertIn("complexity_score", row)
            self.assertIn("brier_score", row)
            self.assertIn("ece", row)
            self.assertIsNotNone(row["brier_score"])

    def test_calibration_scatter_rows_matches_report_fields_exactly(self):
        rows = logic.calibration_scatter_rows(self.run_result)
        by_label = {row["trajectory_label"]: row for row in rows}
        for report in self.run_result.reports:
            calibration = report.agent_performance.calibration
            label = report.metadata.trajectory_id
            if calibration.brier_score is None:
                self.assertNotIn(label, by_label)
            else:
                self.assertEqual(
                    by_label[label]["complexity_score"], report.transformation_summary.complexity_score
                )
                self.assertEqual(by_label[label]["brier_score"], calibration.brier_score)
                self.assertEqual(by_label[label]["ece"], calibration.ece)


class TestTransformationImpactRowsWithZeroMutations(unittest.TestCase):
    """Regression coverage for the "Transformation Type Impact" chart crash
    (KeyError: "None of ['transformation_type'] are in the columns" from
    app.py's now-fixed pd.DataFrame(...).set_index("transformation_type")
    call): confirms transformation_impact_rows() -- the function app.py's
    guard is built around -- returns [] cleanly, without raising, for a
    real zero-mutation experiment, regardless of why zero mutations
    happened. Deliberately uses the exact scenario that surfaced this bug
    (domain_injection_preview against a domain-less generated estate) since
    it's a real, reproducible way to get an empty by_transformation_type --
    but the fix itself (in app.py) is generic and doesn't special-case this
    cause; see that guard's own comment."""

    @classmethod
    def setUpClass(cls):
        specs = logic.build_trajectory_specs(
            logic.ESTATE_SOURCE_GENERATOR, logic.DOMAIN_PROFILE_NAME, seed=1, num_trajectories=3,
        )
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        cls.run_result = logic.run_ui_experiment(specs, client)
        cls.analysis = cls.run_result.analysis

    def test_zero_mutations_were_actually_realized_in_this_scenario(self):
        """Sanity check that this setup really does reproduce the bug
        scenario -- if this ever stops being true (e.g. profile defaults
        change), the test below would stop meaning what it claims to."""
        self.assertEqual(self.analysis.transformation_impact_analysis.by_transformation_type, [])

    def test_transformation_impact_rows_returns_empty_list_without_raising(self):
        rows = logic.transformation_impact_rows(self.analysis)
        self.assertEqual(rows, [])

    def test_empty_rows_do_not_crash_dataframe_construction(self):
        """Mirrors exactly what app.py's fixed guard checks before ever
        calling pd.DataFrame(rows).set_index("transformation_type") --
        confirms the crash's precondition (an empty rows list) is exactly
        what this scenario produces, and that checking `not rows` (the
        guard's condition) correctly identifies it."""
        rows = logic.transformation_impact_rows(self.analysis)
        self.assertFalse(rows)  # this is the condition app.py's guard checks


class TestExperimentSummaryAndDirectionHints(unittest.TestCase):
    """"Add an Experiment Summary card" / "add clear labels indicating
    whether higher or lower values are better" -- both purely
    presentation, reading only already-computed values."""

    @classmethod
    def setUpClass(cls):
        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_2_structural", seed=1, num_trajectories=3)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        cls.run_result = logic.run_ui_experiment(specs, client)
        cls.analysis = cls.run_result.analysis

    def test_experiment_summary_rows_matches_analysis_experiment_summary_exactly(self):
        rows = logic.experiment_summary_rows(
            self.analysis,
            agent_model_used="ScriptedAgentClient (offline)",
            estate_source=logic.ESTATE_SOURCE_MILESTONE1,
            profile_name="level_2_structural",
        )
        summary = self.analysis.experiment_summary
        self.assertEqual(rows["Agent model used"], "ScriptedAgentClient (offline)")
        self.assertEqual(rows["Estate source"], logic.ESTATE_SOURCE_MILESTONE1)
        self.assertEqual(rows["Mutation profile"], "level_2_structural")
        self.assertEqual(rows["Trajectory count"], summary.trajectory_count)
        self.assertEqual(rows["Average complexity score"], summary.average_complexity_score)
        self.assertEqual(rows["Average category F1"], summary.average_category_f1)
        self.assertEqual(rows["Average localization accuracy"], summary.average_entity_localization_accuracy)
        self.assertEqual(rows["Average calibration error (ECE)"], summary.average_calibration_ece)

    def test_metric_direction_hint_for_known_metrics(self):
        self.assertEqual(logic.metric_direction_hint("Category F1"), " (higher is better)")
        self.assertEqual(logic.metric_direction_hint("Brier score"), " (lower is better)")
        self.assertEqual(logic.metric_direction_hint("Expected Calibration Error (ECE)"), " (lower is better)")
        self.assertEqual(logic.metric_direction_hint("Average calibration error (ECE)"), " (lower is better)")

    def test_metric_direction_hint_for_unknown_metric_is_empty(self):
        self.assertEqual(logic.metric_direction_hint("Total findings"), "")
        self.assertEqual(logic.metric_direction_hint("some made-up label"), "")


class TestArtifactViewerIsolation(unittest.TestCase):
    """"no evaluator leakage into agent artifact directory" -- the
    milestone's own testing requirement, checked both structurally
    (assert_artifacts_contain_no_evaluator_metadata) and against a real
    trajectory run."""

    def test_isolation_assertion_passes_on_real_artifacts(self):
        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_1_minor", seed=1, num_trajectories=1)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        run_result = logic.run_ui_experiment(specs, client)
        artifacts = logic.artifacts_for_label(run_result, specs[0].label)
        # Should not raise.
        logic.assert_artifacts_contain_no_evaluator_metadata(artifacts)
        self.assertTrue(all(isinstance(k, str) and isinstance(v, str) for k, v in artifacts.items()))

    def test_isolation_assertion_catches_a_simulated_manifest_leak(self):
        """A deliberately-broken input (as if some future change
        accidentally merged manifest content into the artifacts dict) --
        the guard must catch this, not silently pass it through."""
        leaked = {"a.xml": "<flow/>", "manifest": {"entity_index": {}}}
        with self.assertRaises(AssertionError):
            logic.assert_artifacts_contain_no_evaluator_metadata(leaked)

    def test_isolation_assertion_catches_a_non_string_value(self):
        leaked = {"a.xml": {"not": "a string"}}
        with self.assertRaises(AssertionError):
            logic.assert_artifacts_contain_no_evaluator_metadata(leaked)

    def test_artifacts_for_label_matches_a_fresh_independent_render_exactly(self):
        """A wiring-level check in the same spirit as Milestone 7's spy
        test: what the Artifact Viewer would display for a trajectory
        must be byte-for-byte identical to that trajectory's real
        rendered.artifacts -- confirmed here by independently re-deriving
        the same deterministic trajectory (same seed/profile/baseline)
        and rendering it a second time, then comparing."""
        from ark.adapters.mulesoft.adapter import MuleSoftAdapter
        from ark.core.validate import validate_ground_truth
        from ark.mutation.engine import run_trajectory
        from ark.mutation.profiles import PROFILES

        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_2_structural", seed=7, num_trajectories=1)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        run_result = logic.run_ui_experiment(specs, client)
        ui_artifacts = logic.artifacts_for_label(run_result, specs[0].label)

        baseline = validate_ground_truth(logic.MILESTONE1_GROUND_TRUTH)
        independent_result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=7)
        independent_rendered = MuleSoftAdapter().render(independent_result.transformed_estate)

        self.assertEqual(ui_artifacts, independent_rendered.artifacts)
        self.assertNotIn("manifest", ui_artifacts)
        for internal_id in independent_rendered.manifest["entity_index"]:
            self.assertNotIn(internal_id, ui_artifacts.keys())


class TestAnthropicAgentIsolation(unittest.TestCase):
    """"no manifest/ledger/ground truth is passed to the agent" -- checked
    specifically for the real Anthropic-backed agent path this session
    added, using a fake underlying SDK client (same pattern
    tests/test_milestone7.py's TestAnthropicAgentClient already uses) so
    this needs no real `anthropic` install, no API key, and makes no
    network call."""

    class _FakeBlock:
        def __init__(self, text: str):
            self.type = "text"
            self.text = text

    class _FakeResponse:
        def __init__(self, text: str):
            self.content = [TestAnthropicAgentIsolation._FakeBlock(text)]

    class _FakeMessages:
        def __init__(self):
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return TestAnthropicAgentIsolation._FakeResponse('{"findings": []}')

    class _FakeClient:
        def __init__(self):
            self.messages = TestAnthropicAgentIsolation._FakeMessages()

    def test_ui_can_select_and_build_the_anthropic_agent_client(self):
        from integrations.anthropic_agent_client import AnthropicAgentClient

        self.assertIn(logic.AGENT_CHOICE_ANTHROPIC, logic.available_agent_choices())
        fake = self._FakeClient()
        # build_agent_client() always constructs its own default client;
        # to avoid a real SDK/network dependency in this test we
        # construct AnthropicAgentClient directly with the fake, using
        # the same ANTHROPIC_DEMO_MODEL the UI requests.
        client = AnthropicAgentClient(client=fake, model=logic.ANTHROPIC_DEMO_MODEL)
        self.assertEqual(client.generate("hello"), '{"findings": []}')

    def test_only_rendered_artifacts_reach_the_anthropic_client_during_a_real_run(self):
        """The same wiring-level guarantee Milestone 7 proved for the
        generic harness call site, re-checked specifically against the
        real Anthropic agent path: run_agent_harness() must receive
        exactly rendered.artifacts, never the manifest, ledger, or
        transformed estate -- and the fake SDK's own messages.create()
        call must never see manifest-shaped content either."""
        from unittest import mock

        from ark.adapters.mulesoft.adapter import MuleSoftAdapter
        from ark.core.validate import validate_ground_truth
        from ark.experiment.spec import TrajectorySpec
        from ark.harness import runner as harness_runner_module
        from ark.mutation.engine import run_trajectory
        from ark.mutation.profiles import PROFILES
        from integrations.anthropic_agent_client import AnthropicAgentClient

        fake = self._FakeClient()
        anthropic_client = AnthropicAgentClient(client=fake, model=logic.ANTHROPIC_DEMO_MODEL)

        baseline = validate_ground_truth(logic.MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=7)
        rendered = MuleSoftAdapter().render(result.transformed_estate)

        with mock.patch(
            "ark.experiment.runner.run_agent_harness", wraps=harness_runner_module.run_agent_harness
        ) as spy:
            spec = TrajectorySpec(
                label="anthropic-isolation-check", profile_name="level_2_structural", seed=7,
                baseline_estate_path=logic.MILESTONE1_GROUND_TRUTH,
            )
            from ark.experiment.runner import run_trajectory_spec

            run_trajectory_spec(spec, anthropic_client)

        self.assertEqual(spy.call_count, 1)
        (received_artifacts, received_agent_client) = spy.call_args[0]
        self.assertEqual(received_artifacts, rendered.artifacts)
        self.assertIs(received_agent_client, anthropic_client)
        self.assertNotIn("entity_index", received_artifacts)
        self.assertNotIn("dependencies", received_artifacts)

        # And: what actually got sent to the (fake) Anthropic API call is
        # exactly one prompt built from those same artifacts -- never a
        # manifest, ledger, or ground-truth object. (Checking that
        # individual entity-id STRINGS never appear in the prompt text
        # isn't a meaningful check on this particular estate -- several
        # of its ids are, by hand-authoring choice, literally substrings
        # of the rendered content itself, e.g. "api-order-status-
        # experience-v1"; see tests/test_milestone7.py's own note on
        # this exact false positive. The type/shape/call-count checks
        # above are the real, structural isolation guarantee.)
        self.assertEqual(len(fake.messages.calls), 1)
        sent_kwargs = fake.messages.calls[0]
        self.assertEqual(sent_kwargs["model"], logic.ANTHROPIC_DEMO_MODEL)
        prompt_sent = sent_kwargs["messages"][0]["content"]
        self.assertIsInstance(prompt_sent, str)
        self.assertNotIn("entity_index", prompt_sent)
        self.assertNotIn('"dependencies"', prompt_sent)


class TestGeminiAgentIsolation(unittest.TestCase):
    """Same guarantee as TestAnthropicAgentIsolation above, re-checked for
    the Gemini-backed agent path, using a fake underlying SDK client so
    this needs no real `google-genai` install, no API key, and makes no
    network call."""

    class _FakeResponse:
        def __init__(self, text: str):
            self.text = text

    class _FakeModels:
        def __init__(self):
            self.calls: list[dict] = []

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return TestGeminiAgentIsolation._FakeResponse('{"findings": []}')

    class _FakeClient:
        def __init__(self):
            self.models = TestGeminiAgentIsolation._FakeModels()

    def test_ui_can_select_and_build_the_gemini_agent_client(self):
        from integrations.gemini_agent_client import GeminiAgentClient

        self.assertIn(logic.AGENT_CHOICE_GEMINI, logic.available_agent_choices())
        fake = self._FakeClient()
        # build_agent_client() always constructs its own default client;
        # to avoid a real SDK/network dependency in this test we
        # construct GeminiAgentClient directly with the fake, using
        # the same GEMINI_DEMO_MODEL the UI requests.
        client = GeminiAgentClient(client=fake, model=logic.GEMINI_DEMO_MODEL)
        self.assertEqual(client.generate("hello"), '{"findings": []}')

    def test_only_rendered_artifacts_reach_the_gemini_client_during_a_real_run(self):
        """The same wiring-level guarantee proved for the Anthropic path,
        re-checked for the Gemini agent: run_agent_harness() must receive
        exactly rendered.artifacts, never the manifest, ledger, or
        transformed estate -- and the fake SDK's own
        generate_content() call must never see manifest-shaped content
        either."""
        from unittest import mock

        from ark.adapters.mulesoft.adapter import MuleSoftAdapter
        from ark.core.validate import validate_ground_truth
        from ark.experiment.spec import TrajectorySpec
        from ark.harness import runner as harness_runner_module
        from ark.mutation.engine import run_trajectory
        from ark.mutation.profiles import PROFILES
        from integrations.gemini_agent_client import GeminiAgentClient

        fake = self._FakeClient()
        gemini_client = GeminiAgentClient(client=fake, model=logic.GEMINI_DEMO_MODEL)

        baseline = validate_ground_truth(logic.MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=7)
        rendered = MuleSoftAdapter().render(result.transformed_estate)

        with mock.patch(
            "ark.experiment.runner.run_agent_harness", wraps=harness_runner_module.run_agent_harness
        ) as spy:
            spec = TrajectorySpec(
                label="gemini-isolation-check", profile_name="level_2_structural", seed=7,
                baseline_estate_path=logic.MILESTONE1_GROUND_TRUTH,
            )
            from ark.experiment.runner import run_trajectory_spec

            run_trajectory_spec(spec, gemini_client)

        self.assertEqual(spy.call_count, 1)
        (received_artifacts, received_agent_client) = spy.call_args[0]
        self.assertEqual(received_artifacts, rendered.artifacts)
        self.assertIs(received_agent_client, gemini_client)
        self.assertNotIn("entity_index", received_artifacts)
        self.assertNotIn("dependencies", received_artifacts)

        # And: what actually got sent to the (fake) Gemini API call is
        # exactly one prompt built from those same artifacts -- never a
        # manifest, ledger, or ground-truth object. (See
        # TestAnthropicAgentIsolation's own note, and
        # tests/test_milestone7.py's, on why a substring check against
        # individual entity-id VALUES would be a false positive on this
        # hand-authored estate; the structural checks below are the real
        # guarantee.)
        self.assertEqual(len(fake.models.calls), 1)
        sent_kwargs = fake.models.calls[0]
        self.assertEqual(sent_kwargs["model"], logic.GEMINI_DEMO_MODEL)
        prompt_sent = sent_kwargs["contents"]
        self.assertIsInstance(prompt_sent, str)
        self.assertNotIn("entity_index", prompt_sent)
        self.assertNotIn('"dependencies"', prompt_sent)


class TestAgentModelLabel(unittest.TestCase):
    """"Agent Model Used" card -- must show the real configured model, not
    a hardcoded per-choice label, and must not crash for any agent choice."""

    def test_scripted_agent_gets_the_fixed_offline_label(self):
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        self.assertEqual(logic.agent_model_label(client), logic.AGENT_CHOICE_SCRIPTED)

    def test_anthropic_agent_label_includes_the_real_model_value(self):
        from integrations.anthropic_agent_client import AnthropicAgentClient

        client = AnthropicAgentClient(client=object(), model="some-test-model")
        label = logic.agent_model_label(client)
        self.assertIn("some-test-model", label)
        self.assertIn("AnthropicAgentClient", label)

    def test_gemini_agent_label_includes_the_real_model_value(self):
        from integrations.gemini_agent_client import GeminiAgentClient

        client = GeminiAgentClient(client=object(), model="some-other-test-model")
        label = logic.agent_model_label(client)
        self.assertIn("some-other-test-model", label)
        self.assertIn("GeminiAgentClient", label)

    def test_label_reflects_the_model_actually_passed_to_build_agent_client(self):
        """Wiring-level check: build_agent_client() for the API choices
        passes ANTHROPIC_DEMO_MODEL/GEMINI_DEMO_MODEL through to the real
        client, and agent_model_label() reads that same value back --
        confirmed here against fake SDK clients so no network/API key is
        needed."""
        from unittest import mock

        from integrations.anthropic_agent_client import AnthropicAgentClient

        class _FakeAnthropicSDKClient:
            class _Messages:
                def create(self, **kwargs):
                    raise AssertionError("not called in this test")

            def __init__(self):
                self.messages = self._Messages()

        with mock.patch.object(
            AnthropicAgentClient, "_build_default_client", return_value=_FakeAnthropicSDKClient()
        ):
            client = logic.build_agent_client(logic.AGENT_CHOICE_ANTHROPIC)
        self.assertIn(logic.ANTHROPIC_DEMO_MODEL, logic.agent_model_label(client))


class TestExportHelpers(unittest.TestCase):
    def test_export_report_json_matches_report_to_json(self):
        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_1_minor", seed=1, num_trajectories=1)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        run_result = logic.run_ui_experiment(specs, client)
        report = run_result.reports[0]
        self.assertEqual(logic.export_report_json(report), report_to_json(report))

    def test_export_analysis_json_matches_analysis_to_json(self):
        specs = logic.build_trajectory_specs(logic.ESTATE_SOURCE_MILESTONE1, "level_1_minor", seed=1, num_trajectories=1)
        client = logic.build_agent_client(logic.AGENT_CHOICE_SCRIPTED)
        run_result = logic.run_ui_experiment(specs, client)
        self.assertEqual(logic.export_analysis_json(run_result.analysis), analysis_to_json(run_result.analysis))


class TestImportBoundary(unittest.TestCase):
    """ark/ui/ (logic.py -- app.py can't be imported without Streamlit
    installed, so it's checked via a source-level AST scan instead, which
    needs no import at all) must import from ark.experiment, ark.evaluator,
    and ark.harness (the required architecture), and must never import
    ark.mutation.engine, ark.mutation.operators, or ark.mutation.ledger
    directly (those are mutation internals; the pipeline is always
    reached through ark.experiment.run_experiment())."""

    _FORBIDDEN_PREFIXES = ("ark.mutation.engine", "ark.mutation.operators", "ark.mutation.ledger")

    @staticmethod
    def _imported_module_names(source: str) -> set[str]:
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_logic_module_never_imports_mutation_internals(self):
        names = self._imported_module_names(inspect.getsource(logic))
        for name in names:
            for forbidden in self._FORBIDDEN_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"ark.ui.logic imports {name}, a forbidden mutation-internals module",
                )

    def test_app_module_source_never_imports_mutation_internals(self):
        """app.py imports streamlit, which may not be installed here --
        read and AST-parse its source directly rather than importing it,
        so this check works regardless of whether streamlit is present."""
        app_source = (UI_DIR / "app.py").read_text(encoding="utf-8")
        names = self._imported_module_names(app_source)
        for name in names:
            for forbidden in self._FORBIDDEN_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"ark.ui.app imports {name}, a forbidden mutation-internals module",
                )

    def test_ui_package_as_a_whole_imports_from_all_three_required_packages(self):
        """Not every individual file needs to import all three -- but the
        ark/ui package as a whole must, per the architecture requirement."""
        combined_names: set[str] = set()
        for path in UI_DIR.glob("*.py"):
            combined_names |= self._imported_module_names(path.read_text(encoding="utf-8"))

        for required_prefix in ("ark.experiment", "ark.evaluator", "ark.harness"):
            self.assertTrue(
                any(name == required_prefix or name.startswith(required_prefix + ".") for name in combined_names),
                f"ark/ui/*.py never imports anything from {required_prefix}",
            )


class TestStreamlitAppTestIfAvailable(unittest.TestCase):
    """Drives the real ark/ui/app.py end-to-end via Streamlit's own
    headless AppTest harness -- IF Streamlit is installed. Skipped in any
    environment (including the one this milestone was built in, which has
    no network access to `pip install streamlit`) where it isn't, per
    the `ui` extra's optional-dependency design. Included so this
    actually exercises the real page automatically wherever the extra is
    installed, rather than only ever being smoke-tested by hand."""

    @classmethod
    def setUpClass(cls):
        try:
            from streamlit.testing.v1 import AppTest  # noqa: F401
        except ImportError:
            cls.streamlit_available = False
        else:
            cls.streamlit_available = True

    def setUp(self):
        if not self.streamlit_available:
            self.skipTest("streamlit is not installed in this environment (optional 'ui' extra)")

    def test_app_runs_without_exception_and_can_run_a_scripted_experiment(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(UI_DIR / "app.py"))
        at.run(timeout=60)
        self.assertFalse(at.exception)

        at.button[0].click().run(timeout=120)
        self.assertFalse(at.exception)


if __name__ == "__main__":
    unittest.main()
