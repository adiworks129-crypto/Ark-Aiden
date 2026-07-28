"""
Milestone 7 tests: the agent harness (ark/harness/), the experiment runner
(ark/experiment/), and the optional Anthropic-backed client
(integrations/anthropic_agent_client.py).

Milestone 7 introduces no new scoring, matching, or metrics -- these tests
check the isolation boundary (the agent only ever sees rendered artifact
contents), prompt/response-handling correctness, end-to-end orchestration,
and that the optional vendor-SDK dependency stays fully outside `ark/` and
fully optional.

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests. None of these tests require the `anthropic`
package to be installed or make any network call.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ark.core.validate import validate_ground_truth
from ark.evaluator.issues import derive_issues
from ark.evaluator.report import EvaluationReport
from ark.experiment.runner import run_experiment, run_trajectory_spec
from ark.experiment.spec import TrajectorySpec
from ark.generator.config import GeneratorConfig
from ark.harness import contract as contract_module
from ark.harness import prompt as prompt_module
from ark.harness import response_parsing as response_parsing_module
from ark.harness import runner as harness_runner_module
from ark.harness import scripted_client as scripted_client_module
from ark.harness.response_parsing import AgentResponseParsingError, extract_json_object
from ark.harness.runner import run_agent_harness
from ark.harness.scripted_client import ScriptedAgentClient
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = str(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")

SAMPLE_ARTIFACTS = {
    "customer-system/src/main/mule/customer-system.xml": "<flow name=\"Build Customer Response\"/>",
    "order-status-experience/src/main/resources/api-order-status-experience-v1.yaml": "title: OrderStatusE",
}


class TestPromptConstruction(unittest.TestCase):
    def test_prompt_contains_every_artifact_path_and_content(self):
        prompt = prompt_module.build_agent_prompt(SAMPLE_ARTIFACTS)
        for path, content in SAMPLE_ARTIFACTS.items():
            self.assertIn(path, prompt)
            self.assertIn(content, prompt)

    def test_prompt_is_deterministic_regardless_of_dict_insertion_order(self):
        reordered = dict(reversed(list(SAMPLE_ARTIFACTS.items())))
        self.assertEqual(
            prompt_module.build_agent_prompt(SAMPLE_ARTIFACTS),
            prompt_module.build_agent_prompt(reordered),
        )

    def test_prompt_lists_the_real_issue_type_taxonomy(self):
        from ark.evaluator.schema import ISSUE_TYPE_TAXONOMY

        prompt = prompt_module.build_agent_prompt(SAMPLE_ARTIFACTS)
        for issue_type in ISSUE_TYPE_TAXONOMY:
            self.assertIn(issue_type, prompt)

    def test_prompt_requests_the_exact_required_json_fields(self):
        prompt = prompt_module.build_agent_prompt(SAMPLE_ARTIFACTS)
        for field_name in (
            "artifact_reference", "entity_reference", "issue_type", "explanation", "confidence",
        ):
            self.assertIn(field_name, prompt)


class TestResponseParsing(unittest.TestCase):
    def test_bare_json_object(self):
        result = extract_json_object('{"findings": []}')
        self.assertEqual(result, {"findings": []})

    def test_fenced_json_object(self):
        raw = 'Sure, here you go:\n```json\n{"findings": []}\n```\nHope that helps!'
        result = extract_json_object(raw)
        self.assertEqual(result, {"findings": []})

    def test_unfenced_json_with_surrounding_prose(self):
        raw = 'Here is my analysis: {"findings": []} -- let me know if you need more.'
        result = extract_json_object(raw)
        self.assertEqual(result, {"findings": []})

    def test_no_json_present_raises(self):
        with self.assertRaises(AgentResponseParsingError):
            extract_json_object("I couldn't find any issues, sorry!")

    def test_bare_json_list_is_rejected_not_silently_accepted(self):
        """A JSON array isn't a valid agent-output shape (it must be an
        object with a "findings" key) -- extract_json_object should not
        pretend a list is fine just because it's valid JSON."""
        with self.assertRaises(AgentResponseParsingError):
            extract_json_object('[{"artifact_reference": "a"}]')


class TestScriptedAgentClient(unittest.TestCase):
    def test_fixed_always_returns_the_same_response(self):
        client = ScriptedAgentClient.fixed({"findings": []})
        self.assertEqual(client.generate("prompt A"), client.generate("prompt B"))

    def test_records_every_prompt_it_was_asked_to_respond_to(self):
        client = ScriptedAgentClient.fixed({"findings": []})
        client.generate("first")
        client.generate("second")
        self.assertEqual(client.prompts_received, ["first", "second"])

    def test_custom_responder_can_depend_on_the_prompt(self):
        client = ScriptedAgentClient(lambda prompt: json.dumps({"findings": [], "prompt_len": len(prompt)}))
        response = json.loads(client.generate("hello"))
        self.assertEqual(response["prompt_len"], len("hello"))


class TestRunAgentHarness(unittest.TestCase):
    def test_end_to_end_returns_the_agent_output_dict(self):
        expected = {
            "findings": [
                {
                    "artifact_reference": "a.xml",
                    "entity_reference": "X",
                    "issue_type": "naming_drift",
                    "explanation": "x",
                    "confidence": 0.8,
                }
            ]
        }
        client = ScriptedAgentClient.fixed(expected)
        result = run_agent_harness(SAMPLE_ARTIFACTS, client)
        self.assertEqual(result, expected)

    def test_required_parameter_is_a_plain_dict_not_a_richer_object(self):
        """The isolation boundary made concrete: run_agent_harness's first
        parameter is annotated dict[str, str], never RenderedEstate or
        anything ground-truth-shaped."""
        signature = inspect.signature(run_agent_harness)
        params = list(signature.parameters.values())
        self.assertEqual(params[0].name, "artifacts")
        self.assertIn("dict", str(params[0].annotation))
        self.assertNotIn("RenderedEstate", str(params[0].annotation))


class TestHarnessIsolation(unittest.TestCase):
    """No module under ark/harness/ may import ark.core.models,
    ark.mutation.*, or ark.adapters.* (generic or MuleSoft-specific) --
    it must be able to do its entire job with a plain dict[str, str]."""

    _HARNESS_MODULES = (
        contract_module, prompt_module, response_parsing_module,
        scripted_client_module, harness_runner_module,
    )
    _FORBIDDEN_PREFIXES = ("ark.core.models", "ark.mutation", "ark.adapters")

    def test_no_harness_module_imports_ground_truth_ledger_or_adapters(self):
        for module in self._HARNESS_MODULES:
            tree = ast.parse(inspect.getsource(module))
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
                        f"{module.__name__} imports {name}, which starts with forbidden prefix {forbidden}",
                    )

    def test_harness_call_site_receives_only_artifacts_never_the_manifest(self):
        """A stronger, wiring-level check alongside the import-boundary
        check above: spy on the exact call ark.experiment.runner makes
        into run_agent_harness for a real trajectory, and confirm it
        received `rendered.artifacts` -- content-equal to the real
        artifacts dict -- and nothing manifest-shaped (no "entity_index"/
        "dependencies" keys, which only ever appear on the manifest, not
        on the artifacts dict of file-path -> file-contents)."""
        baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=7)
        from ark.adapters.mulesoft.adapter import MuleSoftAdapter

        rendered = MuleSoftAdapter().render(result.transformed_estate)

        with mock.patch(
            "ark.experiment.runner.run_agent_harness", wraps=harness_runner_module.run_agent_harness
        ) as spy:
            spec = TrajectorySpec(
                label="spy-check", profile_name="level_2_structural", seed=7,
                baseline_estate_path=MILESTONE1_GROUND_TRUTH,
            )
            run_trajectory_spec(spec, ScriptedAgentClient.fixed({"findings": []}))

        self.assertEqual(spy.call_count, 1)
        (received_artifacts, _agent_client) = spy.call_args[0]
        self.assertEqual(received_artifacts, rendered.artifacts)
        self.assertNotIn("entity_index", received_artifacts)
        self.assertNotIn("dependencies", received_artifacts)


class TestTrajectorySpec(unittest.TestCase):
    def test_requires_exactly_one_estate_source(self):
        with self.assertRaises(ValueError):
            TrajectorySpec(label="both", profile_name="level_1_minor", seed=1,
                            baseline_estate_path=MILESTONE1_GROUND_TRUTH,
                            generator_config=GeneratorConfig(seed=1))

    def test_requires_at_least_one_estate_source(self):
        with self.assertRaises(ValueError):
            TrajectorySpec(label="neither", profile_name="level_1_minor", seed=1)

    def test_baseline_path_only_is_valid(self):
        spec = TrajectorySpec(
            label="ok", profile_name="level_1_minor", seed=1, baseline_estate_path=MILESTONE1_GROUND_TRUTH,
        )
        self.assertEqual(spec.baseline_estate_path, MILESTONE1_GROUND_TRUTH)

    def test_generator_config_only_is_valid(self):
        spec = TrajectorySpec(
            label="ok", profile_name="level_1_minor", seed=1, generator_config=GeneratorConfig(seed=1),
        )
        self.assertIsInstance(spec.generator_config, GeneratorConfig)


class TestRunTrajectorySpec(unittest.TestCase):
    def test_hand_authored_estate_produces_a_valid_report(self):
        spec = TrajectorySpec(
            label="m1-level1-seed1", profile_name="level_1_minor", seed=1,
            baseline_estate_path=MILESTONE1_GROUND_TRUTH,
        )
        report = run_trajectory_spec(spec, ScriptedAgentClient.fixed({"findings": []}))

        self.assertIsInstance(report, EvaluationReport)
        self.assertEqual(report.metadata.trajectory_id, "m1-level1-seed1")
        self.assertIsNone(report.metadata.generator_version)
        baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        real_issue_count = len(derive_issues(run_trajectory(baseline, PROFILES["level_1_minor"], seed=1).ledger))
        self.assertEqual(len(report.failure_analysis.missed_issues), real_issue_count)

    def test_generator_config_source_populates_generator_version(self):
        spec = TrajectorySpec(
            label="gen-level1-seed1", profile_name="level_1_minor", seed=1,
            generator_config=GeneratorConfig(seed=1),
        )
        report = run_trajectory_spec(spec, ScriptedAgentClient.fixed({"findings": []}))
        self.assertIsNotNone(report.metadata.generator_version)

    def test_agent_is_never_handed_the_manifest_ledger_or_estate_object(self):
        """Use a spy responder to capture exactly what prompt text the
        agent received, and confirm no manifest/ledger/estate object (or
        its repr) ever reaches it -- only rendered artifact content."""
        captured_prompts: list[str] = []
        client = ScriptedAgentClient(lambda p: captured_prompts.append(p) or json.dumps({"findings": []}))

        spec = TrajectorySpec(
            label="isolation-check", profile_name="level_2_structural", seed=7,
            baseline_estate_path=MILESTONE1_GROUND_TRUTH,
        )
        run_trajectory_spec(spec, client)

        self.assertEqual(len(captured_prompts), 1)
        prompt_text = captured_prompts[0]
        # A real ground-truth entity id (application/API/flow ids all
        # follow this prefix convention -- see ark/core/models.py) must
        # never appear in what the agent saw.
        self.assertNotIn("baseline_estate_id", prompt_text)
        self.assertNotIn("mutation_id", prompt_text)
        self.assertNotIn("affected_entity_ids", prompt_text)


class TestRunExperiment(unittest.TestCase):
    def test_aggregates_multiple_specs_into_one_experiment_analysis(self):
        specs = [
            TrajectorySpec(label="a", profile_name="level_1_minor", seed=1, baseline_estate_path=MILESTONE1_GROUND_TRUTH),
            TrajectorySpec(label="b", profile_name="level_2_structural", seed=2, baseline_estate_path=MILESTONE1_GROUND_TRUTH),
        ]
        result = run_experiment(specs, ScriptedAgentClient.fixed({"findings": []}))

        self.assertEqual(len(result.reports), 2)
        self.assertEqual(result.analysis.report_count, 2)
        self.assertIsNone(result.output_dir)

    def test_persists_reports_and_analysis_when_output_dir_given(self):
        specs = [
            TrajectorySpec(label="only", profile_name="level_1_minor", seed=1, baseline_estate_path=MILESTONE1_GROUND_TRUTH),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_experiment(specs, ScriptedAgentClient.fixed({"findings": []}), output_dir=tmp_dir)

            report_path = Path(tmp_dir) / "reports" / "only.json"
            analysis_path = Path(tmp_dir) / "analysis.json"
            self.assertTrue(report_path.exists())
            self.assertTrue(analysis_path.exists())

            reloaded_report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded_report["metadata"]["trajectory_id"], "only")
            reloaded_analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded_analysis["report_count"], 1)
            self.assertEqual(result.output_dir, Path(tmp_dir))

    def test_unknown_profile_name_raises_rather_than_silently_skipping(self):
        specs = [
            TrajectorySpec(label="bad", profile_name="not_a_real_profile", seed=1, baseline_estate_path=MILESTONE1_GROUND_TRUTH),
        ]
        with self.assertRaises(KeyError):
            run_experiment(specs, ScriptedAgentClient.fixed({"findings": []}))


class TestFullArc(unittest.TestCase):
    def test_generator_to_experiment_analysis_end_to_end(self):
        """Generator -> Mutation Engine -> Rendered Environment -> Agent
        Harness -> EvaluationReport -> ExperimentAnalysis, all the way
        through, using the generator (not just the hand-authored estate)."""
        specs = [
            TrajectorySpec(label=f"gen-seed{seed}", profile_name="level_2_structural", seed=seed,
                            generator_config=GeneratorConfig(seed=seed))
            for seed in (1, 2, 3)
        ]
        result = run_experiment(specs, ScriptedAgentClient.fixed({"findings": []}))
        self.assertEqual(result.analysis.report_count, 3)
        self.assertTrue(all(r.metadata.generator_version for r in result.reports))


class TestNoIntegrationsImportUnderArk(unittest.TestCase):
    """The optional, vendor-specific integrations/ package must never be
    imported by any of Ark's core pipeline modules (ark/core, ark/generator,
    ark/mutation, ark/adapters, ark/evaluator, ark/harness, ark/experiment)
    -- the dependency runs one way only (integrations depends on
    ark.harness's contract; ark's core never depends on integrations), the
    same directional discipline ark/adapters already established for
    rendering targets.

    ark/ui/ (Milestone 8) is a deliberate, narrow, documented exception:
    its whole job is letting a human choose between an offline demo agent
    and a real Anthropic-backed one, so ark/ui/logic.py necessarily
    references integrations.anthropic_agent_client -- but only as a lazy,
    function-local import inside build_agent_client() (the same "import
    only when this exact option is chosen" pattern AnthropicAgentClient
    itself already uses for the anthropic package -- see
    TestAnthropicAgentClient below), never at module level, and never
    anywhere else under ark/. Both halves of that exception are checked
    explicitly below, not just asserted in prose.
    """

    _CORE_SUBPACKAGES = ("core", "generator", "mutation", "adapters", "evaluator", "harness", "experiment")

    def test_no_core_pipeline_module_imports_integrations_or_anthropic(self):
        ark_root = REPO_ROOT / "ark"
        offenders = []
        for subpackage in self._CORE_SUBPACKAGES:
            for path in (ark_root / subpackage).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = [node.module]
                    for name in names:
                        if name == "integrations" or name.startswith("integrations.") or name == "anthropic":
                            offenders.append((str(path), name))
        self.assertEqual(offenders, [])

    def test_ui_logic_only_references_it_via_a_lazy_function_local_import(self):
        ui_logic_path = REPO_ROOT / "ark" / "ui" / "logic.py"
        tree = ast.parse(ui_logic_path.read_text(encoding="utf-8"), filename=str(ui_logic_path))

        module_level_names: set[str] = set()
        for node in tree.body:  # only top-level statements -- never descends into functions
            if isinstance(node, ast.Import):
                module_level_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_level_names.add(node.module)

        self.assertFalse(
            any(
                name == "integrations" or name.startswith("integrations.") or name == "anthropic"
                for name in module_level_names
            ),
            "ark/ui/logic.py must only reference integrations/anthropic via a lazy, "
            "function-local import (inside build_agent_client()), never a module-level one.",
        )


class TestAnthropicAgentClient(unittest.TestCase):
    """No test here requires the real `anthropic` package or a network
    call -- a fake object matching the small slice of the SDK's interface
    this class actually uses stands in for it."""

    class _FakeBlock:
        def __init__(self, text: str, block_type: str = "text"):
            self.type = block_type
            self.text = text

    class _FakeResponse:
        def __init__(self, blocks):
            self.content = blocks

    class _FakeMessages:
        def __init__(self, response_text: str):
            self.last_kwargs = None
            self._response_text = response_text

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return TestAnthropicAgentClient._FakeResponse([TestAnthropicAgentClient._FakeBlock(self._response_text)])

    class _FakeClient:
        def __init__(self, response_text: str = '{"findings": []}'):
            self.messages = TestAnthropicAgentClient._FakeMessages(response_text)

    def test_generate_wraps_a_fake_client_without_needing_the_real_sdk(self):
        from integrations.anthropic_agent_client import AnthropicAgentClient

        fake = self._FakeClient('{"findings": []}')
        agent_client = AnthropicAgentClient(client=fake)
        result = agent_client.generate("hello")
        self.assertEqual(result, '{"findings": []}')

    def test_model_and_max_tokens_are_passed_through(self):
        from integrations.anthropic_agent_client import AnthropicAgentClient

        fake = self._FakeClient()
        agent_client = AnthropicAgentClient(client=fake, model="claude-test-model", max_tokens=123)
        agent_client.generate("hello")
        self.assertEqual(fake.messages.last_kwargs["model"], "claude-test-model")
        self.assertEqual(fake.messages.last_kwargs["max_tokens"], 123)

    def test_non_text_blocks_are_skipped(self):
        from integrations.anthropic_agent_client import AnthropicAgentClient

        fake = self._FakeClient()
        fake.messages.create = lambda **kwargs: self._FakeResponse(
            [self._FakeBlock("ignored", block_type="tool_use"), self._FakeBlock("real text")]
        )
        agent_client = AnthropicAgentClient(client=fake)
        self.assertEqual(agent_client.generate("hello"), "real text")

    def test_missing_anthropic_package_raises_a_clear_import_error(self):
        from integrations.anthropic_agent_client import AnthropicAgentClient

        with mock.patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(ImportError):
                AnthropicAgentClient()

    def test_implements_the_agent_client_interface(self):
        from ark.harness.contract import AgentClient
        from integrations.anthropic_agent_client import AnthropicAgentClient

        self.assertTrue(issubclass(AnthropicAgentClient, AgentClient))


if __name__ == "__main__":
    unittest.main()
