"""
Feature 2: wiring ark.validation.mulesoft_http_connector into the
trajectory pipeline as a standing, automatic step -- the follow-up flagged
by the prior renderer-fix session (see
Ark_Renderer_ConfigRef_Fix_Summary.md's follow-up note, and
ark/validation/pipeline.py's own module docstring for the granularity and
non-blocking-failure decisions this wiring is built on).

Scope: these are wiring-level tests -- confirming validation runs
automatically as part of a trajectory, that its results land on
EvaluationReport.rendering_validation as an additive, separate field, and
that a validation issue (or even a validation-side failure) never crashes
a trajectory or leaks into any agent-performance metric. They deliberately
do NOT re-test validate_http_connector_xml()'s own rules (already covered
by tests/test_http_connector_validation.py) or the renderer fix itself
(already covered by tests/test_milestone2.py's
TestHttpConnectorConfigRefsResolve).

No new trajectory batches are run here -- every test uses the existing
Milestone 1 hand-authored estate, same as every prior session in this
thread.

Session F addendum ("Wire HTTP Connector Validator Into the Pipeline"):
Task 0 of that session re-investigated whether this wiring already
existed, since a later report (domain_injection_preview-seed1) showed a
populated rendering_validation block and it wasn't obvious from that
report alone whether this file's own wiring was responsible. Confirmed,
with direct evidence (grepping every rendering_validation/
validate_rendered_estate_safe call site, and independently re-rendering
Milestone 1 and running validate_rendered_estate() against it by hand):
this wiring is exactly what this file already tests, has run
systematically and profile-agnostically since before that later session,
and nothing about it needed to change. The two test classes added at the
bottom of this file (TestCleanRenderedEstateReportsFullyValid,
TestDeferredDocNameBugStillPresent) close the two specific, real gaps
that investigation found in this file's existing coverage -- everything
else in Session F's scope was already satisfied by what's above.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path
from unittest import mock

from ark.adapters.base import RenderedEstate, TargetAdapter
from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.models import GroundTruthEstate
from ark.core.validate import validate_ground_truth
from ark.evaluator.report import EvaluationReport, report_from_dict, report_to_json
from ark.experiment.runner import run_trajectory_spec, run_trajectory_spec_with_artifacts
from ark.experiment.spec import TrajectorySpec
from ark.harness.scripted_client import ScriptedAgentClient
from ark.validation.pipeline import RenderingValidationSummary, validate_rendered_estate, validate_rendered_estate_safe

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = str(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")

_FIXED_AGENT_OUTPUT = {"findings": []}


def _make_spec(label: str, profile_name: str = "level_2_structural", seed: int = 7) -> TrajectorySpec:
    return TrajectorySpec(
        label=label, profile_name=profile_name, seed=seed, baseline_estate_path=MILESTONE1_GROUND_TRUTH,
    )


class TestGranularityDecisionHoldsForRealOutput(unittest.TestCase):
    """Documents, with an executable check (not just prose), the
    granularity decision ark/validation/pipeline.py's docstring makes:
    MuleSoftAdapter always renders global config + usage into the SAME
    per-application file, so per-file validation (no cross-file
    concatenation) is correct for every real Milestone 1 artifact."""

    def test_every_config_ref_and_its_matching_global_element_share_one_artifact_path(self):
        import re

        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)

        for path, xml in rendered.artifacts.items():
            if not path.endswith(".xml"):
                continue
            config_refs = re.findall(r'config-ref="([^"]+)"', xml)
            global_names = set(re.findall(r'<http:(?:listener|request)-config name="([^"]+)"', xml))
            for ref in config_refs:
                with self.subTest(path=path, config_ref=ref):
                    self.assertIn(
                        ref, global_names,
                        f"{path} uses config-ref={ref!r} but its matching global element "
                        f"isn't declared in this same file -- the per-file granularity "
                        f"assumption in ark/validation/pipeline.py would be wrong.",
                    )


class TestRenderingValidationRunsAutomatically(unittest.TestCase):
    def test_report_has_a_rendering_validation_field_populated_for_a_mulesoft_trajectory(self):
        report = run_trajectory_spec(_make_spec("wiring-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))
        self.assertIsInstance(report, EvaluationReport)
        self.assertIsNotNone(report.rendering_validation)
        self.assertIsInstance(report.rendering_validation, RenderingValidationSummary)

    def test_milestone1_estate_has_zero_config_ref_issues_end_to_end_via_the_automatic_pipeline_path(self):
        """The concrete confirmation this task's Definition of Done asks
        for: the same "zero config-ref issues" result the prior renderer-
        fix session got by calling the validator manually (8 -> 0 across
        the 4 real Milestone 1 artifacts -- see
        Ark_Renderer_ConfigRef_Fix_Summary.md), now produced automatically
        by run_trajectory_spec() with no manual validator call anywhere in
        this test.

        Deliberately scoped to config-ref issues specifically, not
        `validation.is_valid`/`total_issues` overall: the separate,
        pre-existing, still-unfixed doc:name attribute-namespace bug in
        the validator (flagged, not fixed, in the prior renderer-fix
        session's summary, and explicitly out of scope for this wiring
        task too -- see the "DO NOT TOUCH" list) means Milestone 1's real
        output still has a small number of non-config-ref issues today.
        Asserting `is_valid=True` here would be a false claim; asserting
        zero config-ref issues is the actual, honest, literal thing this
        session's renderer fix + wiring together guarantee."""
        report = run_trajectory_spec(_make_spec("milestone1-clean-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))
        validation = report.rendering_validation
        self.assertIsNone(validation.validation_error)
        self.assertEqual(validation.validator_name, "mulesoft_http_connector")
        config_ref_issues = [
            issue
            for issues in validation.issues_by_artifact.values()
            for issue in issues
            if issue["attribute"] == "config-ref"
        ]
        self.assertEqual(config_ref_issues, [])

    def test_rendering_validation_is_never_folded_into_agent_performance(self):
        """Structural check: AgentPerformanceSummary's own fields (matches
        report.py's dataclass definition) contain nothing
        validation-shaped -- rendering_validation only ever lives on
        EvaluationReport directly, as a sibling, never nested inside
        agent_performance."""
        report = run_trajectory_spec(_make_spec("sibling-field-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))
        agent_performance_field_names = {f.name for f in dataclasses.fields(report.agent_performance)}
        self.assertNotIn("rendering_validation", agent_performance_field_names)
        report_field_names = {f.name for f in dataclasses.fields(report)}
        self.assertIn("rendering_validation", report_field_names)

    def test_rendering_validation_never_reaches_the_agent(self):
        """Same isolation boundary test_milestone7.py's
        TestHarnessIsolation already established for the manifest/ledger/
        estate -- extended here to cover the new field. Spies on the real
        run_agent_harness call site and confirms nothing
        validation-shaped is present in what the agent was actually
        handed."""
        from ark.harness import runner as harness_runner_module

        with mock.patch(
            "ark.experiment.runner.run_agent_harness", wraps=harness_runner_module.run_agent_harness
        ) as spy:
            run_trajectory_spec(_make_spec("agent-isolation-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))

        self.assertEqual(spy.call_count, 1)
        (received_artifacts, _agent_client) = spy.call_args[0]
        self.assertNotIn("rendering_validation", received_artifacts)
        for content in received_artifacts.values():
            self.assertNotIn("ValidationIssue", content)
            self.assertNotIn("rendering_validation", content)


class _NonMuleSoftAdapter(TargetAdapter):
    """A minimal, real TargetAdapter that is NOT MuleSoftAdapter -- stands
    in for "some future adapter" to prove the HTTP-connector validator is
    only ever run against MuleSoft output, not blindly against anything a
    TargetAdapter happens to produce."""

    name = "not-mulesoft"

    def render(self, estate: GroundTruthEstate) -> RenderedEstate:
        return RenderedEstate(artifacts={"whatever.txt": "not mule xml at all"}, manifest={})


class TestNonMuleSoftAdapterIsNotValidated(unittest.TestCase):
    def test_rendering_validation_is_none_for_a_non_mulesoft_adapter(self):
        result = run_trajectory_spec_with_artifacts(
            _make_spec("non-mulesoft-check"),
            ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT),
            adapter=_NonMuleSoftAdapter(),
        )
        self.assertIsNone(result.report.rendering_validation)


class TestDeliberatelyBrokenRenderingIsNonBlocking(unittest.TestCase):
    """Reuses the exact dangling-config-ref scenario the prior renderer-fix
    session fixed and tested manually (mock.patch.object suppressing
    _render_http_connector_configs to simulate the old, buggy renderer) --
    but this time driven end-to-end through run_trajectory_spec(), to
    prove the issue surfaces as report data rather than crashing the
    pipeline or changing agent-performance scoring."""

    def test_dangling_config_ref_surfaces_as_a_non_blocking_issue_not_a_crash(self):
        with mock.patch(
            "ark.adapters.mulesoft.renderer._render_http_connector_configs", return_value=[]
        ):
            report = run_trajectory_spec(
                _make_spec("broken-rendering-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)
            )

        # No exception propagated -- the trajectory completed and returned
        # a normal report.
        self.assertIsInstance(report, EvaluationReport)
        validation = report.rendering_validation
        self.assertIsNotNone(validation)
        self.assertIsNone(validation.validation_error)
        self.assertFalse(validation.is_valid)
        self.assertGreater(validation.total_issues, 0)
        config_ref_issues = [
            issue
            for issues in validation.issues_by_artifact.values()
            for issue in issues
            if issue["attribute"] == "config-ref"
        ]
        self.assertTrue(config_ref_issues, "Expected at least one dangling config-ref issue.")

    def test_agent_performance_metrics_are_identical_whether_or_not_rendering_is_broken(self):
        """The strongest form of "non-blocking": corrupting the render
        (via the same monkeypatch above) must not change a single
        agent-performance number, since agent_performance is computed
        purely from ground truth + the agent's raw output + the manifest
        -- none of which the config-ref bug touches."""
        spec_clean = _make_spec("compare-clean")
        spec_broken = _make_spec("compare-broken")

        clean_report = run_trajectory_spec(spec_clean, ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))
        with mock.patch(
            "ark.adapters.mulesoft.renderer._render_http_connector_configs", return_value=[]
        ):
            broken_report = run_trajectory_spec(spec_broken, ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))

        self.assertEqual(
            dataclasses.asdict(clean_report.agent_performance),
            dataclasses.asdict(broken_report.agent_performance),
        )
        self.assertEqual(
            dataclasses.asdict(clean_report.research_hooks),
            dataclasses.asdict(broken_report.research_hooks),
        )
        # But the two reports' rendering_validation must differ -- the
        # broken one actually found a config-ref issue the clean one
        # didn't (both may still have the separate, unfixed, unrelated
        # doc:name issue -- see the config-ref-scoped test above for why
        # this compares config-ref counts specifically, not overall
        # is_valid/total_issues).
        def _config_ref_issue_count(validation: RenderingValidationSummary) -> int:
            return sum(
                1
                for issues in validation.issues_by_artifact.values()
                for issue in issues
                if issue["attribute"] == "config-ref"
            )

        self.assertEqual(_config_ref_issue_count(clean_report.rendering_validation), 0)
        self.assertGreater(_config_ref_issue_count(broken_report.rendering_validation), 0)


class TestUnexpectedValidationSideFailureIsNonBlocking(unittest.TestCase):
    """A different, rarer failure mode than a content issue: something
    goes wrong in the validation wiring ITSELF (not in the rendered XML).
    Also must never crash a trajectory -- validate_rendered_estate_safe()
    is the guarantee; this confirms it end-to-end through the real
    runner, not just as a unit test of the pipeline module alone."""

    def test_an_internal_validator_exception_degrades_to_a_validation_error_field(self):
        with mock.patch(
            "ark.experiment.runner.validate_rendered_estate_safe",
            side_effect=lambda rendered: RenderingValidationSummary(
                schema_version="0.1.0",
                validator_name="mulesoft_http_connector",
                is_valid=False,
                total_issues=0,
                issues_by_artifact={},
                validation_error="RuntimeError: simulated internal validator failure",
            ),
        ):
            report = run_trajectory_spec(
                _make_spec("validator-side-failure-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)
            )

        self.assertIsInstance(report, EvaluationReport)
        self.assertIsNotNone(report.rendering_validation.validation_error)
        self.assertIn("simulated internal validator failure", report.rendering_validation.validation_error)

    def test_validate_rendered_estate_safe_itself_never_raises_for_a_bad_input(self):
        """Unit-level guarantee behind the wiring-level test above: even a
        genuinely malformed RenderedEstate-like input can't escape as an
        exception."""

        class _NotARenderedEstate:
            """Deliberately missing `.artifacts` to trigger an
            AttributeError inside validate_rendered_estate()."""

        result = validate_rendered_estate_safe(_NotARenderedEstate())  # type: ignore[arg-type]
        self.assertIsInstance(result, RenderingValidationSummary)
        self.assertIsNotNone(result.validation_error)
        self.assertFalse(result.is_valid)


class TestRenderingValidationSerializationRoundTrip(unittest.TestCase):
    def test_rendering_validation_survives_json_round_trip(self):
        report = run_trajectory_spec(_make_spec("roundtrip-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))
        reloaded = report_from_dict(json.loads(report_to_json(report)))
        self.assertEqual(reloaded.rendering_validation, report.rendering_validation)

    def test_a_report_dict_with_no_rendering_validation_key_reconstructs_with_none(self):
        """Backward compatibility with reports serialized before this
        field existed (or produced by a caller that never passed
        rendering_validation to evaluate())."""
        report = run_trajectory_spec(_make_spec("backcompat-check"), ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT))
        data = json.loads(report_to_json(report))
        del data["rendering_validation"]
        reloaded = report_from_dict(data)
        self.assertIsNone(reloaded.rendering_validation)


class TestCleanRenderedEstateReportsFullyValid(unittest.TestCase):
    """The one gap Session F's Task 0 investigation found in this file's
    existing coverage: every other test that exercises real, adapter-
    rendered output (Milestone 1, generated estates) inevitably also hits
    the separate, pre-existing, still-unfixed doc:name attribute-namespace
    bug (see TestDeferredDocNameBugStillPresent below) -- so nothing here
    could previously assert is_valid=True/total_issues=0 without either
    overclaiming or depending on that bug being fixed first (out of scope
    for this session either way). A hand-written, independently-verified-
    valid XML fixture (same snippet shape
    tests/test_http_connector_validation.py's own TestValidDocuments class
    already establishes as valid against the real schema) sidesteps that
    entirely: this is a fixture-level RenderedEstate, not a real adapter
    render, specifically so it can be genuinely, provably clean."""

    _VALID_XML = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mule xmlns="http://www.mulesoft.org/schema/mule/core"\n'
        '      xmlns:http="http://www.mulesoft.org/schema/mule/http">\n'
        '  <http:listener-config name="HTTP_Listener_config" basePath="api">\n'
        '    <http:listener-connection host="0.0.0.0" port="8081"/>\n'
        "  </http:listener-config>\n"
        '  <http:request-config name="HTTP_Request_config">\n'
        '    <http:request-connection host="localhost" port="8082"/>\n'
        "  </http:request-config>\n"
        '  <flow name="server">\n'
        '    <http:listener path="/orders" allowedMethods="GET" config-ref="HTTP_Listener_config"/>\n'
        '    <http:request method="POST" path="/downstream" config-ref="HTTP_Request_config"/>\n'
        "  </flow>\n"
        "</mule>\n"
    )

    def test_a_genuinely_clean_rendered_estate_is_reported_fully_valid(self):
        rendered = RenderedEstate(
            artifacts={"CleanApp/src/main/mule/CleanApp.xml": self._VALID_XML},
            manifest={},
        )
        summary = validate_rendered_estate(rendered)

        self.assertTrue(summary.is_valid)
        self.assertEqual(summary.total_issues, 0)
        self.assertEqual(summary.issues_by_artifact, {})
        self.assertIsNone(summary.validation_error)

    def test_a_non_xml_artifact_alongside_the_clean_xml_is_ignored_not_flagged(self):
        """Confirms the existing "only .xml artifacts are validated"
        decision (pipeline.py's own module docstring) holds for the
        clean-fixture case too -- a .yaml sibling artifact shouldn't be
        able to introduce a false issue or a false is_valid=False."""
        rendered = RenderedEstate(
            artifacts={
                "CleanApp/src/main/mule/CleanApp.xml": self._VALID_XML,
                "CleanApp/src/main/resources/api-clean-app-v1.yaml": "openapi: 3.0.0\ninfo:\n  title: whatever\n",
            },
            manifest={},
        )
        summary = validate_rendered_estate(rendered)

        self.assertTrue(summary.is_valid)
        self.assertEqual(summary.total_issues, 0)


class TestDeferredDocNameBugStillPresent(unittest.TestCase):
    """Session F's Task 0 explicitly asks to reconfirm (not fix) the
    previously-deferred doc:name attribute-namespace bug. This is that
    reconfirmation as an executable check, not just a prose claim in a
    session summary -- run against the REAL Milestone 1 estate through
    the real, unmodified adapter, exactly the scenario the seed1
    domain_injection_preview report's own rendering_validation block
    (mentioned in this session's Context) independently corroborates."""

    def test_the_doc_name_attribute_bug_is_still_present_on_real_milestone1_output(self):
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)
        summary = validate_rendered_estate(rendered)

        doc_name_attribute = "{http://www.mulesoft.org/schema/mule/documentation}name"
        doc_name_issues = [
            issue
            for issues in summary.issues_by_artifact.values()
            for issue in issues
            if issue["attribute"] == doc_name_attribute
        ]

        self.assertTrue(
            doc_name_issues,
            "Expected the previously-deferred doc:name attribute-namespace bug to still be "
            "present -- if this now fails, the bug may have been fixed elsewhere; re-flag and "
            "update/remove this test deliberately rather than assuming it's stale.",
        )
        for issue in doc_name_issues:
            self.assertEqual(issue["element"], "http:request")
        # Not fixed here, per this session's own DO NOT TOUCH list -- this
        # test exists to reconfirm and re-flag, not to resolve it.
        self.assertFalse(summary.is_valid)


if __name__ == "__main__":
    unittest.main()
