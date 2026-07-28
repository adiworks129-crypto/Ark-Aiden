"""
Feature 2 tests: domain-conditioned component injection ("organized
randomness").

Unit tests only, per this feature's own scope — no new trajectory
BATCHES (ark.experiment.run_experiment/run_trajectory_spec) and no agent
calls anywhere in this file. Several tests below do call
ark.mutation.engine.run_trajectory() directly (with the new, opt-in
"domain_injection_preview" profile) — that is the mutation engine itself,
with zero agent involvement, exactly the same pattern every existing
Milestone 4 operator test already uses (see tests/test_milestone4.py);
it is not the "trajectory batch"/"live agent" testing this feature's task
spec explicitly defers to a later, separate session.
"""

from __future__ import annotations

import copy
import dataclasses
import random
import unittest
from pathlib import Path

from ark.core import validate as validate_module
from ark.core.models import ConnectorStep, GroundTruthEstate
from ark.core.serialize import estate_to_dict
from ark.core.validate import (
    GroundTruthValidationError,
    validate_estate_object,
    validate_ground_truth,
)
from ark.evaluator.issues import derive_issues
from ark.evaluator.schema import ISSUE_TYPE_TAXONOMY
from ark.generator import config as generator_config_module
from ark.generator.config import GeneratorConfig, GeneratorConfigError
from ark.generator.domain_plausibility import (
    DEFAULT_DOMAIN_PLAUSIBILITY_PATH,
    SUPPORTED_DOMAINS,
    load_domain_plausibility,
    plausible_components_for,
)
from ark.generator.generator import generate_estate
from ark.mutation.engine import run_trajectory
from ark.mutation.operators import DomainComponentInjectionOperator
from ark.mutation.profiles import (
    LEVEL_1_OPERATORS,
    LEVEL_2_OPERATORS,
    LEVEL_3_OPERATORS,
    PROFILES,
)
from ark.mutation.registry import OPERATOR_REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONE1_GROUND_TRUTH = REPO_ROOT / "examples" / "milestone1" / "ground_truth.json"

_TRANSFORMATION_TYPE = "domain_implausible_component"


def _load_estate(path=MILESTONE1_GROUND_TRUTH) -> GroundTruthEstate:
    return validate_ground_truth(path)


def _domain_tagged(estate: GroundTruthEstate, domain: str) -> GroundTruthEstate:
    return dataclasses.replace(estate, domain=domain)


# ---------------------------------------------------------------------------
# 1. The `domain` concept on GroundTruthEstate.
# ---------------------------------------------------------------------------


class TestDomainConceptOnEstate(unittest.TestCase):
    def test_domain_defaults_to_none(self):
        estate = GroundTruthEstate(estate_id="e1")
        self.assertIsNone(estate.domain)

    def test_hand_authored_milestone1_estate_has_no_domain_by_default(self):
        """Backward compatibility: a file written before this field
        existed simply has no "domain" key and loads fine, with
        domain=None."""
        estate = _load_estate()
        self.assertIsNone(estate.domain)

    def test_validate_ground_truth_accepts_a_supported_domain(self):
        import json
        import tempfile

        raw = json.loads(MILESTONE1_GROUND_TRUTH.read_text(encoding="utf-8"))
        raw["domain"] = "finance"
        tmp_path = Path(tempfile.mkdtemp()) / "with_domain.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")

        estate = validate_ground_truth(tmp_path)
        self.assertEqual(estate.domain, "finance")

    def test_validate_ground_truth_rejects_an_unsupported_domain(self):
        import json
        import tempfile

        raw = json.loads(MILESTONE1_GROUND_TRUTH.read_text(encoding="utf-8"))
        raw["domain"] = "healthcare"  # not one of the two supported domains
        tmp_path = Path(tempfile.mkdtemp()) / "bad_domain.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaises(GroundTruthValidationError) as ctx:
            validate_ground_truth(tmp_path)
        self.assertTrue(any("domain" in e for e in ctx.exception.errors))

    def test_domain_round_trips_through_serialize_and_validate(self):
        estate = _domain_tagged(_load_estate(), "retail")
        raw = estate_to_dict(estate)
        self.assertEqual(raw["domain"], "retail")

    def test_only_two_domains_are_valid_in_validate_py(self):
        self.assertEqual(set(validate_module._VALID_DOMAINS), set(SUPPORTED_DOMAINS))
        self.assertEqual(set(validate_module._VALID_DOMAINS), {"finance", "retail"})


# ---------------------------------------------------------------------------
# 2. GeneratorConfig.domain plumbing.
# ---------------------------------------------------------------------------


class TestGeneratorConfigDomain(unittest.TestCase):
    def test_domain_defaults_to_none(self):
        config = GeneratorConfig(seed=1)
        self.assertIsNone(config.domain)

    def test_accepts_supported_domains(self):
        for domain in ("finance", "retail"):
            config = GeneratorConfig(seed=1, domain=domain)
            self.assertEqual(config.domain, domain)

    def test_rejects_unsupported_domain(self):
        with self.assertRaises(GeneratorConfigError):
            GeneratorConfig(seed=1, domain="healthcare")

    def test_generate_estate_propagates_domain_onto_the_estate(self):
        generated = generate_estate(GeneratorConfig(seed=1, domain="finance"))
        self.assertEqual(generated.estate.domain, "finance")

    def test_generate_estate_leaves_domain_none_when_not_set(self):
        generated = generate_estate(GeneratorConfig(seed=1))
        self.assertIsNone(generated.estate.domain)

    def test_config_supported_domains_matches_validate_py(self):
        self.assertEqual(generator_config_module.SUPPORTED_DOMAINS, set(validate_module._VALID_DOMAINS))


# ---------------------------------------------------------------------------
# 3. The plausibility mapping (ark/generator/domain_plausibility.json).
# ---------------------------------------------------------------------------


class TestDomainPlausibilityMapping(unittest.TestCase):
    def test_default_mapping_file_exists_and_loads(self):
        self.assertTrue(DEFAULT_DOMAIN_PLAUSIBILITY_PATH.exists())
        mapping = load_domain_plausibility()
        self.assertIn("domains", mapping)

    def test_mapping_domains_match_the_two_supported_domains_exactly(self):
        mapping = load_domain_plausibility()
        self.assertEqual(set(mapping["domains"].keys()), {"finance", "retail"})
        self.assertEqual(set(mapping["domains"].keys()), set(SUPPORTED_DOMAINS))

    def test_every_component_has_key_display_name_and_justification(self):
        mapping = load_domain_plausibility()
        for domain_name, entry in mapping["domains"].items():
            for component in entry["plausible_components"]:
                for field_name in ("key", "display_name", "justification"):
                    with self.subTest(domain=domain_name, component=component.get("key")):
                        self.assertIn(field_name, component)
                        self.assertTrue(component[field_name].strip())

    def test_component_keys_are_unique_within_each_domain(self):
        mapping = load_domain_plausibility()
        for domain_name, entry in mapping["domains"].items():
            keys = [c["key"] for c in entry["plausible_components"]]
            with self.subTest(domain=domain_name):
                self.assertEqual(len(keys), len(set(keys)))

    def test_no_component_key_is_shared_between_the_two_domains(self):
        """The whole point of the mapping is that each component is
        distinctive to ONE domain -- a key appearing under both would
        undermine the "doesn't belong here" signal entirely."""
        mapping = load_domain_plausibility()
        finance_keys = {c["key"] for c in mapping["domains"]["finance"]["plausible_components"]}
        retail_keys = {c["key"] for c in mapping["domains"]["retail"]["plausible_components"]}
        self.assertEqual(finance_keys & retail_keys, set())

    def test_plausible_components_for_returns_the_right_domain(self):
        finance_components = plausible_components_for("finance")
        self.assertTrue(all("key" in c for c in finance_components))
        self.assertGreater(len(finance_components), 0)

    def test_plausible_components_for_unknown_domain_raises_key_error(self):
        with self.assertRaises(KeyError):
            plausible_components_for("healthcare")


# ---------------------------------------------------------------------------
# 4. ConnectorStep model shape + structural validation.
# ---------------------------------------------------------------------------


class TestConnectorStepModel(unittest.TestCase):
    def test_connector_step_parses_from_a_raw_step_dict(self):
        raw = copy.deepcopy(_raw_milestone1())
        flow = raw["applications"][0]["flows"][0]
        flow["steps"].append(
            {
                "id": "step-test-connector",
                "kind": "connector",
                "name": "Integrate with Core Banking Platform",
                "description": "Test step.",
                "connector_type": "core_banking_platform",
            }
        )
        import json
        import tempfile

        tmp_path = Path(tempfile.mkdtemp()) / "with_connector_step.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")

        estate = validate_ground_truth(tmp_path)
        matching = [
            s
            for app in estate.applications
            for f in app.flows
            for s in f.steps
            if isinstance(s, ConnectorStep)
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].connector_type, "core_banking_platform")

    def test_connector_step_missing_field_is_rejected(self):
        raw = copy.deepcopy(_raw_milestone1())
        flow = raw["applications"][0]["flows"][0]
        flow["steps"].append({"id": "step-bad-connector", "kind": "connector", "name": "X"})
        import json
        import tempfile

        tmp_path = Path(tempfile.mkdtemp()) / "bad_connector_step.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaises(GroundTruthValidationError):
            validate_ground_truth(tmp_path)

    def test_duplicate_connector_step_id_is_caught_by_referential_integrity(self):
        estate = _load_estate()
        app = estate.applications[0]
        flow = app.flows[0]
        existing_id = flow.steps[0].id
        flow.steps.append(
            ConnectorStep(id=existing_id, name="X", description="Y", connector_type="sap_retail_scm")
        )
        errors = validate_estate_object(estate)
        self.assertTrue(any("Duplicate id" in e for e in errors))


def _raw_milestone1() -> dict:
    import json

    return json.loads(MILESTONE1_GROUND_TRUTH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 5. Registration / live-sourced taxonomy / opt-in-only wiring.
# ---------------------------------------------------------------------------


class TestOperatorRegistrationAndTaxonomy(unittest.TestCase):
    def test_operator_is_registered_under_its_transformation_type(self):
        self.assertIn(_TRANSFORMATION_TYPE, OPERATOR_REGISTRY)
        self.assertIsInstance(OPERATOR_REGISTRY[_TRANSFORMATION_TYPE], DomainComponentInjectionOperator)

    def test_issue_type_taxonomy_includes_it_via_the_live_registry_not_a_hand_edit(self):
        """schema.py derives ISSUE_TYPE_TAXONOMY as
        frozenset(OPERATOR_REGISTRY.keys()) | {"other"} -- so registering
        the operator is sufficient for it to flow into the agent's prompt;
        nothing in ark/harness/prompt.py needed a separate, hand-added
        entry for it."""
        self.assertIn(_TRANSFORMATION_TYPE, ISSUE_TYPE_TAXONOMY)
        self.assertEqual(ISSUE_TYPE_TAXONOMY, frozenset(OPERATOR_REGISTRY.keys()) | {"other"})

    def test_new_operator_is_not_folded_into_any_existing_level_0_3_profile(self):
        for operator_tuple in (LEVEL_1_OPERATORS, LEVEL_2_OPERATORS, LEVEL_3_OPERATORS):
            self.assertNotIn(_TRANSFORMATION_TYPE, operator_tuple)
        for profile_name in ("level_1_minor", "level_2_structural", "level_3_legacy"):
            self.assertNotIn(_TRANSFORMATION_TYPE, PROFILES[profile_name].operator_types)

    def test_opt_in_profile_exists_and_is_scoped_to_only_this_operator(self):
        self.assertIn("domain_injection_preview", PROFILES)
        profile = PROFILES["domain_injection_preview"]
        self.assertEqual(profile.operator_types, (_TRANSFORMATION_TYPE,))
        self.assertEqual(profile.level, -1)


# ---------------------------------------------------------------------------
# 6. The operator's injection logic itself.
# ---------------------------------------------------------------------------


class TestDomainComponentInjectionLogic(unittest.TestCase):
    def setUp(self):
        self.operator = OPERATOR_REGISTRY[_TRANSFORMATION_TYPE]
        self.estate = _load_estate()

    def test_no_candidates_without_a_domain(self):
        self.assertEqual(self.operator.find_candidates(self.estate), [])

    def test_candidates_exist_once_a_domain_is_assigned(self):
        for domain in ("finance", "retail"):
            with self.subTest(domain=domain):
                candidates = self.operator.find_candidates(_domain_tagged(self.estate, domain))
                self.assertGreater(len(candidates), 0)

    def test_apply_picks_a_component_from_the_opposite_domain(self):
        finance_estate = _domain_tagged(self.estate, "finance")
        candidates = self.operator.find_candidates(finance_estate)
        rng = random.Random(0)
        new_estate, draft = self.operator.apply(finance_estate, candidates[0], 0.5, rng, mutation_ordinal=0)

        new_step_id = draft.affected_entity_ids[0]
        new_step = _find_step_anywhere(new_estate, new_step_id)
        self.assertIsInstance(new_step, ConnectorStep)

        retail_keys = {c["key"] for c in plausible_components_for("retail")}
        finance_keys = {c["key"] for c in plausible_components_for("finance")}
        self.assertIn(new_step.connector_type, retail_keys)
        self.assertNotIn(new_step.connector_type, finance_keys)

    def test_apply_for_retail_domain_picks_a_finance_component(self):
        retail_estate = _domain_tagged(self.estate, "retail")
        candidates = self.operator.find_candidates(retail_estate)
        rng = random.Random(1)
        new_estate, draft = self.operator.apply(retail_estate, candidates[0], 0.5, rng, mutation_ordinal=0)

        new_step_id = draft.affected_entity_ids[0]
        new_step = _find_step_anywhere(new_estate, new_step_id)
        finance_keys = {c["key"] for c in plausible_components_for("finance")}
        self.assertIn(new_step.connector_type, finance_keys)

    def test_apply_appends_exactly_one_new_step_and_leaves_estate_valid(self):
        estate = _domain_tagged(self.estate, "finance")
        candidates = self.operator.find_candidates(estate)
        rng = random.Random(2)
        target = candidates[0]
        new_estate, draft = self.operator.apply(estate, target, 0.5, rng, mutation_ordinal=3)

        app = next(a for a in new_estate.applications if a.id == target["app_id"])
        flow = next(f for f in app.flows if f.id == target["flow_id"])
        original_app = next(a for a in estate.applications if a.id == target["app_id"])
        original_flow = next(f for f in original_app.flows if f.id == target["flow_id"])
        self.assertEqual(len(flow.steps), len(original_flow.steps) + 1)

        errors = validate_estate_object(new_estate)
        self.assertEqual(errors, [])

    def test_apply_does_not_mutate_its_input_estate(self):
        estate = _domain_tagged(self.estate, "finance")
        estate_copy = copy.deepcopy(estate)
        candidates = self.operator.find_candidates(estate)
        rng = random.Random(3)
        self.operator.apply(estate, candidates[0], 0.5, rng, mutation_ordinal=0)
        self.assertEqual(estate, estate_copy)

    def test_draft_is_never_a_no_op(self):
        estate = _domain_tagged(self.estate, "finance")
        candidates = self.operator.find_candidates(estate)
        rng = random.Random(4)
        _, draft = self.operator.apply(estate, candidates[0], 0.5, rng, mutation_ordinal=0)
        self.assertNotEqual(draft.original_state, draft.transformed_state)
        self.assertEqual(draft.transformation_type, _TRANSFORMATION_TYPE)

    def test_severity_changes_the_injected_steps_description(self):
        estate = _domain_tagged(self.estate, "finance")
        candidates = self.operator.find_candidates(estate)

        descriptions = {}
        for severity in (0.1, 0.5, 0.9):
            rng = random.Random(42)  # same seed -> same component choice across severities
            new_estate, draft = self.operator.apply(estate, candidates[0], severity, rng, mutation_ordinal=0)
            step = _find_step_anywhere(new_estate, draft.affected_entity_ids[0])
            descriptions[severity] = step.description

        self.assertEqual(len(set(descriptions.values())), 3, "each severity band should read differently")


def _find_step_anywhere(estate: GroundTruthEstate, step_id: str):
    for app in estate.applications:
        for flow in app.flows:
            for step in flow.steps:
                if step.id == step_id:
                    return step
    raise KeyError(step_id)


# ---------------------------------------------------------------------------
# 7. Ledger entry shape + issue derivation, via the mutation engine directly
#    (no agent, no experiment batch — see module docstring).
# ---------------------------------------------------------------------------


class TestLedgerEntryShapeAndIssueDerivation(unittest.TestCase):
    def test_engine_run_with_opt_in_profile_produces_one_domain_implausible_record(self):
        baseline = _domain_tagged(_load_estate(), "finance")
        result = run_trajectory(baseline, PROFILES["domain_injection_preview"], seed=1)

        self.assertEqual(len(result.ledger.records), 1)
        record = result.ledger.records[0]
        self.assertEqual(record.transformation_type, _TRANSFORMATION_TYPE)
        self.assertEqual(len(record.affected_entity_ids), 1)
        new_id = record.affected_entity_ids[0]
        self.assertIsNone(record.original_state[new_id])
        self.assertIn("connector_type", record.transformed_state[new_id])
        self.assertIn("kind", record.transformed_state[new_id])
        self.assertEqual(record.transformed_state[new_id]["kind"], "connector")

    def test_engine_run_without_a_domain_realizes_zero_mutations(self):
        """Graceful degradation (ark/mutation/engine.py's own documented
        behavior), not a crash, for a profile whose only operator has no
        eligible candidates."""
        baseline = _load_estate()  # no domain set
        result = run_trajectory(baseline, PROFILES["domain_injection_preview"], seed=1)
        self.assertEqual(result.ledger.records, [])

    def test_reproducible_across_repeated_runs_with_the_same_seed(self):
        baseline = _domain_tagged(_load_estate(), "retail")
        result_1 = run_trajectory(baseline, PROFILES["domain_injection_preview"], seed=7)
        result_2 = run_trajectory(baseline, PROFILES["domain_injection_preview"], seed=7)
        self.assertEqual(result_1.transformed_estate, result_2.transformed_estate)

    def test_derive_issues_produces_exactly_one_domain_implausible_issue(self):
        baseline = _domain_tagged(_load_estate(), "finance")
        result = run_trajectory(baseline, PROFILES["domain_injection_preview"], seed=2)
        issues = derive_issues(result.ledger)

        matching = [i for i in issues if i.issue_type == _TRANSFORMATION_TYPE]
        self.assertEqual(len(matching), 1)
        issue = matching[0]
        self.assertTrue(issue.issue_id.startswith(f"{_TRANSFORMATION_TYPE}:"))
        self.assertEqual(len(issue.affected_entity_ids), 1)
        self.assertIn("connector_type", issue.observable_symptom[issue.affected_entity_ids[0]])


if __name__ == "__main__":
    unittest.main()
