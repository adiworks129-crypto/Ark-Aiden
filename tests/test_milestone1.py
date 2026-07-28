"""
Milestone 1 tests: exercise the schema against a larger, multi-application
estate (4 APIs, 9 flows) and lock in the two referential-integrity scopes
introduced alongside it — FlowRefStep resolves within one Application,
ApiCallStep resolves across the whole estate.

Written as unittest.TestCase for the same zero-dependency reason as
Milestone 0's tests (see tests/test_milestone0.py's docstring).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ark.core.models import ApiCallStep, FlowRefStep, SchedulerTrigger
from ark.core.validate import GroundTruthValidationError, validate_ground_truth

MILESTONE0_GROUND_TRUTH = (
    Path(__file__).resolve().parent.parent / "examples" / "milestone0" / "ground_truth.json"
)
MILESTONE1_GROUND_TRUTH = (
    Path(__file__).resolve().parent.parent / "examples" / "milestone1" / "ground_truth.json"
)


def _write_broken_copy(mutate) -> Path:
    """Load the Milestone 1 estate, apply `mutate` to the raw dict, write it
    to a temp file, and return the path."""
    raw = json.loads(MILESTONE1_GROUND_TRUTH.read_text(encoding="utf-8"))
    mutate(raw)
    tmp_dir = Path(tempfile.mkdtemp())
    broken_path = tmp_dir / "broken_ground_truth.json"
    broken_path.write_text(json.dumps(raw), encoding="utf-8")
    return broken_path


def _find_app(estate, app_id):
    return next(a for a in estate.applications if a.id == app_id)


def _find_flow(app, flow_id):
    return next(f for f in app.flows if f.id == flow_id)


class TestMilestone1Structure(unittest.TestCase):
    """Positive/structural checks: the estate has the shape this milestone asked for."""

    @classmethod
    def setUpClass(cls):
        cls.estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)

    def test_multiple_applications_and_apis(self):
        self.assertEqual(len(self.estate.applications), 4)
        total_apis = sum(len(app.apis) for app in self.estate.applications)
        self.assertEqual(total_apis, 4)

    def test_multiple_flows_on_one_api(self):
        process_app = _find_app(self.estate, "app-order-processing-process")
        self.assertEqual(len(process_app.flows), 3)

    def test_shared_subflow_reused_within_application(self):
        """flow-validate-order is flow-ref'd by two different flows in the
        same application — the in-app 'shared component' this milestone asked for."""
        process_app = _find_app(self.estate, "app-order-processing-process")
        main_flow = _find_flow(process_app, "flow-process-order-main")
        nightly_flow = _find_flow(process_app, "flow-nightly-reconciliation")

        main_targets = {s.target_flow_id for s in main_flow.steps if isinstance(s, FlowRefStep)}
        nightly_targets = {s.target_flow_id for s in nightly_flow.steps if isinstance(s, FlowRefStep)}

        self.assertIn("flow-validate-order", main_targets)
        self.assertIn("flow-validate-order", nightly_targets)

    def test_cross_api_dependency_chain(self):
        """Experience -> Process -> System(s), expressed via ApiCallStep."""
        experience_app = _find_app(self.estate, "app-order-status-experience")
        experience_flow = _find_flow(experience_app, "flow-order-status-main")
        experience_calls = {
            s.target_api_id for s in experience_flow.steps if isinstance(s, ApiCallStep)
        }
        self.assertEqual(experience_calls, {"api-order-processing-process-v1"})

        process_app = _find_app(self.estate, "app-order-processing-process")
        process_flow = _find_flow(process_app, "flow-process-order-main")
        process_calls = {s.target_api_id for s in process_flow.steps if isinstance(s, ApiCallStep)}
        self.assertEqual(process_calls, {"api-inventory-system-v1", "api-customer-system-v1"})

    def test_mixed_trigger_types_on_same_api(self):
        process_app = _find_app(self.estate, "app-order-processing-process")
        main_flow = _find_flow(process_app, "flow-process-order-main")
        nightly_flow = _find_flow(process_app, "flow-nightly-reconciliation")

        self.assertEqual(main_flow.trigger.type, "http-listener")
        self.assertIsInstance(nightly_flow.trigger, SchedulerTrigger)
        self.assertEqual(nightly_flow.trigger.cron_expression, "0 2 * * *")

    def test_milestone0_example_still_validates_under_updated_schema(self):
        """Backward-compatibility regression check: Milestone 1 only added union
        members (SchedulerTrigger, ApiCallStep) — it must not break the older,
        still-schema_version-0.1.0 Milestone 0 example."""
        estate = validate_ground_truth(MILESTONE0_GROUND_TRUTH)
        self.assertEqual(estate.schema_version, "0.1.0")


class TestMilestone1ReferentialIntegrity(unittest.TestCase):
    """Negative checks: the validator actually rejects invalid references,
    duplicate ids, and malformed new-in-Milestone-1 fields."""

    def test_flow_ref_across_applications_is_rejected(self):
        """FlowRefStep must stay intra-application even though the target
        flow id exists elsewhere in the estate — proves the constraint is
        enforced, not just assumed."""

        def mutate(raw):
            # Point the Experience API's flow-ref at a sub-flow that exists,
            # but lives in a different Application (inventory-system).
            raw["applications"][0]["flows"][0]["steps"][2]["target_flow_id"] = "flow-inventory-log-request"

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(
            any("flow-ref only resolves within the same Application" in e for e in ctx.exception.errors)
        )

    def test_api_call_to_unknown_api_is_rejected(self):
        def mutate(raw):
            raw["applications"][1]["flows"][0]["steps"][1]["target_api_id"] = "api-does-not-exist"

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("api-does-not-exist" in e for e in ctx.exception.errors))

    def test_api_call_can_target_api_in_different_application(self):
        """Sanity check for the flip side of the above: a *valid* cross-app
        ApiCallStep target must NOT be rejected."""
        # The unmodified Milestone 1 estate already does this (Experience ->
        # Process API, a different Application) and validates cleanly.
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        self.assertIsNotNone(estate)

    def test_duplicate_ids_across_different_applications_is_rejected(self):
        def mutate(raw):
            # Give the customer-system app the same id as the inventory-system app.
            raw["applications"][3]["id"] = raw["applications"][2]["id"]

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("Duplicate id" in e for e in ctx.exception.errors))

    def test_scheduler_trigger_missing_field_is_rejected(self):
        def mutate(raw):
            del raw["applications"][1]["flows"][2]["trigger"]["cron_expression"]

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("scheduler trigger missing field" in e for e in ctx.exception.errors))

    def test_api_call_step_missing_field_is_rejected(self):
        def mutate(raw):
            del raw["applications"][1]["flows"][0]["steps"][1]["target_api_id"]

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("api-call step missing field" in e for e in ctx.exception.errors))


if __name__ == "__main__":
    unittest.main()
