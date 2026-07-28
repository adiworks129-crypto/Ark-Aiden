"""
Milestone 0 tests: prove the ground-truth-as-source-of-truth loop works
before any generator/mutation automation exists to obscure bugs in it.

Written as unittest.TestCase (stdlib only, no pytest required) so they run
with zero installs via `python -m unittest discover`. pytest can also
discover and run unittest.TestCase classes unmodified, so this file will
keep working as-is once pytest is available.

What's checked:

1. The example ground-truth file validates (schema shape + referential
   integrity) — test_example_ground_truth_validates.
2. Rendering that ground truth reproduces the independently hand-authored
   reference MuleSoft XML file byte-for-byte — test_render_matches_golden_file.
   The reference file was authored first, as what a MuleSoft developer would
   actually write; render.py is what has to earn an exact match to it. This
   is the "golden-file test" from the plan's testing strategy.
3. A handful of deliberately-broken copies of the example are rejected with
   the expected error, proving the validator's checks actually fire rather
   than silently passing everything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ark.core.validate import GroundTruthValidationError, validate_ground_truth
from examples.milestone0.render import render_milestone0_xml

MILESTONE0_DIR = Path(__file__).resolve().parent.parent / "examples" / "milestone0"
GROUND_TRUTH_PATH = MILESTONE0_DIR / "ground_truth.json"
GOLDEN_XML_PATH = MILESTONE0_DIR / "expected_render.xml"


def _write_broken_copy(mutate) -> Path:
    """Load the example ground truth, apply `mutate` to the raw dict, write
    it to a temp file, and return the path."""
    raw = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    tmp_dir = Path(tempfile.mkdtemp())
    broken_path = tmp_dir / "broken_ground_truth.json"
    broken_path.write_text(json.dumps(raw), encoding="utf-8")
    return broken_path


class TestMilestone0(unittest.TestCase):
    def test_example_ground_truth_validates(self):
        estate = validate_ground_truth(GROUND_TRUTH_PATH)
        self.assertEqual(estate.estate_id, "milestone0-example")
        self.assertEqual(len(estate.applications), 1)
        self.assertEqual(len(estate.applications[0].flows), 2)

    def test_render_matches_golden_file(self):
        estate = validate_ground_truth(GROUND_TRUTH_PATH)
        rendered = render_milestone0_xml(estate)
        expected = GOLDEN_XML_PATH.read_text(encoding="utf-8")
        self.assertEqual(rendered, expected)

    def test_unknown_flow_ref_target_is_rejected(self):
        def mutate(raw):
            raw["applications"][0]["flows"][0]["steps"][1]["target_flow_id"] = "flow-does-not-exist"

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("flow-does-not-exist" in e for e in ctx.exception.errors))

    def test_duplicate_ids_are_rejected(self):
        def mutate(raw):
            raw["applications"][0]["flows"][1]["id"] = raw["applications"][0]["flows"][0]["id"]

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("Duplicate id" in e for e in ctx.exception.errors))

    def test_unsupported_schema_version_is_rejected(self):
        def mutate(raw):
            raw["schema_version"] = "9.9.9"

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("Unsupported schema_version" in e for e in ctx.exception.errors))

    def test_missing_required_field_is_rejected(self):
        def mutate(raw):
            del raw["applications"][0]["flows"][0]["steps"][0]["dataweave"]

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("missing field" in e for e in ctx.exception.errors))

    def test_sub_flow_with_trigger_is_rejected(self):
        def mutate(raw):
            raw["applications"][0]["flows"][1]["trigger"] = {
                "type": "http-listener",
                "path": "/should-not-exist",
                "method": "GET",
                "listener_config_ref": "HTTP_Listener_config",
            }

        broken_path = _write_broken_copy(mutate)
        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(broken_path)
        self.assertTrue(any("sub_flow' but has a trigger" in e for e in ctx.exception.errors))


if __name__ == "__main__":
    unittest.main()
