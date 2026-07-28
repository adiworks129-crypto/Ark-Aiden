"""
Feature 2 follow-up: wiring `ConnectorStep` rendering + a domain-tagged test
estate, so the full injection -> render -> validate -> manifest chain can be
exercised at the unit/component level.

Explicitly out of scope here (see this session's own task spec): running
a real trajectory through `ark.experiment`'s runner, or calling an agent.
`TestThisModuleNeverTouchesTheExperimentPipelineOrAnAgent` below checks
that structurally, the same self-auditing pattern
`tests/test_milestone7.py`'s `TestNoIntegrationsImportUnderArk` already
uses for an analogous boundary — not just a comment promising it.

Every other test here calls `ark.mutation.engine.run_trajectory()` and/or
`ark.generator.generator.generate_estate()` directly — the mutation engine
and generator, with zero agent involvement — exactly the same pattern
`tests/test_milestone4.py` and `tests/test_domain_component_injection.py`
already use for unit-testing operators. That is not the "real trajectory"
this task defers to a separate, later, compute-costing session.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.models import ConnectorStep
from ark.core.validate import validate_ground_truth
from ark.generator.config import GeneratorConfig
from ark.generator.generator import generate_estate
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES
from ark.validation.mulesoft_http_connector import validate_http_connector_xml

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"


def _domain_tagged_milestone1(domain: str):
    estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
    return dataclasses.replace(estate, domain=domain)


def _inject_connector_step(domain: str, seed: int):
    """Run the opt-in domain_injection_preview profile against a
    domain-tagged copy of the Milestone 1 estate and return
    (transformed_estate, new_step_id). Milestone 1's own ground-truth file
    is never modified -- dataclasses.replace() only tags an in-memory
    copy, exactly like tests/test_domain_component_injection.py already
    does."""
    baseline = _domain_tagged_milestone1(domain)
    result = run_trajectory(baseline, PROFILES["domain_injection_preview"], seed=seed)
    assert len(result.ledger.records) == 1, "expected exactly one injected component"
    new_step_id = result.ledger.records[0].affected_entity_ids[0]
    return result.transformed_estate, new_step_id


def _find_connector_step(estate, step_id: str) -> ConnectorStep:
    for app in estate.applications:
        for flow in app.flows:
            for step in flow.steps:
                if step.id == step_id:
                    assert isinstance(step, ConnectorStep)
                    return step
    raise AssertionError(f"ConnectorStep {step_id!r} not found in transformed estate")


class TestConnectorStepRendersWellFormedXml(unittest.TestCase):
    def test_every_artifact_is_still_well_formed_xml_after_injection(self):
        transformed_estate, _ = _inject_connector_step("finance", seed=1)
        rendered = MuleSoftAdapter().render(transformed_estate)
        for path, content in rendered.artifacts.items():
            if path.endswith(".xml"):
                with self.subTest(path=path):
                    ET.fromstring(content)  # raises ParseError if not well-formed

    def test_connector_step_is_rendered_as_a_labeled_comment_plus_logger_not_a_fabricated_element(self):
        """Grounded-in-real-Mule-syntax check: the rendered output must
        use only real, generic, already-used-elsewhere Mule elements
        (<logger>, an XML comment, doc:name) -- never an invented,
        connector-specific namespace/tag (e.g. no "<sap:" or "<connector:"
        style element, which would be fabricated syntax with no basis in
        real MuleSoft documentation)."""
        transformed_estate, new_step_id = _inject_connector_step("finance", seed=1)
        step = _find_connector_step(transformed_estate, new_step_id)
        rendered = MuleSoftAdapter().render(transformed_estate)

        # Find the specific artifact containing this step's rendered output.
        matching_artifacts = [
            xml for xml in rendered.artifacts.values()
            if f"External connector reference: {step.connector_type}" in xml
        ]
        self.assertEqual(len(matching_artifacts), 1)
        xml = matching_artifacts[0]

        self.assertIn(f"<!-- External connector reference: {step.connector_type} -->", xml)
        self.assertIn(f'doc:name="{step.name}"', xml)
        self.assertIn(f'message="{step.description}"', xml)
        self.assertIn("<logger ", xml)
        # No fabricated connector-specific namespace anywhere in the file.
        self.assertNotIn(f"<{step.connector_type}:", xml)
        self.assertNotIn("<sap:", xml)
        self.assertNotIn("<connector:", xml)

    def test_connector_step_entity_appears_in_manifest_with_the_right_name_and_type(self):
        transformed_estate, new_step_id = _inject_connector_step("retail", seed=2)
        step = _find_connector_step(transformed_estate, new_step_id)
        rendered = MuleSoftAdapter().render(transformed_estate)

        self.assertIn(new_step_id, rendered.manifest["entity_index"])
        entry = rendered.manifest["entity_index"][new_step_id]
        self.assertEqual(entry["name"], step.name)
        self.assertEqual(entry["entity_type"], "Step:connector")

    def test_connector_step_produces_no_dependency_edge_in_the_manifest(self):
        """Unlike FlowRefStep/ApiCallStep, a ConnectorStep names an
        EXTERNAL system, not another Ark-modeled entity -- build_manifest()
        should not (and, unmodified, does not) fabricate a dependency edge
        for it."""
        transformed_estate, new_step_id = _inject_connector_step("finance", seed=3)
        rendered = MuleSoftAdapter().render(transformed_estate)
        matching_edges = [d for d in rendered.manifest["dependencies"] if d.get("source_step_id") == new_step_id]
        self.assertEqual(matching_edges, [])


class TestHttpConnectorValidatorIgnoresConnectorStep(unittest.TestCase):
    """ConnectorStep renders as <logger>/an XML comment -- neither is an
    HTTP-connector element, so ark.validation.mulesoft_http_connector
    (unmodified, per this session's scope) should simply have nothing to
    say about it, exactly like it already ignores <flow>/<ee:transform>
    today (see that module's own docstring: "non-HTTP elements ... are
    walked over but never flagged")."""

    def test_rendered_connector_step_produces_no_validator_issue_referencing_it(self):
        transformed_estate, new_step_id = _inject_connector_step("finance", seed=4)
        step = _find_connector_step(transformed_estate, new_step_id)
        rendered = MuleSoftAdapter().render(transformed_estate)

        for path, xml in rendered.artifacts.items():
            if not path.endswith(".xml"):
                continue
            if f"External connector reference: {step.connector_type}" not in xml:
                continue
            result = validate_http_connector_xml(xml)
            offending = [
                issue
                for issue in result.issues
                if step.connector_type in issue.message or issue.element == "logger"
            ]
            with self.subTest(path=path):
                self.assertEqual(offending, [])

    def test_injecting_a_connector_step_does_not_change_the_validators_issue_count_for_that_artifact(self):
        """Stronger form of the same guarantee: compare an artifact's
        validator issue count WITH vs. WITHOUT the injected ConnectorStep
        -- injection must add exactly zero new issues."""
        baseline = _domain_tagged_milestone1("finance")
        before_rendered = MuleSoftAdapter().render(baseline)

        transformed_estate, new_step_id = _inject_connector_step("finance", seed=4)
        step = _find_connector_step(transformed_estate, new_step_id)
        after_rendered = MuleSoftAdapter().render(transformed_estate)

        artifact_path = next(
            path for path, xml in after_rendered.artifacts.items()
            if f"External connector reference: {step.connector_type}" in xml
        )

        before_issues = validate_http_connector_xml(before_rendered.artifacts[artifact_path]).issues
        after_issues = validate_http_connector_xml(after_rendered.artifacts[artifact_path]).issues
        self.assertEqual(len(before_issues), len(after_issues))


class TestDomainTaggedGeneratedEstate(unittest.TestCase):
    """Per this session's task: confirm the GENERATOR path (option (b) --
    GeneratorConfig.domain) already produces a usable domain-tagged
    estate, rather than adding a new duplicative hand-authored fixture
    file. The Milestone 1 hand-authored ground-truth file itself is never
    touched anywhere in this module -- only in-memory copies via
    dataclasses.replace(), or entirely separate generated estates."""

    def test_generated_estate_has_the_requested_domain(self):
        generated = generate_estate(GeneratorConfig(seed=100, domain="finance"))
        self.assertEqual(generated.estate.domain, "finance")

    def test_generated_domain_tagged_estate_yields_a_connector_step_via_the_opt_in_operator(self):
        generated = generate_estate(GeneratorConfig(seed=101, domain="retail"))
        result = run_trajectory(generated.estate, PROFILES["domain_injection_preview"], seed=101)

        self.assertEqual(len(result.ledger.records), 1)
        new_step_id = result.ledger.records[0].affected_entity_ids[0]
        step = _find_connector_step(result.transformed_estate, new_step_id)
        self.assertIsInstance(step, ConnectorStep)

    def test_full_chain_on_a_generated_estate_injection_render_validate_manifest(self):
        """The end-to-end unit/component chain this session's Definition
        of Done asks for, run against a GENERATED (not hand-authored)
        domain-tagged estate: injection -> render -> validator ignores it
        -> manifest round-trip, no errors anywhere, no trajectory
        pipeline, no agent."""
        generated = generate_estate(GeneratorConfig(seed=102, domain="finance"))
        result = run_trajectory(generated.estate, PROFILES["domain_injection_preview"], seed=102)
        self.assertEqual(len(result.ledger.records), 1)

        rendered = MuleSoftAdapter().render(result.transformed_estate)  # also builds the manifest
        self.assertIn("entity_index", rendered.manifest)

        new_step_id = result.ledger.records[0].affected_entity_ids[0]
        self.assertIn(new_step_id, rendered.manifest["entity_index"])

        for path, content in rendered.artifacts.items():
            if not path.endswith(".xml"):
                continue
            with self.subTest(path=path):
                ET.fromstring(content)  # well-formed
                validate_http_connector_xml(content)  # must not raise


class TestThisModuleNeverTouchesTheExperimentPipelineOrAnAgent(unittest.TestCase):
    """Structural check (not just a docstring promise) that this test
    file stays at the unit/component level: no import of
    ark.experiment/ark.harness/integrations anywhere, matching this
    session's explicit "no real trajectory, no agent" boundary. Same
    self-auditing pattern as
    tests/test_milestone7.py's TestNoIntegrationsImportUnderArk."""

    _FORBIDDEN_PREFIXES = ("ark.experiment", "ark.harness", "integrations", "anthropic")

    def test_no_forbidden_import_anywhere_in_this_file(self):
        this_file = Path(__file__)
        tree = ast.parse(this_file.read_text(encoding="utf-8"), filename=str(this_file))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)

        for name in names:
            for forbidden in self._FORBIDDEN_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"this test module imports {name!r}, which starts with forbidden prefix {forbidden!r}",
                )


if __name__ == "__main__":
    unittest.main()
