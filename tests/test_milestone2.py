"""
Milestone 2 tests: the general MuleSoft adapter (ark/adapters/mulesoft/)
renders any estate conforming to the current schema, with a correct
rendering manifest, deterministically, without the adapter having required
any change to the core domain model.

Written as unittest.TestCase for the same zero-dependency reason as the
Milestone 0/1 tests.
"""

from __future__ import annotations

import dataclasses
import json
import re
import tempfile
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.adapters.mulesoft.renderer import MuleSoftRenderError
from ark.core import models as core_models
from ark.core.validate import validate_ground_truth

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden"


def _load_golden_artifacts(golden_dir: Path) -> dict[str, str]:
    """Load every file under golden_dir except manifest.json into a
    {relative_path: contents} dict, using forward-slash paths to match the
    adapter's own path convention regardless of OS."""
    artifacts = {}
    for path in golden_dir.rglob("*"):
        if path.is_file() and path.name != "manifest.json":
            rel = path.relative_to(golden_dir).as_posix()
            artifacts[rel] = path.read_text(encoding="utf-8")
    return artifacts


class TestMuleSoftAdapterRendering(unittest.TestCase):
    """Golden-file tests: rendering each example estate must reproduce the
    committed reference artifacts and manifest exactly."""

    def _assert_matches_golden(self, estate_name: str):
        estate = validate_ground_truth(REPO_ROOT / "examples" / estate_name / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)

        golden_dir = GOLDEN_ROOT / estate_name
        expected_artifacts = _load_golden_artifacts(golden_dir)
        self.assertEqual(rendered.artifacts, expected_artifacts)

        expected_manifest = json.loads((golden_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(rendered.manifest, expected_manifest)

    def test_renders_milestone0_estate(self):
        self._assert_matches_golden("milestone0")

    def test_renders_milestone1_estate(self):
        self._assert_matches_golden("milestone1")

    def test_milestone0_general_adapter_matches_original_handwritten_golden_plus_the_config_ref_fix(self):
        """The Milestone 0 golden XML (`examples/milestone0/expected_render.xml`)
        was hand-authored independently *before* any renderer existed (see
        examples/milestone0/render.py), and deliberately stays frozen/
        unmodified -- `examples/milestone0/render.py` is a preserved
        historical one-off, out of scope for the renderer fix below, and
        `tests/test_milestone0.py` still pins it to that exact file.

        This test used to assert plain byte-for-byte equality between the
        general adapter's output and that frozen file -- proof that
        generalizing the renderer hadn't quietly changed its output for the
        case it already had to get right. A later session found and fixed a
        real bug in the general adapter (ark/adapters/mulesoft/renderer.py):
        it emitted `config-ref` attributes with no matching
        `http:listener-config`/`http:request-config` global element for
        them to resolve to -- confirmed by running
        ark/validation/mulesoft_http_connector.py (built and tested
        separately) against real output. Fixing that bug necessarily,
        correctly, breaks plain byte-for-byte equality with the frozen
        (still-buggy-in-that-one-respect) hand-authored file -- the general
        adapter is now MORE correct than the historical reference it used
        to match exactly.

        So this test now asserts something more precise than "identical":
        the general adapter's real output equals the original hand-authored
        file with EXACTLY the expected listener-config block inserted, and
        nothing else different -- i.e., the fix changed exactly what it was
        supposed to and nothing else drifted unintentionally, keeping this
        test's original regression-guard purpose intact.
        """
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone0" / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)
        original_golden = (REPO_ROOT / "examples" / "milestone0" / "expected_render.xml").read_text(
            encoding="utf-8"
        )
        xml_path = "order-status-service/src/main/mule/order-status-service.xml"
        actual = rendered.artifacts[xml_path]

        expected_fix_insertion = (
            '\n    <http:listener-config name="HTTP_Listener_config">\n'
            '        <http:listener-connection host="0.0.0.0" port="8081"/>\n'
            "    </http:listener-config>\n"
        )
        self.assertIn(expected_fix_insertion, actual)
        reconstructed_pre_fix_output = actual.replace(expected_fix_insertion, "", 1)
        self.assertEqual(reconstructed_pre_fix_output, original_golden)


class TestHttpConnectorConfigRefsResolve(unittest.TestCase):
    """Renderer fix: every http:listener/http:request config-ref must
    resolve to a real http:listener-config/http:request-config global
    element declared in the same rendered file -- previously dangling
    (found by running ark/validation/mulesoft_http_connector.py, built in
    a prior session, against real output). These tests check the
    resolution property directly (regex-based, not a full XML parse,
    keeping this test file's own dependencies unchanged), independent of
    the golden-file byte-for-byte comparisons above, which also cover it
    implicitly."""

    @staticmethod
    def _names(pattern: str, xml: str) -> set[str]:
        return set(re.findall(pattern, xml))

    def _assert_all_config_refs_resolve(self, xml: str):
        listener_config_names = self._names(r'<http:listener-config name="([^"]+)"', xml)
        request_config_names = self._names(r'<http:request-config name="([^"]+)"', xml)

        for config_ref in self._names(r"<http:listener\b[^>]*\bconfig-ref=\"([^\"]+)\"", xml):
            self.assertIn(
                config_ref, listener_config_names,
                f"http:listener config-ref={config_ref!r} has no matching http:listener-config",
            )
        for config_ref in self._names(r"<http:request\b[^>]*\bconfig-ref=\"([^\"]+)\"", xml):
            self.assertIn(
                config_ref, request_config_names,
                f"http:request config-ref={config_ref!r} has no matching http:request-config",
            )

    def test_every_config_ref_resolves_in_every_milestone1_artifact(self):
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)
        for path, xml in rendered.artifacts.items():
            if path.endswith(".xml"):
                with self.subTest(path=path):
                    self._assert_all_config_refs_resolve(xml)

    def test_every_config_ref_resolves_in_milestone0_artifact(self):
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone0" / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)
        xml = rendered.artifacts["order-status-service/src/main/mule/order-status-service.xml"]
        self._assert_all_config_refs_resolve(xml)

    def test_validator_reports_zero_config_ref_issues_against_real_output(self):
        """The concrete before/after this fix was built to produce: the
        existing, UNMODIFIED validator from the prior session, run against
        real rendered output, now reports zero config-ref issues (it may
        still report other, unrelated issues -- see this session's
        summary -- but never a config-ref issue, which is this task's
        specific scope)."""
        from ark.validation.mulesoft_http_connector import validate_http_connector_xml

        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)
        for path, xml in rendered.artifacts.items():
            if path.endswith(".xml"):
                result = validate_http_connector_xml(xml)
                config_ref_issues = [i for i in result.issues if i.attribute == "config-ref"]
                with self.subTest(path=path):
                    self.assertEqual(config_ref_issues, [])

    def test_shared_config_ref_name_produces_exactly_one_global_element_not_a_duplicate(self):
        """order-status-experience's flow uses "HTTP_Listener_config" once
        and app-order-processing-process's three http:request steps all
        share the single "HTTP_Request_config" name -- in both cases,
        exactly one matching global element must be rendered, not one per
        usage."""
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)
        xml = rendered.artifacts["order-processing-process/src/main/mule/order-processing-process.xml"]

        # Three http:request steps in this file (Inventory call x2, Customer call) --
        # all sharing the one HTTP_Request_config name (see renderer.py's
        # _HTTP_REQUEST_CONFIG_REF) -- must still produce exactly one
        # http:request-config element, not three.
        self.assertEqual(xml.count("<http:request-config"), 1)
        self.assertGreaterEqual(xml.count('config-ref="HTTP_Request_config"'), 2)
        self.assertEqual(xml.count("<http:listener-config"), 1)

    def test_app_with_no_api_call_steps_gets_no_request_config(self):
        """inventory-system/customer-system have an http:listener but no
        ApiCallStep anywhere -- no http:request-config should be rendered
        at all for them (not even an empty/unused one)."""
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)
        for path in (
            "inventory-system/src/main/mule/inventory-system.xml",
            "customer-system/src/main/mule/customer-system.xml",
        ):
            with self.subTest(path=path):
                xml = rendered.artifacts[path]
                self.assertNotIn("<http:request-config", xml)
                self.assertIn("<http:listener-config", xml)


class TestMuleSoftAdapterDeterminism(unittest.TestCase):
    def test_rendering_twice_produces_identical_output(self):
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")
        first = MuleSoftAdapter().render(estate)
        second = MuleSoftAdapter().render(estate)
        self.assertEqual(first.artifacts, second.artifacts)
        self.assertEqual(first.manifest, second.manifest)


class TestMuleSoftAdapterManifestCorrectness(unittest.TestCase):
    """Spot-check the manifest against facts we know about the Milestone 1
    estate, independent of the golden-file comparison above."""

    @classmethod
    def setUpClass(cls):
        estate = validate_ground_truth(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")
        cls.manifest = MuleSoftAdapter().render(estate).manifest

    def test_every_flow_and_api_appears_in_entity_index(self):
        for entity_id in [
            "flow-order-status-main",
            "flow-process-order-main",
            "flow-nightly-reconciliation",
            "flow-validate-order",
            "api-inventory-system-v1",
            "api-customer-system-v1",
        ]:
            self.assertIn(entity_id, self.manifest["entity_index"])

    def test_entity_index_points_at_the_right_artifact(self):
        entry = self.manifest["entity_index"]["flow-nightly-reconciliation"]
        self.assertEqual(
            entry["artifact_path"],
            "order-processing-process/src/main/mule/order-processing-process.xml",
        )
        self.assertEqual(entry["entity_type"], "Flow")

    def test_api_call_dependency_is_recorded(self):
        api_call_deps = [d for d in self.manifest["dependencies"] if d["kind"] == "api-call"]
        targets = {d["target_entity_id"] for d in api_call_deps}
        self.assertEqual(
            targets,
            {"api-order-processing-process-v1", "api-inventory-system-v1", "api-customer-system-v1"},
        )

    def test_flow_ref_dependency_for_shared_subflow_is_recorded_twice(self):
        """flow-validate-order is flow-ref'd from two different flows
        (Milestone 1's in-app shared component) — the manifest must show
        both edges, not just one."""
        edges_to_validate_order = [
            d
            for d in self.manifest["dependencies"]
            if d["kind"] == "flow-ref" and d["target_entity_id"] == "flow-validate-order"
        ]
        sources = {d["source_entity_id"] for d in edges_to_validate_order}
        self.assertEqual(sources, {"flow-process-order-main", "flow-nightly-reconciliation"})


class TestMuleSoftAdapterFailsLoudly(unittest.TestCase):
    """The adapter must refuse to render rather than guess when an
    ApiCallStep targets an API whose entry flow has no HTTP contract to
    derive a request path/method from (a known, documented Milestone 2
    limitation — see renderer.py)."""

    def test_api_call_to_non_http_entry_flow_raises(self):
        raw = json.loads(
            (REPO_ROOT / "examples" / "milestone1" / "ground_truth.json").read_text(encoding="utf-8")
        )
        # Point the Process API's entry flow at the scheduler-triggered
        # reconciliation flow instead of the HTTP-triggered main flow.
        # Every ApiCallStep targeting this API (there are two, from the
        # Experience app) now has no HTTP path/method to resolve.
        process_app = raw["applications"][1]
        assert process_app["id"] == "app-order-processing-process"
        process_app["apis"][0]["entry_flow_id"] = "flow-nightly-reconciliation"

        tmp_path = Path(tempfile.mkdtemp()) / "broken_ground_truth.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")

        estate = validate_ground_truth(tmp_path)
        with self.assertRaises(MuleSoftRenderError):
            MuleSoftAdapter().render(estate)


class TestAdapterDidNotChangeCoreModel(unittest.TestCase):
    """Pin test: building the *adapter* (Milestone 2's own scope) must not
    have required adding fields to, renaming, or otherwise reshaping the
    core domain model. If this test ever fails, that's a signal to stop
    and consciously decide whether a real schema change is justified — not
    to update the pin silently.

    Updated once, consciously, for Feature 2 (domain-conditioned component
    injection, "organized randomness" — a later, separate session, not the
    adapter work this test's name refers to): that feature deliberately
    DID add to the core model (ConnectorStep, GroundTruthEstate.domain),
    per its own task spec, and bumped SCHEMA_VERSION 0.2.0 -> 0.3.0
    following the exact same additive-only discipline Milestone 1's
    0.1.0 -> 0.2.0 bump already established (see SCHEMA_VERSION's own
    comment in ark/core/models.py). This test's job is unchanged: pin the
    shape so any *future* unintentional/undocumented change is caught the
    same way this one was consciously reviewed and accepted."""

    def test_schema_version_is_0_3_0_after_the_feature_2_domain_concept_addition(self):
        self.assertEqual(core_models.SCHEMA_VERSION, "0.3.0")

    def test_core_dataclass_shapes_match_the_post_feature_2_schema(self):
        expected_fields = {
            "HttpListenerTrigger": {"path", "method", "listener_config_ref", "type"},
            "SchedulerTrigger": {"cron_expression", "description", "type"},
            "TransformStep": {"id", "name", "description", "dataweave", "kind"},
            "FlowRefStep": {"id", "target_flow_id", "kind"},
            "LoggerStep": {"id", "message", "level", "kind"},
            "ApiCallStep": {"id", "name", "description", "target_api_id", "kind"},
            "ConnectorStep": {"id", "name", "description", "connector_type", "kind"},
            "Flow": {"id", "name", "flow_type", "trigger", "steps"},
            "API": {"id", "name", "version", "entry_flow_id"},
            "Application": {"id", "name", "apis", "flows"},
            "GroundTruthEstate": {"estate_id", "schema_version", "applications", "domain"},
        }
        for class_name, expected in expected_fields.items():
            cls = getattr(core_models, class_name)
            actual = {f.name for f in dataclasses.fields(cls)}
            self.assertEqual(actual, expected, f"{class_name} fields changed unexpectedly")


if __name__ == "__main__":
    unittest.main()
