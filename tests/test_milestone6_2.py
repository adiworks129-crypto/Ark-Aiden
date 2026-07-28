"""
Milestone 6.2 tests: the step/component-level manifest expansion
(ark/adapters/mulesoft/manifest.py + renderer.py's _step_entity) and the
translation layer that resolves an agent's artifact-visible claims back
into Ark's internal representation (ark/evaluator/parser.py, matcher.py).

Covers, per the milestone's requirements: manifest traceability, entity
resolution (including the required matcher scenarios: correct match,
wrong-entity/correct-category, correct-entity/wrong-category, vague
artifact references, and duplicate entity names), and isolation
regression guards (the agent-facing modules never read ground truth or
the mutation ledger directly, and nothing here mutates either).

Written as unittest.TestCase for the same zero-dependency reason as every
earlier milestone's tests.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
from ark.evaluator import matcher as matcher_module
from ark.evaluator import parser as parser_module
from ark.evaluator import schema as schema_module
from ark.evaluator.issues import Issue, derive_issues
from ark.evaluator.matcher import match_findings
from ark.evaluator.parser import EntityResolution, ResolvedFinding, resolve_entity_reference
from ark.evaluator.schema import Finding, parse_agent_output
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"


def _milestone1_manifest() -> dict:
    estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
    return MuleSoftAdapter().render(estate).manifest


class TestManifestExpansion(unittest.TestCase):
    """The manifest, not the rendered artifact content, is where
    traceability lives -- these tests confirm the Milestone 6.2 expansion
    added step-level labels without duplicating or altering ground truth."""

    @classmethod
    def setUpClass(cls):
        cls.estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        cls.rendered = MuleSoftAdapter().render(cls.estate)
        cls.manifest = cls.rendered.manifest

    def test_every_exported_artifact_entity_has_traceability_information(self):
        """Every entity listed under every artifact must carry an id, a
        type, and -- after the Milestone 6.2 expansion -- a rendered name,
        so an agent's textual reference to it can be resolved."""
        self.assertGreater(len(self.manifest["artifacts"]), 0)
        for artifact in self.manifest["artifacts"]:
            self.assertGreater(len(artifact["entities"]), 0)
            for entity in artifact["entities"]:
                self.assertIn("id", entity)
                self.assertIn("type", entity)
                self.assertIn("name", entity, f"{entity['id']} has no rendered name for traceability")
                self.assertTrue(entity["name"], f"{entity['id']} has an empty rendered name")

    def test_entity_index_carries_name_for_every_entity(self):
        for entity_id, entry in self.manifest["entity_index"].items():
            self.assertIn("name", entry)
            self.assertTrue(entry["name"], f"entity_index entry for {entity_id} has no name")

    def test_flow_ref_step_gets_a_synthesized_label_derived_from_its_target(self):
        step = self.manifest["entity_index"]["step-order-status-audit-ref"]
        self.assertEqual(step["entity_type"], "Step:flow-ref")
        self.assertEqual(step["name"], "reference to 'audit-log-sub-flow'")
        # Deliberately NOT aliased to the bare target name "audit-log-sub-flow"
        # -- see renderer.py's _step_entity docstring: a FlowRefStep and its
        # target Flow always render into the same artifact file (flow-ref is
        # intra-Application only), so that alias would make the step
        # permanently ambiguous with the Flow entity itself in every case,
        # not just occasionally.
        self.assertEqual(step.get("aliases", []), [])

    def test_logger_step_uses_its_message_as_the_label(self):
        step = self.manifest["entity_index"]["step-reconciliation-log"]
        self.assertEqual(step["entity_type"], "Step:logger")
        self.assertEqual(step["name"], "Nightly order reconciliation completed")

    def test_transform_and_api_call_steps_reuse_their_existing_name_field(self):
        transform = self.manifest["entity_index"]["step-inventory-build-response"]
        self.assertEqual(transform["name"], "Build Inventory Response")
        api_call = self.manifest["entity_index"]["step-order-status-call-process"]
        self.assertEqual(api_call["name"], "Call Order Processing Process API")

    def test_manifest_expansion_did_not_alter_rendered_artifact_content(self):
        """The expansion is additive to the manifest only -- confirmed by
        diffing against the golden XML/YAML fixtures (byte-for-byte, same
        as Milestone 2's own golden tests)."""
        golden_dir = REPO_ROOT / "tests" / "golden" / "milestone1"
        for path, contents in self.rendered.artifacts.items():
            golden_file = golden_dir / path
            self.assertEqual(contents, golden_file.read_text(encoding="utf-8"), f"{path} content changed")

    def test_rendering_does_not_mutate_the_ground_truth_estate(self):
        estate_copy = copy.deepcopy(self.estate)
        MuleSoftAdapter().render(self.estate)
        self.assertEqual(self.estate, estate_copy, "rendering the manifest mutated the ground-truth estate")


class TestEntityResolutionAgainstRealManifest(unittest.TestCase):
    """Every kind of manifest entity must be resolvable given the labels
    Milestone 6.2 actually generates for it, using the real Milestone 1
    rendered manifest -- not a hand-built fixture."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = _milestone1_manifest()

    def test_resolve_flow_by_exact_name_and_artifact(self):
        artifact_path, matches_entity, resolution = resolve_entity_reference(
            self.manifest,
            "customer-system/src/main/mule/customer-system.xml",
            "get-customer-main-flow",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "flow-customer-get-main")
        self.assertTrue(matches_entity)

    def test_resolve_transform_step_by_name_within_artifact(self):
        _, _, resolution = resolve_entity_reference(
            self.manifest,
            "inventory-system/src/main/mule/inventory-system.xml",
            "Build Inventory Response",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "step-inventory-build-response")

    def test_resolve_flow_ref_step_via_its_full_synthesized_label(self):
        _, _, resolution = resolve_entity_reference(
            self.manifest,
            "order-status-experience/src/main/mule/order-status-experience.xml",
            "reference to 'audit-log-sub-flow'",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "step-order-status-audit-ref")

    def test_bare_target_flow_name_resolves_to_the_flow_not_the_referencing_step(self):
        """The bare name 'audit-log-sub-flow' names the Flow entity
        itself, even though a FlowRefStep in the same file also refers to
        it -- the step is only findable via its full synthesized label
        (see test above), not the bare target name. This is the intended
        consequence of the alias decision documented in renderer.py."""
        _, _, resolution = resolve_entity_reference(
            self.manifest,
            "order-status-experience/src/main/mule/order-status-experience.xml",
            "audit-log-sub-flow",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "flow-order-status-audit-log")

    def test_resolve_logger_step_by_its_message(self):
        _, _, resolution = resolve_entity_reference(
            self.manifest,
            "order-processing-process/src/main/mule/order-processing-process.xml",
            "Nightly order reconciliation completed",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "step-reconciliation-log")

    def test_case_and_separator_normalization_still_resolves(self):
        _, _, resolution = resolve_entity_reference(
            self.manifest,
            "customer-system/src/main/resources/api-customer-system-v1.yaml",
            "Customer_System___API",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "api-customer-system-v1")

    def test_partial_substring_reference_does_not_match(self):
        """Deliberately conservative: 'Customer' alone must NOT resolve to
        'Customer System API' -- the tradeoff this module documents (no
        fuzzy/substring matching)."""
        _, _, resolution = resolve_entity_reference(self.manifest, "some-file.xml", "Customer")
        self.assertEqual(resolution.status, "unresolved")

    def test_duplicate_flow_name_without_artifact_scoping_is_ambiguous(self):
        """Milestone 1's own estate already has two sub-flows independently
        named 'log-request-sub-flow' (inventory-system and customer-system).
        Referencing the name alone, without a resolvable artifact to scope
        it, must be reported as ambiguous -- never silently resolved to
        one of the two."""
        _, _, resolution = resolve_entity_reference(
            self.manifest, "not-a-real-file.xml", "log-request-sub-flow"
        )
        self.assertEqual(resolution.status, "ambiguous")
        self.assertEqual(
            set(resolution.candidate_entity_ids),
            {"flow-inventory-log-request", "flow-customer-log-request"},
        )

    def test_duplicate_flow_name_resolves_uniquely_when_artifact_reference_disambiguates(self):
        """The same ambiguous name resolves cleanly once the agent also
        names the (real) file it appeared in."""
        _, matches_entity, resolution = resolve_entity_reference(
            self.manifest,
            "customer-system/src/main/mule/customer-system.xml",
            "log-request-sub-flow",
        )
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "flow-customer-log-request")
        self.assertTrue(matches_entity)

    def test_vague_artifact_reference_falls_back_to_whole_manifest_search_safely(self):
        """An artifact reference that resolves to nothing must not crash
        the resolver -- it falls back to a whole-manifest name search, and
        (since this particular name is unique across the whole estate)
        still finds the right entity, just flagged as not artifact-matched."""
        artifact_path, matches_entity, resolution = resolve_entity_reference(
            self.manifest, "the API file", "Build Inventory Response"
        )
        self.assertIsNone(artifact_path)
        self.assertFalse(matches_entity)
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.entity_id, "step-inventory-build-response")

    def test_completely_unrecognized_reference_is_unresolved_not_an_error(self):
        _, _, resolution = resolve_entity_reference(
            self.manifest, "nonexistent.xml", "Totally Made Up Entity Name"
        )
        self.assertEqual(resolution.status, "unresolved")
        self.assertIsNone(resolution.entity_id)


def _issue(issue_id: str, issue_type: str, affected_entity_ids: list[str]) -> Issue:
    return Issue(
        issue_id=issue_id,
        issue_type=issue_type,
        affected_entity_ids=affected_entity_ids,
        observable_symptom={eid: {"field": "changed"} for eid in affected_entity_ids},
        severity=0.5,
        expected_detection_target="test fixture issue",
        transformation_history=[],
    )


def _resolved_finding(
    issue_type: str,
    *,
    entity_id: str | None,
    status: str = "resolved",
    artifact_resolved: bool = True,
    artifact_matches_entity: bool = True,
    confidence: float = 0.8,
) -> ResolvedFinding:
    finding = Finding(
        artifact_reference="some-file.xml",
        entity_reference="Some Entity",
        issue_type=issue_type,
        explanation="a test explanation",
        confidence=confidence,
        raw_issue_type=issue_type,
    )
    resolution = EntityResolution(
        status=status, entity_id=entity_id, entity_type="Flow" if entity_id else None
    )
    return ResolvedFinding(
        finding=finding,
        finding_id="finding-000",
        artifact_resolved=artifact_resolved,
        resolved_artifact_path="some-file.xml" if artifact_resolved else None,
        artifact_matches_entity=artifact_matches_entity,
        entity_resolution=resolution,
    )


class TestFindingMatcher(unittest.TestCase):
    def test_correct_finding_matches_correctly(self):
        issues = [_issue("naming_drift:api-1", "naming_drift", ["api-1"])]
        resolved = [_resolved_finding("naming_drift", entity_id="api-1")]
        [result] = match_findings(resolved, issues)

        self.assertTrue(result.category_correct)
        self.assertTrue(result.entity_correct)
        self.assertTrue(result.artifact_reference_correct)
        self.assertEqual(result.matched_issue_id, "naming_drift:api-1")

    def test_wrong_entity_but_correct_issue_type_is_partially_correct(self):
        """The agent names the right issue_type (it's a real category
        present in this estate) but points at an entity that has no real
        issue on it at all -- category_correct=True, entity_correct=False."""
        issues = [_issue("naming_drift:api-1", "naming_drift", ["api-1"])]
        resolved = [_resolved_finding("naming_drift", entity_id="api-2")]  # api-2 has no issue
        [result] = match_findings(resolved, issues)

        self.assertTrue(result.category_correct)
        self.assertFalse(result.entity_correct)
        self.assertIsNone(result.matched_issue_id)

    def test_correct_entity_but_wrong_issue_type_is_incorrect(self):
        """The agent points at the exact entity with a real issue, but
        names the wrong category -- entity_correct=True (they found the
        right thing), category_correct=False (they misdiagnosed it)."""
        issues = [_issue("naming_drift:api-1", "naming_drift", ["api-1"])]
        resolved = [_resolved_finding("documentation_decay", entity_id="api-1")]
        [result] = match_findings(resolved, issues)

        self.assertTrue(result.entity_correct)
        self.assertFalse(result.category_correct)
        self.assertEqual(result.matched_issue_id, "naming_drift:api-1")

    def test_vague_artifact_reference_is_handled_safely_not_as_a_crash(self):
        issues = [_issue("naming_drift:api-1", "naming_drift", ["api-1"])]
        resolved = [
            _resolved_finding(
                "naming_drift", entity_id="api-1", artifact_resolved=False, artifact_matches_entity=False
            )
        ]
        [result] = match_findings(resolved, issues)

        self.assertFalse(result.artifact_reference_correct)
        # The entity itself can still resolve via the whole-manifest
        # fallback even when the artifact claim is vague/wrong.
        self.assertTrue(result.entity_correct)

    def test_duplicate_entity_names_do_not_produce_a_silent_ambiguous_match(self):
        """When parser.py reports 'ambiguous' (two entities share a name),
        the matcher must not guess -- entity_correct is False and no
        issue is matched, rather than picking one of the candidates."""
        issues = [_issue("naming_drift:flow-a", "naming_drift", ["flow-a"])]
        resolved = [_resolved_finding("naming_drift", entity_id=None, status="ambiguous")]
        [result] = match_findings(resolved, issues)

        self.assertFalse(result.entity_correct)
        self.assertIsNone(result.matched_issue_id)

    def test_unresolved_reference_produces_no_match(self):
        issues = [_issue("naming_drift:flow-a", "naming_drift", ["flow-a"])]
        resolved = [_resolved_finding("naming_drift", entity_id=None, status="unresolved")]
        [result] = match_findings(resolved, issues)

        self.assertFalse(result.entity_correct)
        self.assertIsNone(result.matched_issue_id)

    def test_hallucination_against_a_clean_level_0_estate(self):
        """No real issues at all -- any finding is necessarily both
        category-wrong (no category exists to be right about) and
        entity-wrong."""
        resolved = [_resolved_finding("naming_drift", entity_id="api-1")]
        [result] = match_findings(resolved, issues=[])

        self.assertFalse(result.category_correct)
        self.assertFalse(result.entity_correct)
        self.assertIsNone(result.matched_issue_id)

    def test_explanation_and_confidence_pass_through_unscored(self):
        resolved = [_resolved_finding("naming_drift", entity_id="api-1", confidence=0.42)]
        resolved[0].finding.explanation = "a specific rationale"
        [result] = match_findings(resolved, issues=[_issue("i", "naming_drift", ["api-1"])])

        self.assertEqual(result.explanation_score_input, "a specific rationale")
        self.assertEqual(result.confidence, 0.42)


def _imported_module_names(module) -> set[str]:
    """Statically collect every module path actually imported by `module`
    (via `import x.y` or `from x.y import z`), using the AST rather than a
    raw text/docstring search -- so a module explaining in prose what it
    does NOT import (as parser.py and schema.py both do) doesn't produce a
    false positive."""
    import ast

    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestIsolation(unittest.TestCase):
    """The agent-facing modules (schema.py, parser.py, matcher.py) must
    never import ground truth or the mutation ledger directly, and nothing
    in the evaluation flow may mutate either."""

    _FORBIDDEN_MODULE_PREFIXES = ("ark.mutation.ledger", "ark.mutation.engine", "ark.core.models")

    def _assert_no_forbidden_imports(self, module):
        imported = _imported_module_names(module)
        for name in imported:
            for forbidden in self._FORBIDDEN_MODULE_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"{module.__name__} imports {name}, crossing the agent-isolation boundary",
                )

    def test_parser_module_never_imports_ground_truth_or_ledger_internals(self):
        self._assert_no_forbidden_imports(parser_module)

    def test_matcher_module_never_imports_ground_truth_or_ledger_internals(self):
        self._assert_no_forbidden_imports(matcher_module)

    def test_schema_module_never_imports_ground_truth_or_ledger_internals(self):
        self._assert_no_forbidden_imports(schema_module)

    def test_finding_dataclass_carries_no_hidden_ark_internal_fields(self):
        finding_fields = {f.name for f in dataclasses.fields(Finding)}
        hidden_fields = {
            "mutation_id", "rationale", "sequence_index", "original_state",
            "transformed_state", "seed", "entity_id",
        }
        self.assertEqual(finding_fields & hidden_fields, set())

    def test_ground_truth_and_ledger_are_unchanged_after_a_full_evaluation_pass(self):
        """Runs the whole flow -- baseline -> mutate -> render -> parse
        agent output -> resolve -> match -- and confirms neither the
        baseline, the transformed estate, nor the ledger were touched."""
        baseline = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=1)

        baseline_copy = copy.deepcopy(baseline)
        estate_copy = copy.deepcopy(result.transformed_estate)
        ledger_copy = copy.deepcopy(result.ledger)

        rendered = MuleSoftAdapter().render(result.transformed_estate)
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
        resolved = parser_module.parse_and_resolve_findings(agent_output.findings, rendered.manifest)
        match_findings(resolved, issues)

        self.assertEqual(baseline, baseline_copy)
        self.assertEqual(result.transformed_estate, estate_copy)
        self.assertEqual(result.ledger, ledger_copy)


if __name__ == "__main__":
    unittest.main()
