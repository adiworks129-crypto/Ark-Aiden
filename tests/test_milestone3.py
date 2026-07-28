"""
Milestone 3 tests: the estate generator (ark/generator/) produces
deterministic, schema-valid, exportable ground truth from configurable
parameters, using realistic layered enterprise topology rather than
arbitrary random connections.

Written as unittest.TestCase for the same zero-dependency reason as the
earlier milestone tests.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.models import ApiCallStep
from ark.core.serialize import estate_to_json
from ark.core.validate import validate_ground_truth
from ark.generator.config import GeneratorConfig, GeneratorConfigError
from ark.generator.generator import generate_estate


def _validate_via_real_file(estate) -> None:
    """Round-trip a generated estate through the exact same
    validate_ground_truth() path a hand-authored file goes through — not
    an in-memory shortcut."""
    tmp_path = Path(tempfile.mkdtemp()) / "generated.json"
    tmp_path.write_text(estate_to_json(estate), encoding="utf-8")
    return validate_ground_truth(tmp_path)


class TestGeneratorDeterminism(unittest.TestCase):
    def test_same_seed_and_config_produce_identical_estate(self):
        config = GeneratorConfig(seed=7, num_experience_apis=2, num_process_apis=2, num_system_apis=3)
        first = generate_estate(config)
        second = generate_estate(config)
        self.assertEqual(first.estate, second.estate)
        self.assertEqual(first.manifest.config, second.manifest.config)

    def test_determinism_survives_unrelated_global_random_state(self):
        """No ambient randomness: disturbing Python's global `random`
        module state between two generation calls must not change the
        result, since every decision should flow through the generator's
        own explicit RNG instance, not the global one."""
        config = GeneratorConfig(seed=7, num_experience_apis=2, num_process_apis=2, num_system_apis=3)

        random.seed(111)
        first = generate_estate(config)

        # Disturb global random state significantly.
        for _ in range(1000):
            random.random()

        second = generate_estate(config)
        self.assertEqual(first.estate, second.estate)

    def test_different_seeds_produce_different_but_valid_estates(self):
        config_a = GeneratorConfig(seed=1, num_experience_apis=1, num_process_apis=2, num_system_apis=3)
        config_b = GeneratorConfig(seed=2, num_experience_apis=1, num_process_apis=2, num_system_apis=3)

        result_a = generate_estate(config_a)
        result_b = generate_estate(config_b)

        self.assertNotEqual(result_a.estate, result_b.estate)
        _validate_via_real_file(result_a.estate)
        _validate_via_real_file(result_b.estate)


class TestGeneratedEstateValidity(unittest.TestCase):
    def test_generated_estate_passes_core_validation(self):
        config = GeneratorConfig(seed=1, num_experience_apis=1, num_process_apis=2, num_system_apis=3)
        result = generate_estate(config)
        validated = _validate_via_real_file(result.estate)
        self.assertEqual(validated.estate_id, result.estate.estate_id)

    def test_generated_estate_exports_via_mulesoft_adapter(self):
        config = GeneratorConfig(seed=3, num_experience_apis=1, num_process_apis=1, num_system_apis=2)
        result = generate_estate(config)
        rendered = MuleSoftAdapter().render(result.estate)

        # Every application should produce exactly one XML artifact and
        # one API metadata artifact.
        num_apps = len(result.estate.applications)
        self.assertEqual(len(rendered.artifacts), num_apps * 2)

        total_entities = sum(len(a["entities"]) for a in rendered.manifest["artifacts"])
        self.assertGreater(total_entities, 0)

    def test_reproducible_exports(self):
        """Determinism must survive the full generate -> render pipeline,
        not just generation on its own."""
        config = GeneratorConfig(seed=5, num_experience_apis=1, num_process_apis=2, num_system_apis=2)
        estate_1 = generate_estate(config).estate
        estate_2 = generate_estate(config).estate

        rendered_1 = MuleSoftAdapter().render(estate_1)
        rendered_2 = MuleSoftAdapter().render(estate_2)

        self.assertEqual(rendered_1.artifacts, rendered_2.artifacts)
        self.assertEqual(rendered_1.manifest, rendered_2.manifest)


class TestGeneratorScalesWithConfig(unittest.TestCase):
    def test_increasing_counts_produce_a_larger_estate(self):
        small_config = GeneratorConfig(seed=1, num_experience_apis=1, num_process_apis=1, num_system_apis=1)
        large_config = GeneratorConfig(seed=1, num_experience_apis=3, num_process_apis=4, num_system_apis=6)

        small = generate_estate(small_config).estate
        large = generate_estate(large_config).estate

        self.assertEqual(len(small.applications), 3)
        self.assertEqual(len(large.applications), 13)

        _validate_via_real_file(small)
        _validate_via_real_file(large)


class TestGeneratorTopologyRealism(unittest.TestCase):
    """Requirement 2's anti-patterns to avoid: full mesh, and no
    semantically-meaningless connectivity. These tests check the
    generator actually avoids them, not just that it claims to."""

    def test_shared_dependency_emerges_under_reasonable_defaults(self):
        """With a small system-API pool shared by multiple process APIs,
        at least one system API should end up called by more than one
        process API (a 'shared dependency') — verified empirically for a
        fixed seed/config, not merely asserted in prose."""
        config = GeneratorConfig(
            seed=1,
            num_experience_apis=1,
            num_process_apis=2,
            num_system_apis=3,
            dependency_density=0.6,
            scheduled_job_ratio=0.6,
        )
        estate = generate_estate(config).estate

        fan_in: dict[str, int] = {}
        for app in estate.applications:
            for flow in app.flows:
                for step in flow.steps:
                    if isinstance(step, ApiCallStep):
                        fan_in[step.target_api_id] = fan_in.get(step.target_api_id, 0) + 1

        self.assertGreaterEqual(max(fan_in.values()), 2, f"No shared dependency found in fan-in map: {fan_in}")

    def test_no_direct_experience_to_system_calls(self):
        """Strictly layered topology: an experience-layer flow's
        ApiCallSteps must only ever target process-layer APIs, never
        system-layer APIs directly — the structural guarantee that rules
        out an "everything connects to everything" mesh."""
        config = GeneratorConfig(seed=9, num_experience_apis=2, num_process_apis=2, num_system_apis=3, dependency_density=0.9)
        estate = generate_estate(config).estate

        api_owner_layer = {}
        for app in estate.applications:
            for api in app.apis:
                # Recover the layer from the app id convention (app-{noun}-{layer}).
                api_owner_layer[api.id] = app.id.rsplit("-", 1)[-1]

        experience_app_ids = {app.id for app in estate.applications if app.id.endswith("-experience")}

        for app in estate.applications:
            if app.id not in experience_app_ids:
                continue
            for flow in app.flows:
                for step in flow.steps:
                    if isinstance(step, ApiCallStep):
                        self.assertEqual(
                            api_owner_layer[step.target_api_id],
                            "process",
                            f"Experience app '{app.id}' called a non-process API directly: {step.target_api_id}",
                        )


class TestGeneratorConfigValidation(unittest.TestCase):
    def test_negative_counts_are_rejected(self):
        with self.assertRaises(GeneratorConfigError):
            GeneratorConfig(seed=1, num_experience_apis=-1)

    def test_all_zero_counts_are_rejected(self):
        with self.assertRaises(GeneratorConfigError):
            GeneratorConfig(seed=1, num_experience_apis=0, num_process_apis=0, num_system_apis=0)

    def test_density_out_of_range_is_rejected(self):
        with self.assertRaises(GeneratorConfigError):
            GeneratorConfig(seed=1, dependency_density=1.5)

    def test_unsupported_topology_style_is_rejected(self):
        with self.assertRaises(GeneratorConfigError):
            GeneratorConfig(seed=1, topology_style="mesh")

    def test_unsupported_naming_style_is_rejected(self):
        with self.assertRaises(GeneratorConfigError):
            GeneratorConfig(seed=1, naming_style="camelCase")


if __name__ == "__main__":
    unittest.main()
