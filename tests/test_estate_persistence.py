"""
Session A: "Estate Persistence Layer."

Tests both halves of ark.generator.persistence (save_estate()/load_estate())
directly, and the opt-in wiring into ark.experiment.runner.run_experiment().

Scope, matching the session's own scope: this is a serialization feature.
No new trajectory batches are run for their own sake -- every trajectory
run here exists only to produce a realistic (estate, rendered_artifacts,
ledger, generation_manifest) tuple to round-trip, using the existing
ScriptedAgentClient (offline, no network, no API key) exactly like every
other prior session's tests in this repo.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.serialize import estate_to_dict
from ark.core.validate import validate_ground_truth
from ark.experiment.runner import run_experiment
from ark.experiment.spec import TrajectorySpec
from ark.generator.config import GeneratorConfig
from ark.generator.generator import generate_estate
from ark.generator.persistence import load_estate, save_estate
from ark.harness.scripted_client import ScriptedAgentClient
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = str(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")

_FIXED_AGENT_OUTPUT = {"findings": []}


class TestSaveLoadRoundTripUnmutatedBaseline(unittest.TestCase):
    """The simplest case: a hand-authored baseline (Milestone 1, no
    mutation ever run against it) with no ledger and no generation
    manifest -- both are legitimately absent, not an error state."""

    def test_round_trip_matches_the_original_exactly(self):
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(estate, rendered.artifacts, tmp, "milestone1-check")
            self.assertEqual(estate_dir, Path(tmp) / "milestone1-check")

            loaded = load_estate(estate_dir)

        self.assertEqual(loaded.estate, estate)
        self.assertEqual(loaded.rendered_artifacts, rendered.artifacts)
        self.assertIsNone(loaded.ledger)
        self.assertIsNone(loaded.generation_manifest)

    def test_ground_truth_json_is_loadable_by_existing_tooling_directly(self):
        """Not just "load_estate() can read it back" -- the literal
        requirement from the session spec: validate_ground_truth() itself,
        unmodified, must accept the file save_estate() wrote."""
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(estate, rendered.artifacts, tmp, "tooling-check")
            reloaded_directly = validate_ground_truth(estate_dir / "ground_truth.json")

        self.assertEqual(reloaded_directly, estate)

    def test_rendered_files_actually_land_at_their_nested_relative_paths_on_disk(self):
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)
        self.assertTrue(any("/" in path for path in rendered.artifacts), "expected at least one nested artifact path")

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(estate, rendered.artifacts, tmp, "nested-path-check")
            for relative_path, content in rendered.artifacts.items():
                on_disk = estate_dir / "rendered" / relative_path
                self.assertTrue(on_disk.is_file(), f"expected {on_disk} to exist")
                self.assertEqual(on_disk.read_text(encoding="utf-8"), content)

    def test_manifest_json_records_explicit_nulls_when_neither_is_given(self):
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(estate, rendered.artifacts, tmp, "null-manifest-check")
            import json

            manifest = json.loads((estate_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest, {"ledger": None, "generation_manifest": None})


class TestSaveLoadRoundTripMutatedGeneratedEstate(unittest.TestCase):
    """The full case the session's Goal describes: ground truth + rendered
    artifacts + mutation manifest, from a generator-sourced, actually-
    mutated estate -- proving the ledger ("which operators were applied")
    and the generation manifest both survive a save/load round trip."""

    def test_round_trip_preserves_estate_rendered_artifacts_ledger_and_generation_manifest(self):
        config = GeneratorConfig(seed=11)
        generated = generate_estate(config)
        result = run_trajectory(generated.estate, PROFILES["level_3_legacy"], seed=11)
        self.assertGreater(len(result.ledger.records), 0, "expected level_3_legacy to realize at least one mutation")
        rendered = MuleSoftAdapter().render(result.transformed_estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(
                result.transformed_estate,
                rendered.artifacts,
                tmp,
                "generated-mutated-check",
                ledger=result.ledger,
                generation_manifest=generated.manifest,
            )
            loaded = load_estate(estate_dir)

        self.assertEqual(loaded.estate, result.transformed_estate)
        self.assertEqual(loaded.rendered_artifacts, rendered.artifacts)
        self.assertEqual(loaded.ledger, result.ledger)
        self.assertEqual(loaded.generation_manifest, generated.manifest)
        self.assertGreater(len(loaded.ledger.records), 0)

    def test_saved_ground_truth_json_matches_estate_to_dict_exactly(self):
        """No competing schema: the file on disk is byte-for-byte the same
        JSON estate_to_dict()/json.dumps() would already produce -- not a
        second, subtly-different serialization path."""
        import json

        config = GeneratorConfig(seed=3, domain="finance")
        generated = generate_estate(config)
        rendered = MuleSoftAdapter().render(generated.estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(generated.estate, rendered.artifacts, tmp, "schema-check")
            on_disk = json.loads((estate_dir / "ground_truth.json").read_text(encoding="utf-8"))

        self.assertEqual(on_disk, estate_to_dict(generated.estate))


class TestMutatedEstateJson(unittest.TestCase):
    """Session G: "Mutated JSON View" -- mutated_estate.json is a new,
    optional sibling of ground_truth.json, same serialization path, same
    reload path, never touching ground_truth.json's own format/content."""

    def test_mutated_estate_json_round_trips_and_matches_the_transformed_estate(self):
        config = GeneratorConfig(seed=11)
        generated = generate_estate(config)
        result = run_trajectory(generated.estate, PROFILES["level_3_legacy"], seed=11)
        self.assertNotEqual(
            result.baseline_estate, result.transformed_estate,
            "test setup assumption failed: expected at least one realized mutation",
        )
        rendered = MuleSoftAdapter().render(result.transformed_estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(
                result.baseline_estate, rendered.artifacts, tmp, "mutated-json-check",
                ledger=result.ledger, generation_manifest=generated.manifest,
                mutated_estate=result.transformed_estate,
            )
            on_disk = json.loads((estate_dir / "mutated_estate.json").read_text(encoding="utf-8"))
            loaded = load_estate(estate_dir)

        self.assertEqual(on_disk, estate_to_dict(result.transformed_estate))
        self.assertEqual(loaded.mutated_estate, result.transformed_estate)
        # ground_truth.json itself is unaffected -- still the baseline.
        self.assertEqual(loaded.estate, result.baseline_estate)
        self.assertNotEqual(loaded.estate, loaded.mutated_estate)

    def test_mutated_estate_json_is_loadable_by_existing_tooling_directly(self):
        """Same proof save_estate()'s own docstring already requires for
        ground_truth.json, extended to its new sibling: validate_ground_truth()
        itself, unmodified, must accept the file save_estate() wrote."""
        config = GeneratorConfig(seed=7)
        generated = generate_estate(config)
        result = run_trajectory(generated.estate, PROFILES["level_3_legacy"], seed=7)
        rendered = MuleSoftAdapter().render(result.transformed_estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(
                result.baseline_estate, rendered.artifacts, tmp, "tooling-check-mutated",
                ledger=result.ledger, mutated_estate=result.transformed_estate,
            )
            reloaded_directly = validate_ground_truth(estate_dir / "mutated_estate.json")

        self.assertEqual(reloaded_directly, result.transformed_estate)

    def test_mutated_estate_json_is_not_written_when_omitted(self):
        """The default (no `mutated_estate` argument) writes no file at
        all -- not an empty/null placeholder -- so an existing caller that
        never passes it produces exactly the same on-disk output as
        before this parameter existed."""
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(estate, rendered.artifacts, tmp, "no-mutated-json-check")
            self.assertFalse((estate_dir / "mutated_estate.json").exists())
            loaded = load_estate(estate_dir)

        self.assertIsNone(loaded.mutated_estate)

    def test_load_estate_gracefully_returns_none_for_an_estate_saved_before_this_feature(self):
        """Simulates an estate saved by the pre-Session-G code path: a
        directory with ground_truth.json/manifest.json/rendered/ but no
        mutated_estate.json at all. load_estate() must not error -- it
        reloads `mutated_estate` as None, the same as the omitted-argument
        case above, since the two are indistinguishable on disk."""
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)

        with tempfile.TemporaryDirectory() as tmp:
            estate_dir = save_estate(estate, rendered.artifacts, tmp, "pre-session-g-check")
            self.assertFalse((estate_dir / "mutated_estate.json").is_file())
            loaded = load_estate(estate_dir)  # must not raise

        self.assertIsNone(loaded.mutated_estate)
        self.assertEqual(loaded.estate, estate)


def _report_dict_ignoring_generated_at(report_json_text: str) -> dict:
    """EvaluationMetadata.generated_at is documented (report.py) as "the
    ONE field expected to differ between two otherwise-identical
    evaluation runs" -- same normalization test_milestone6_4.py's own
    determinism tests already use, applied here so two real
    run_experiment() calls' report JSON can be compared for everything
    that should actually be identical."""
    data = json.loads(report_json_text)
    data["metadata"]["generated_at"] = None
    return data


def _analysis_dict_ignoring_generated_at(analysis_json_text: str) -> dict:
    """Same normalization as _report_dict_ignoring_generated_at() above,
    for ExperimentAnalysis's own top-level generated_at field (see
    test_milestone6_5.py's identical pattern)."""
    data = json.loads(analysis_json_text)
    data["generated_at"] = None
    return data


def _make_generator_spec(label: str, seed: int, profile_name: str = "level_2_structural") -> TrajectorySpec:
    return TrajectorySpec(
        label=label, profile_name=profile_name, seed=seed,
        generator_config=GeneratorConfig(seed=seed),
    )


class TestSaveEstatesFlagWiring(unittest.TestCase):
    """The opt-in flag on run_experiment() -- default-off, byte-identical
    when off, and actually writes estates/<label>/ when explicitly on."""

    def test_flag_omitted_creates_no_estates_directory_and_does_not_change_reports_or_analysis(self):
        specs = [_make_generator_spec("flagless", seed=1)]
        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)

        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            run_experiment(specs, client, output_dir=tmp_a)
            run_experiment(specs, client, output_dir=tmp_b, save_estates=False)

            self.assertFalse((Path(tmp_a) / "estates").exists())
            self.assertFalse((Path(tmp_b) / "estates").exists())

            report_a = (Path(tmp_a) / "reports" / "flagless.json").read_text(encoding="utf-8")
            report_b = (Path(tmp_b) / "reports" / "flagless.json").read_text(encoding="utf-8")
            self.assertEqual(
                _report_dict_ignoring_generated_at(report_a), _report_dict_ignoring_generated_at(report_b)
            )

            analysis_a = (Path(tmp_a) / "analysis.json").read_text(encoding="utf-8")
            analysis_b = (Path(tmp_b) / "analysis.json").read_text(encoding="utf-8")
            self.assertEqual(
                _analysis_dict_ignoring_generated_at(analysis_a), _analysis_dict_ignoring_generated_at(analysis_b)
            )

    def test_save_estates_true_without_output_dir_raises(self):
        specs = [_make_generator_spec("no-output-dir", seed=1)]
        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)
        with self.assertRaises(ValueError):
            run_experiment(specs, client, save_estates=True)

    def test_save_estates_true_writes_a_loadable_estate_per_trajectory_label(self):
        specs = [
            _make_generator_spec("wired-a", seed=1),
            _make_generator_spec("wired-b", seed=2, profile_name="level_3_legacy"),
        ]
        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)

        with tempfile.TemporaryDirectory() as tmp:
            run_result = run_experiment(specs, client, output_dir=tmp, save_estates=True)

            for spec in specs:
                estate_dir = Path(tmp) / "estates" / spec.label
                self.assertTrue(estate_dir.is_dir())
                loaded = load_estate(estate_dir)
                self.assertEqual(loaded.rendered_artifacts, run_result.artifacts_by_label[spec.label])
                self.assertIsNotNone(loaded.generation_manifest)
                self.assertEqual(loaded.generation_manifest.seed, spec.generator_config.seed)

    def test_save_estates_persists_the_baseline_not_the_transformed_estate(self):
        """The corrected design decision: ground_truth.json must hold the
        PRE-mutation baseline, not the already-mutated estate -- otherwise
        a diff viewer comparing ground_truth.json against rendered/ would
        be comparing a mutated state against itself. Uses a profile
        already confirmed elsewhere in this file to realize at least one
        real mutation, so baseline != transformed is a meaningful check,
        not a vacuous one."""
        config = GeneratorConfig(seed=21)
        spec = _make_generator_spec("baseline-not-transformed-check", seed=21, profile_name="level_3_legacy")

        # Independently derive what this exact (config, profile, seed)
        # combination produces, the same way test_milestone8.py's own
        # "matches a fresh independent render" tests do, so this doesn't
        # just trust run_experiment()'s internals.
        independent_generated = generate_estate(config)
        independent_result = run_trajectory(independent_generated.estate, PROFILES["level_3_legacy"], seed=21)
        self.assertNotEqual(
            independent_result.baseline_estate, independent_result.transformed_estate,
            "test setup assumption failed: level_3_legacy seed=21 realized no mutation, "
            "so this profile/seed can't distinguish baseline from transformed",
        )

        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)
        with tempfile.TemporaryDirectory() as tmp:
            run_experiment([spec], client, output_dir=tmp, save_estates=True)
            loaded = load_estate(Path(tmp) / "estates" / spec.label)

        self.assertEqual(loaded.estate, independent_result.baseline_estate)
        self.assertNotEqual(loaded.estate, independent_result.transformed_estate)

    def test_save_estates_true_also_writes_mutated_estate_json_matching_the_transformed_estate(self):
        """Session G's wiring into run_experiment(): the same call site
        that already passes baseline_estate as `estate` now also passes
        transformed_estate as `mutated_estate` -- confirmed here against
        an independently-derived run of the same (config, profile, seed),
        the same "don't just trust the code under test" pattern the
        baseline-not-transformed test above already uses."""
        spec = _make_generator_spec("mutated-json-wiring-check", seed=21, profile_name="level_3_legacy")

        independent_generated = generate_estate(GeneratorConfig(seed=21))
        independent_result = run_trajectory(independent_generated.estate, PROFILES["level_3_legacy"], seed=21)
        self.assertNotEqual(
            independent_result.baseline_estate, independent_result.transformed_estate,
            "test setup assumption failed: level_3_legacy seed=21 realized no mutation",
        )

        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)
        with tempfile.TemporaryDirectory() as tmp:
            run_experiment([spec], client, output_dir=tmp, save_estates=True)
            estate_dir = Path(tmp) / "estates" / spec.label
            self.assertTrue((estate_dir / "mutated_estate.json").is_file())
            loaded = load_estate(estate_dir)

        self.assertEqual(loaded.mutated_estate, independent_result.transformed_estate)
        self.assertEqual(loaded.estate, independent_result.baseline_estate)

    def test_estates_directory_name_matches_reports_filename_stem_for_every_trajectory(self):
        """The join a future file-browser/diff-viewer page will lean on:
        estates/<label>/ and reports/<label>.json must use the exact same
        label for every trajectory in a run, with no transformation
        applied to either -- not just "close enough" or "usually the
        same." Checked here against the real on-disk output of a real
        run_experiment() call, for a multi-trajectory run, rather than by
        inspecting the source alone."""
        specs = [
            _make_generator_spec("join-check-a", seed=1),
            _make_generator_spec("join-check-b", seed=2, profile_name="level_3_legacy"),
        ]
        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)

        with tempfile.TemporaryDirectory() as tmp:
            run_experiment(specs, client, output_dir=tmp, save_estates=True)

            report_stems = {p.stem for p in (Path(tmp) / "reports").glob("*.json")}
            estate_dir_names = {p.name for p in (Path(tmp) / "estates").iterdir() if p.is_dir()}
            expected_labels = {spec.label for spec in specs}

            self.assertEqual(report_stems, expected_labels)
            self.assertEqual(estate_dir_names, expected_labels)
            self.assertEqual(report_stems, estate_dir_names)

            for spec in specs:
                self.assertTrue((Path(tmp) / "reports" / f"{spec.label}.json").is_file())
                self.assertTrue((Path(tmp) / "estates" / spec.label).is_dir())

    def test_save_estates_true_still_writes_the_same_reports_and_analysis_as_the_flag_omitted_case(self):
        """The flag adds a new side effect; it must not change any
        existing one -- reports/<label>.json and analysis.json must be
        identical whether or not estates are also being saved."""
        specs = [_make_generator_spec("parity-check", seed=5)]
        client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)

        with tempfile.TemporaryDirectory() as tmp_off, tempfile.TemporaryDirectory() as tmp_on:
            run_experiment(specs, client, output_dir=tmp_off, save_estates=False)
            run_experiment(specs, client, output_dir=tmp_on, save_estates=True)

            report_off = (Path(tmp_off) / "reports" / "parity-check.json").read_text(encoding="utf-8")
            report_on = (Path(tmp_on) / "reports" / "parity-check.json").read_text(encoding="utf-8")
            self.assertEqual(
                _report_dict_ignoring_generated_at(report_off), _report_dict_ignoring_generated_at(report_on)
            )

            analysis_off = (Path(tmp_off) / "analysis.json").read_text(encoding="utf-8")
            analysis_on = (Path(tmp_on) / "analysis.json").read_text(encoding="utf-8")
            self.assertEqual(
                _analysis_dict_ignoring_generated_at(analysis_off), _analysis_dict_ignoring_generated_at(analysis_on)
            )


if __name__ == "__main__":
    unittest.main()
