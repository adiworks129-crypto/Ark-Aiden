"""
Session B: "Project Browser + Ground-Truth/Mutated Diff Viewer."

Tests ark.ui.browser_logic -- the Streamlit-free half of this session,
same discipline tests/test_milestone8.py already established for
ark.ui.logic (see that file's own docstring, and browser_logic.py's).
ark/ui/pages/1_Project_Browser.py itself is not exercised here (no
Streamlit installed in this environment; consistent with how
test_milestone8.py's TestStreamlitAppTestIfAvailable skips its own
equivalent check under the same condition) -- manual verification against
the demo estates is this session's own stated testing approach for the
actual widget page.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.serialize import estate_to_dict
from ark.core.validate import validate_ground_truth
from ark.generator.config import GeneratorConfig
from ark.generator.generator import generate_estate
from ark.generator.persistence import LoadedEstate, save_estate
from ark.mutation.engine import run_trajectory
from ark.mutation.ledger import MutationLedger, MutationRecord
from ark.mutation.profiles import PROFILES
from ark.ui import browser_logic as browser

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = str(REPO_ROOT / "examples" / "milestone1" / "ground_truth.json")


def _save_a_real_mutated_estate(
    tmp: str, estate_id: str, seed: int = 1, *, include_mutated_estate: bool = True
) -> tuple[Path, dict]:
    """Helper: build and save() a real generator-sourced, level_3_legacy-
    mutated estate, returning (estate_dir, extra) where extra carries the
    independently-derived baseline/transformed estates + rendered output
    for tests to compare against, the same "derive it independently, don't
    just trust the code under test" pattern test_milestone8.py's own
    artifact-viewer tests use.

    `include_mutated_estate` (Session G addition, default True): whether
    to also pass `mutated_estate=result.transformed_estate` to
    save_estate() -- set False to simulate an estate saved before Session
    G's mutated_estate.json existed, for the "gracefully absent" tests."""
    config = GeneratorConfig(seed=seed, domain="finance")
    generated = generate_estate(config)
    result = run_trajectory(generated.estate, PROFILES["level_3_legacy"], seed=seed)
    rendered = MuleSoftAdapter().render(result.transformed_estate)
    baseline_rendered = MuleSoftAdapter().render(result.baseline_estate)

    estate_dir = save_estate(
        result.baseline_estate,
        rendered.artifacts,
        tmp,
        estate_id,
        ledger=result.ledger,
        generation_manifest=generated.manifest,
        mutated_estate=result.transformed_estate if include_mutated_estate else None,
    )
    return estate_dir, {
        "baseline_estate": result.baseline_estate,
        "transformed_estate": result.transformed_estate,
        "rendered_artifacts": rendered.artifacts,
        "baseline_rendered_artifacts": baseline_rendered.artifacts,
        "ledger": result.ledger,
        "generation_manifest": generated.manifest,
    }


class TestDiscoverSavedEstates(unittest.TestCase):
    def test_missing_directory_returns_empty_list_not_an_error(self):
        self.assertEqual(browser.discover_saved_estates("/no/such/path/at/all"), [])

    def test_discovers_and_summarizes_a_real_mutated_generated_estate(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, extra = _save_a_real_mutated_estate(tmp, "estate-a", seed=5)
            summaries = browser.discover_saved_estates(tmp)

        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary.estate_id, "estate-a")
        self.assertEqual(summary.ground_truth_estate_id, extra["baseline_estate"].estate_id)
        self.assertEqual(summary.domain, "finance")
        self.assertEqual(summary.profile_name, "level_3_legacy")
        self.assertEqual(summary.trajectory_seed, 5)
        self.assertEqual(summary.mutation_count, len(extra["ledger"].records))
        self.assertGreater(summary.mutation_count, 0)
        self.assertEqual(summary.generator_seed, 5)
        self.assertEqual(summary.generator_version, extra["generation_manifest"].generator_version)
        self.assertTrue(summary.is_readable)
        self.assertTrue(summary.saved_at)  # non-empty ISO8601 string

    def test_estate_with_no_ledger_or_generation_manifest_reports_none_not_zero(self):
        """A plain hand-authored baseline snapshot (no mutation ever run
        against it, no generator involved) -- mutation_count must be None
        (unknown/not-applicable), never 0 (which would falsely imply a
        ledger existed and simply had zero records)."""
        from ark.core.validate import validate_ground_truth

        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        rendered = MuleSoftAdapter().render(estate)
        with tempfile.TemporaryDirectory() as tmp:
            save_estate(estate, rendered.artifacts, tmp, "bare-baseline")
            summaries = browser.discover_saved_estates(tmp)

        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertIsNone(summary.profile_name)
        self.assertIsNone(summary.trajectory_seed)
        self.assertIsNone(summary.mutation_count)
        self.assertIsNone(summary.generator_seed)
        self.assertIsNone(summary.generator_version)
        self.assertIsNone(summary.domain)
        self.assertTrue(summary.is_readable)

    def test_a_malformed_estate_is_flagged_not_readable_but_does_not_break_the_whole_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            _save_a_real_mutated_estate(tmp, "good-estate", seed=2)

            broken_dir = Path(tmp) / "broken-estate"
            broken_dir.mkdir()
            (broken_dir / "manifest.json").write_text("{ not valid json at all", encoding="utf-8")

            summaries = browser.discover_saved_estates(tmp)

        by_id = {s.estate_id: s for s in summaries}
        self.assertEqual(set(by_id), {"good-estate", "broken-estate"})
        self.assertTrue(by_id["good-estate"].is_readable)
        self.assertFalse(by_id["broken-estate"].is_readable)
        self.assertIsNone(by_id["broken-estate"].profile_name)

    def test_results_are_sorted_by_estate_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            for estate_id in ("zebra", "alpha", "mike"):
                _save_a_real_mutated_estate(tmp, estate_id, seed=1)
            summaries = browser.discover_saved_estates(tmp)

        self.assertEqual([s.estate_id for s in summaries], ["alpha", "mike", "zebra"])


class TestFilterSavedEstates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _save_a_real_mutated_estate(self.tmp.name, "finance-legacy-seed5", seed=5)
        self.summaries = browser.discover_saved_estates(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_query_returns_everything_unchanged(self):
        self.assertEqual(browser.filter_saved_estates(self.summaries, ""), self.summaries)
        self.assertEqual(browser.filter_saved_estates(self.summaries, "   "), self.summaries)

    def test_matches_by_estate_id_case_insensitively(self):
        result = browser.filter_saved_estates(self.summaries, "FINANCE-LEGACY")
        self.assertEqual(len(result), 1)

    def test_matches_by_profile_name(self):
        result = browser.filter_saved_estates(self.summaries, "level_3_legacy")
        self.assertEqual(len(result), 1)

    def test_matches_by_domain(self):
        result = browser.filter_saved_estates(self.summaries, "finance")
        self.assertEqual(len(result), 1)

    def test_no_match_returns_empty_list(self):
        self.assertEqual(browser.filter_saved_estates(self.summaries, "no-such-thing-here"), [])


class TestBuildFileTree(unittest.TestCase):
    def test_flat_and_nested_paths_produce_the_expected_structure(self):
        paths = [
            "AppA/src/main/mule/AppA.xml",
            "AppA/src/main/resources/api-a.yaml",
            "AppB/src/main/mule/AppB.xml",
        ]
        tree = browser.build_file_tree(paths)

        self.assertEqual(tree[browser.GROUND_TRUTH_FILENAME], browser.GROUND_TRUTH_LEAF)
        self.assertEqual(tree["manifest.json"], browser.MANIFEST_LEAF)
        rendered = tree["rendered"]
        self.assertEqual(
            rendered["AppA"]["src"]["main"]["mule"]["AppA.xml"], "AppA/src/main/mule/AppA.xml"
        )
        self.assertEqual(
            rendered["AppA"]["src"]["main"]["resources"]["api-a.yaml"], "AppA/src/main/resources/api-a.yaml"
        )
        self.assertEqual(rendered["AppB"]["src"]["main"]["mule"]["AppB.xml"], "AppB/src/main/mule/AppB.xml")

    def test_empty_artifact_list_still_produces_an_empty_rendered_folder(self):
        tree = browser.build_file_tree([])
        self.assertEqual(tree["rendered"], {})
        self.assertEqual(tree["ground_truth.json"], browser.GROUND_TRUTH_LEAF)
        self.assertEqual(tree["manifest.json"], browser.MANIFEST_LEAF)


class TestBaselineDiffPairing(unittest.TestCase):
    def test_baseline_text_matches_an_independently_rendered_baseline_for_a_changed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate_dir, extra = _save_a_real_mutated_estate(tmp, "diff-check", seed=9)
            loaded = browser.open_saved_estate(estate_dir)

            changed_path = next(
                path for path, after in extra["rendered_artifacts"].items()
                if extra["baseline_rendered_artifacts"].get(path) != after
            )

            before = browser.baseline_text_for_rendered_path(loaded, changed_path)

        self.assertEqual(before, extra["baseline_rendered_artifacts"][changed_path])
        self.assertNotEqual(before, extra["rendered_artifacts"][changed_path])
        self.assertFalse(browser.is_identical(before, extra["rendered_artifacts"][changed_path]))

    def test_baseline_text_is_none_for_a_path_with_no_baseline_counterpart(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate_dir, _ = _save_a_real_mutated_estate(tmp, "no-counterpart-check", seed=1)
            loaded = browser.open_saved_estate(estate_dir)

            missing = browser.baseline_text_for_rendered_path(loaded, "NoSuchApp/does/not/exist.xml")

        self.assertIsNone(missing)

    def test_is_identical_treats_none_as_empty_string(self):
        self.assertTrue(browser.is_identical(None, ""))
        self.assertTrue(browser.is_identical("", None))
        self.assertTrue(browser.is_identical(None, None))
        self.assertFalse(browser.is_identical(None, "something"))

    def test_html_diff_table_contains_diff_markup_for_genuinely_different_text(self):
        html = browser.html_diff_table("line one\nline two\n", "line one\nline TWO changed\n")
        self.assertIn("<table", html)
        self.assertTrue(any(marker in html for marker in ("diff_chg", "diff_add", "diff_sub")))

    def test_html_diff_table_handles_a_none_side_without_erroring(self):
        html = browser.html_diff_table(None, "brand new content\n")
        self.assertIn("<table", html)


class TestReadSavedFileText(unittest.TestCase):
    def test_reads_back_exactly_what_was_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate_dir, _ = _save_a_real_mutated_estate(tmp, "read-check", seed=1)
            ground_truth_text = browser.read_saved_file_text(estate_dir, "ground_truth.json")
            manifest_text = browser.read_saved_file_text(estate_dir, "manifest.json")

        self.assertIsNotNone(ground_truth_text)
        json.loads(ground_truth_text)  # must be valid JSON
        self.assertIsNotNone(manifest_text)
        json.loads(manifest_text)

    def test_missing_file_returns_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(browser.read_saved_file_text(tmp, "does_not_exist.json"))


class TestDiscoverEstateRoots(unittest.TestCase):
    """Session D: 'Estate Directory Discoverability.' discover_saved_estates()
    itself is reused verbatim throughout (never reimplemented) -- these
    tests are about finding *which* estates/ folders exist, not about how
    a folder's own contents get summarized (that's still Session B's job,
    untouched)."""

    def _touch_manifest_mtime(self, estates_dir: Path, estate_id: str, timestamp: float) -> None:
        manifest_path = estates_dir / estate_id / "manifest.json"
        os.utime(manifest_path, (timestamp, timestamp))

    def test_finds_a_valid_estates_folder_with_correct_count_and_recency(self):
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "proj" / "run_output" / "estates"
            estates_dir.mkdir(parents=True)
            _save_a_real_mutated_estate(str(estates_dir), "e1", seed=1)
            _save_a_real_mutated_estate(str(estates_dir), "e2", seed=2)

            summaries = browser.discover_saved_estates(str(estates_dir))
            expected_most_recent = max(s.saved_at for s in summaries)

            roots = browser.discover_estate_roots([tmp])

        self.assertEqual(len(roots), 1)
        self.assertEqual(Path(roots[0].path).resolve(), estates_dir.resolve())
        self.assertEqual(roots[0].estate_count, 2)
        self.assertEqual(roots[0].most_recent_saved_at, expected_most_recent)

    def test_ignores_an_estates_folder_with_no_saved_estates_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_estates_dir = Path(tmp) / "empty_run" / "estates"
            empty_estates_dir.mkdir(parents=True)
            # A stray subfolder with no manifest.json at all -- still not
            # a saved estate by discover_saved_estates()'s own definition.
            (empty_estates_dir / "not-an-estate").mkdir()

            roots = browser.discover_estate_roots([tmp])

        self.assertEqual(roots, [])

    def test_a_folder_with_only_a_malformed_manifest_is_still_discovered(self):
        """Matches discover_saved_estates()'s own, unmodified behavior:
        a subfolder with a manifest.json present (even unparsable) still
        counts as "has a saved estate" for discovery purposes -- Session D
        doesn't add a stricter validity bar than Session B already has."""
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "proj" / "estates"
            broken_dir = estates_dir / "broken"
            broken_dir.mkdir(parents=True)
            (broken_dir / "manifest.json").write_text("not json", encoding="utf-8")

            roots = browser.discover_estate_roots([tmp])

        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].estate_count, 1)
        self.assertTrue(roots[0].most_recent_saved_at)  # mtime-derived, still non-empty

    def test_sorts_multiple_discovered_roots_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            older_dir = Path(tmp) / "older_run" / "estates"
            newer_dir = Path(tmp) / "newer_run" / "estates"
            older_dir.mkdir(parents=True)
            newer_dir.mkdir(parents=True)

            _save_a_real_mutated_estate(str(older_dir), "old-estate", seed=1)
            _save_a_real_mutated_estate(str(newer_dir), "new-estate", seed=2)
            self._touch_manifest_mtime(older_dir, "old-estate", 1_000_000_000)
            self._touch_manifest_mtime(newer_dir, "new-estate", 2_000_000_000)

            roots = browser.discover_estate_roots([tmp])

        self.assertEqual(len(roots), 2)
        self.assertEqual(Path(roots[0].path).resolve(), newer_dir.resolve())
        self.assertEqual(Path(roots[1].path).resolve(), older_dir.resolve())

    def test_deduplicates_a_root_reachable_via_two_overlapping_search_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "proj" / "estates"
            estates_dir.mkdir(parents=True)
            _save_a_real_mutated_estate(str(estates_dir), "e1", seed=1)

            roots = browser.discover_estate_roots([tmp, str(Path(tmp) / "proj")])

        self.assertEqual(len(roots), 1)

    def test_nonexistent_search_root_is_skipped_silently(self):
        self.assertEqual(browser.discover_estate_roots(["/no/such/path/at/all"]), [])

    def test_skips_dot_git_and_similar_junk_directory_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk_estates_dir = Path(tmp) / ".git" / "estates"
            junk_estates_dir.mkdir(parents=True)
            _save_a_real_mutated_estate(str(junk_estates_dir), "shouldnt-be-found", seed=1)

            roots = browser.discover_estate_roots([tmp])

        self.assertEqual(roots, [])

    def test_default_search_root_is_examples_and_finds_the_real_demo_estates(self):
        """Locks in the derived default (see DEFAULT_ESTATE_SEARCH_ROOTS'
        own docstring for why "examples" and "ui_runs" specifically) and
        ties it back to Session B's own demo data, which really exists in
        this checkout."""
        self.assertEqual(browser.DEFAULT_ESTATE_SEARCH_ROOTS, ("examples", "ui_runs"))

        roots = browser.discover_estate_roots()
        paths = {str(Path(r.path).as_posix()) for r in roots}
        self.assertIn("examples/estate_browser_demo/run_output/estates", paths)

    def test_ui_runs_convention_is_discovered_the_same_way_examples_is(self):
        """The live Streamlit "Run Experiment" button saves under
        ui_runs/<slug>/estates -- confirms that shape is discovered by the
        same generic estates/-folder scan as the examples/ convention,
        using an explicit search_roots (not the real cwd-relative default)
        so this test doesn't depend on a real UI click having happened in
        this checkout."""
        with tempfile.TemporaryDirectory() as tmp:
            ui_runs_dir = Path(tmp) / "ui_runs"
            estates_dir = ui_runs_dir / "my-live-run" / "estates"
            estates_dir.mkdir(parents=True)
            _save_a_real_mutated_estate(str(estates_dir), "level_1_minor-seed1", seed=1)

            roots = browser.discover_estate_roots([str(ui_runs_dir)])

        self.assertEqual(len(roots), 1)
        self.assertEqual(Path(roots[0].path).resolve(), estates_dir.resolve())
        self.assertEqual(roots[0].estate_count, 1)


class TestMutatedEstateLeaf(unittest.TestCase):
    """Session G, part 2: "Mutated JSON View." build_file_tree()'s new
    MUTATED_ESTATE_LEAF entry, and how it reads back for both a freshly
    saved estate (has mutated_estate.json) and one saved before this
    feature existed (doesn't)."""

    def test_build_file_tree_always_includes_the_mutated_estate_leaf(self):
        tree = browser.build_file_tree([])
        self.assertEqual(tree[browser.MUTATED_ESTATE_FILENAME], browser.MUTATED_ESTATE_LEAF)

    def test_build_file_tree_top_level_order_places_mutated_estate_directly_after_ground_truth(self):
        tree = browser.build_file_tree([])
        self.assertEqual(
            list(tree.keys()),
            [
                browser.GROUND_TRUTH_FILENAME,
                browser.MUTATED_ESTATE_FILENAME,
                browser.MANIFEST_FILENAME,
                browser.RENDERED_DIRNAME,
            ],
        )

    def test_mutated_estate_file_is_readable_and_matches_the_transformed_estate_for_a_fresh_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate_dir, extra = _save_a_real_mutated_estate(tmp, "mutated-leaf-check", seed=3)
            text = browser.read_saved_file_text(estate_dir, browser.MUTATED_ESTATE_FILENAME)
            loaded = browser.open_saved_estate(estate_dir)

        self.assertIsNotNone(text)
        self.assertEqual(json.loads(text), estate_to_dict(extra["transformed_estate"]))
        self.assertEqual(loaded.mutated_estate, extra["transformed_estate"])

    def test_mutated_estate_file_is_gracefully_absent_for_an_estate_saved_before_this_feature(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate_dir, _ = _save_a_real_mutated_estate(
                tmp, "no-mutated-leaf-check", seed=5, include_mutated_estate=False
            )
            text = browser.read_saved_file_text(estate_dir, browser.MUTATED_ESTATE_FILENAME)
            loaded = browser.open_saved_estate(estate_dir)  # must not raise

        self.assertIsNone(text)
        self.assertIsNone(loaded.mutated_estate)


class TestMutationHighlighting(unittest.TestCase):
    """Session G, part 3: "Interactive Mutation Highlighting." Ledger-
    driven, not difflib-driven -- these tests build a known
    transformation_history by hand (a real MutationLedger with real
    MutationRecords) so the expected set of flagged entities and their
    rationales is known exactly, independent of what any real mutation
    profile happens to realize for a given seed."""

    def _minimal_loaded_estate(self, ledger: MutationLedger | None) -> LoadedEstate:
        estate = validate_ground_truth(MILESTONE1_GROUND_TRUTH)
        return LoadedEstate(estate=estate, rendered_artifacts={}, ledger=ledger)

    def _make_ledger(self, records: list[MutationRecord]) -> MutationLedger:
        return MutationLedger(
            baseline_estate_id="estate-x",
            baseline_schema_version="0.3.0",
            trajectory_seed=1,
            profile_name="level_3_legacy",
            engine_version="0.1.0",
            ledger_schema_version="0.1.0",
            records=records,
        )

    def test_highlights_flatten_to_one_per_affected_entity_per_record(self):
        record_a = MutationRecord(
            mutation_id="m-1", transformation_type="naming_drift",
            affected_entity_ids=["step-1"],
            original_state={"step-1": {"name": "Validate Order"}},
            transformed_state={"step-1": {"name": "validateOrder"}},
            severity=0.3, rationale="Casing drift on doc:name.",
            sequence_index=0, timestamp="2026-01-01T00:00:00Z", seed=1,
        )
        record_b = MutationRecord(
            mutation_id="m-2", transformation_type="dependency_change",
            affected_entity_ids=["step-2", "step-3"],
            original_state={}, transformed_state={},
            severity=0.5, rationale="Swapped target API.",
            sequence_index=1, timestamp="2026-01-01T00:00:01Z", seed=1,
        )
        loaded = self._minimal_loaded_estate(self._make_ledger([record_a, record_b]))

        highlights = browser.mutation_highlights_for_estate(loaded)

        # Exactly the entities the ledger names -- not a broader or
        # narrower set.
        self.assertEqual({h.entity_id for h in highlights}, {"step-1", "step-2", "step-3"})

        grouped = browser.highlights_by_entity_id(highlights)
        self.assertEqual(grouped["step-1"][0].rationale, "Casing drift on doc:name.")
        self.assertEqual(grouped["step-1"][0].mutation_id, "m-1")
        self.assertEqual(grouped["step-2"][0].rationale, "Swapped target API.")
        self.assertEqual(grouped["step-3"][0].rationale, "Swapped target API.")
        self.assertNotIn("step-4", grouped)

    def test_an_entity_touched_by_two_records_keeps_both_rationales_in_ledger_order(self):
        record_a = MutationRecord(
            mutation_id="m-1", transformation_type="naming_drift",
            affected_entity_ids=["step-1"], original_state={}, transformed_state={},
            severity=0.2, rationale="First rationale.",
            sequence_index=0, timestamp="2026-01-01T00:00:00Z", seed=1,
        )
        record_b = MutationRecord(
            mutation_id="m-2", transformation_type="schema_inconsistency",
            affected_entity_ids=["step-1"], original_state={}, transformed_state={},
            severity=0.4, rationale="Second rationale.",
            sequence_index=1, timestamp="2026-01-01T00:00:01Z", seed=1,
        )
        loaded = self._minimal_loaded_estate(self._make_ledger([record_a, record_b]))

        grouped = browser.highlights_by_entity_id(browser.mutation_highlights_for_estate(loaded))

        self.assertEqual([h.rationale for h in grouped["step-1"]], ["First rationale.", "Second rationale."])

    def test_no_ledger_produces_no_highlights_not_an_error(self):
        loaded = self._minimal_loaded_estate(None)
        self.assertEqual(browser.mutation_highlights_for_estate(loaded), [])

    def test_ledger_with_no_records_produces_no_highlights(self):
        loaded = self._minimal_loaded_estate(self._make_ledger([]))
        self.assertEqual(browser.mutation_highlights_for_estate(loaded), [])

    def test_against_a_real_trajectory_every_ledger_affected_entity_is_flagged(self):
        """Same "known transformation_history" guarantee, checked against
        a real, generator-sourced, mutated estate's own real ledger
        (loaded back off disk), not a hand-built one -- confirms
        mutation_highlights_for_estate() agrees with the real ledger
        exactly, for every real record it contains."""
        with tempfile.TemporaryDirectory() as tmp:
            estate_dir, extra = _save_a_real_mutated_estate(tmp, "highlight-real-check", seed=2)
            loaded = browser.open_saved_estate(estate_dir)

        highlights = browser.mutation_highlights_for_estate(loaded)
        expected_entity_ids = {
            entity_id for record in extra["ledger"].records for entity_id in record.affected_entity_ids
        }
        self.assertEqual({h.entity_id for h in highlights}, expected_entity_ids)
        for record in extra["ledger"].records:
            for entity_id in record.affected_entity_ids:
                matching = [h for h in highlights if h.entity_id == entity_id and h.mutation_id == record.mutation_id]
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0].rationale, record.rationale)


class TestHighlightedMutatedJsonHtml(unittest.TestCase):
    """highlighted_mutated_json_html() -- the visual half of Session G's
    highlighting. Purely string-in/string-out, so these are pure unit
    tests with no saved estate involved at all."""

    _SAMPLE_ESTATE_JSON = json.dumps(
        {
            "estate_id": "e1",
            "applications": [
                {
                    "id": "app-1",
                    "name": "A",
                    "apis": [],
                    "flows": [
                        {
                            "id": "flow-1",
                            "name": "F",
                            "steps": [
                                {"id": "step-1", "kind": "logger"},
                                {"id": "step-2", "kind": "logger"},
                            ],
                        }
                    ],
                }
            ],
        },
        indent=2,
    )

    def test_only_the_given_entity_ids_are_wrapped_in_mark(self):
        html = browser.highlighted_mutated_json_html(self._SAMPLE_ESTATE_JSON, {"step-1"})

        self.assertIn("<mark>", html)
        self.assertIn("&quot;id&quot;: &quot;step-1&quot;</mark>", html)
        for untouched in ("app-1", "flow-1", "step-2"):
            self.assertNotIn(f"<mark>&quot;id&quot;: &quot;{untouched}&quot;</mark>", html)

    def test_multiple_highlighted_ids_are_each_wrapped(self):
        html = browser.highlighted_mutated_json_html(self._SAMPLE_ESTATE_JSON, {"app-1", "step-2"})
        self.assertIn("&quot;id&quot;: &quot;app-1&quot;</mark>", html)
        self.assertIn("&quot;id&quot;: &quot;step-2&quot;</mark>", html)
        self.assertNotIn("&quot;id&quot;: &quot;flow-1&quot;</mark>", html)
        self.assertNotIn("&quot;id&quot;: &quot;step-1&quot;</mark>", html)

    def test_empty_highlight_set_produces_no_mark_tags(self):
        html = browser.highlighted_mutated_json_html(self._SAMPLE_ESTATE_JSON, set())
        self.assertNotIn("<mark>", html)

    def test_returns_a_self_contained_pre_block_safe_for_markdown(self):
        html = browser.highlighted_mutated_json_html(self._SAMPLE_ESTATE_JSON, set())
        self.assertIn("<style>", html)
        self.assertIn('<pre class="mutation-highlight-json">', html)

    def test_an_entity_id_that_does_not_appear_in_the_text_is_a_silent_no_op(self):
        """Highlighting an id the ledger names but that isn't actually in
        this particular file's JSON (shouldn't happen given how this is
        wired, but the function itself shouldn't error either way) --
        confirms this doesn't raise or corrupt the rest of the text."""
        html = browser.highlighted_mutated_json_html(self._SAMPLE_ESTATE_JSON, {"no-such-entity"})
        self.assertNotIn("<mark>", html)
        self.assertIn("step-1", html)


class TestDeleteEstate(unittest.TestCase):
    """Session G, part 1: "Estate Deletion." The only destructive function
    in ark.ui.browser_logic -- tested in isolation from every read-only
    function above, per this session's own instruction."""

    def test_deletes_the_correct_folder_and_leaves_siblings_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "estates"
            _save_a_real_mutated_estate(str(estates_dir), "keep-me", seed=1)
            _save_a_real_mutated_estate(str(estates_dir), "delete-me", seed=2)

            removed = browser.delete_estate("delete-me", estates_dir)

            self.assertTrue(removed)
            self.assertFalse((estates_dir / "delete-me").exists())
            self.assertTrue((estates_dir / "keep-me").exists())
            self.assertTrue((estates_dir / "keep-me" / "manifest.json").is_file())

    def test_nonexistent_estate_id_is_a_no_op_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "estates"
            estates_dir.mkdir()
            removed = browser.delete_estate("no-such-estate", estates_dir)
        self.assertFalse(removed)

    def test_nonexistent_estates_dir_is_also_a_no_op_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            removed = browser.delete_estate("anything", Path(tmp) / "never-created")
        self.assertFalse(removed)

    def test_refuses_a_path_traversal_style_estate_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "estates"
            estates_dir.mkdir()
            outside = Path(tmp) / "escaped-marker"
            outside.mkdir()

            with self.assertRaises(ValueError):
                browser.delete_estate("../escaped-marker", estates_dir)

        # (implicitly) nothing outside estates_dir was ever touched --
        # the ValueError above is raised before any filesystem mutation.

    def test_deleted_estate_disappears_from_discover_saved_estates(self):
        """The list-view-refresh guarantee, checked at the logic layer:
        discover_saved_estates() (called fresh by the page on every
        rerun) simply no longer finds a deleted estate -- no caching to
        invalidate, no special-case needed."""
        with tempfile.TemporaryDirectory() as tmp:
            estates_dir = Path(tmp) / "estates"
            _save_a_real_mutated_estate(str(estates_dir), "will-be-deleted", seed=1)
            before = browser.discover_saved_estates(estates_dir)
            self.assertEqual({s.estate_id for s in before}, {"will-be-deleted"})

            browser.delete_estate("will-be-deleted", estates_dir)
            after = browser.discover_saved_estates(estates_dir)

        self.assertEqual(after, [])


class TestImportBoundary(unittest.TestCase):
    """Same rule and same technique test_milestone8.py's TestImportBoundary
    already enforces for ark.ui.logic/ark.ui.app: ark.ui.browser_logic must
    never import ark.mutation.engine, ark.mutation.operators, or
    ark.mutation.ledger directly -- it only ever reaches saved mutation
    data through ark.generator.persistence.load_estate() (see this
    module's own docstring)."""

    _FORBIDDEN_PREFIXES = ("ark.mutation.engine", "ark.mutation.operators", "ark.mutation.ledger")

    def test_browser_logic_never_imports_mutation_internals(self):
        tree = ast.parse(inspect.getsource(browser))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)

        for name in names:
            for forbidden in self._FORBIDDEN_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"ark.ui.browser_logic imports {name}, a forbidden mutation-internals module",
                )

    def test_project_browser_page_source_never_imports_mutation_internals(self):
        """Mirrors test_milestone8.py's app.py check: read the page's
        source as text and AST-parse it directly, rather than importing
        it, so this works whether or not Streamlit is installed here."""
        page_path = REPO_ROOT / "ark" / "ui" / "pages" / "1_Project_Browser.py"
        source = page_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)

        for name in names:
            for forbidden in self._FORBIDDEN_PREFIXES:
                self.assertFalse(
                    name == forbidden or name.startswith(forbidden + "."),
                    f"1_Project_Browser.py imports {name}, a forbidden mutation-internals module",
                )


if __name__ == "__main__":
    unittest.main()
