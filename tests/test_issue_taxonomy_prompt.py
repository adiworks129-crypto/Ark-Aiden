"""
Session E: "Issue Taxonomy Descriptions in Agent Prompt."

Root cause this session addresses: domain_injection_preview trajectories
were scoring category_f1 = 0.0 because the agent had no way to know
"domain_implausible_component" was a distinct, nameable category to reach
for -- the prompt only ever listed bare type names, no descriptions. This
file tests the fix at all three levels the session touched: each
operator's own new `.description` (ark.mutation.base/operators), the
live-sourced mapping built from it (ark.evaluator.schema.ISSUE_TYPE_DESCRIPTIONS),
and the actual rendered prompt text (ark.harness.prompt.build_agent_prompt).

Explicitly NOT tested here (out of scope for this session, per its own
"DO NOT TOUCH"/"out of scope" lists): whether F1 actually improves on a
real agent re-run, and anything about exposing the estate's domain to the
agent.
"""

from __future__ import annotations

import inspect
import unittest

from ark.evaluator.schema import ISSUE_TYPE_DESCRIPTIONS, ISSUE_TYPE_TAXONOMY
from ark.harness.prompt import build_agent_prompt
from ark.mutation.base import MutationOperator
from ark.mutation.registry import OPERATOR_REGISTRY

SAMPLE_ARTIFACTS = {"App/src/main/mule/App.xml": "<flow name='x'/>"}


class TestOperatorDescriptions(unittest.TestCase):
    """The source of truth: every real, registered operator must carry
    its own non-empty description -- checked directly against the live
    registry, not a hand-copied list, so a future 8th operator is caught
    by this same test with zero changes needed here."""

    def test_every_registered_operator_has_a_non_empty_description(self):
        for transformation_type, operator in OPERATOR_REGISTRY.items():
            with self.subTest(transformation_type=transformation_type):
                description = getattr(operator, "description", None)
                self.assertIsNotNone(
                    description, f"{type(operator).__name__} ('{transformation_type}') has no description at all"
                )
                self.assertTrue(
                    description.strip(),
                    f"{type(operator).__name__} ('{transformation_type}') has a blank description",
                )

    def test_an_operator_that_forgets_to_set_a_description_fails_loudly_not_silently(self):
        """Demonstrates the actual enforcement mechanism (a bare
        `description: str` annotation with no default on MutationOperator,
        same pattern transformation_type already used): a concrete
        operator that never assigns `description` raises AttributeError
        the instant anything reads it, rather than silently returning ''
        or None. This is exactly the "fails a test loudly" guarantee the
        session asked for -- proven here against a deliberately incomplete
        fake operator, not just asserted in prose."""

        class _FakeOperatorMissingDescription(MutationOperator):
            transformation_type = "fake_missing_description"
            # description deliberately not set.

            def find_candidates(self, estate):
                return []

            def apply(self, estate, target, severity, rng, mutation_ordinal):
                raise NotImplementedError

        fake = _FakeOperatorMissingDescription()
        with self.assertRaises(AttributeError):
            _ = fake.description


class TestIssueTypeDescriptions(unittest.TestCase):
    """ark.evaluator.schema.ISSUE_TYPE_DESCRIPTIONS -- the live-sourced
    mapping build_agent_prompt() actually reads from."""

    def test_every_taxonomy_member_has_a_non_empty_description(self):
        for issue_type in ISSUE_TYPE_TAXONOMY:
            with self.subTest(issue_type=issue_type):
                self.assertIn(issue_type, ISSUE_TYPE_DESCRIPTIONS)
                self.assertTrue(ISSUE_TYPE_DESCRIPTIONS[issue_type].strip())

    def test_no_stray_description_entries_outside_the_taxonomy(self):
        """Drift-proofing in both directions: not just "every taxonomy
        member has a description" but "every description key really is a
        taxonomy member" -- catches a stale entry left behind if an
        operator were ever removed from the registry."""
        self.assertEqual(set(ISSUE_TYPE_DESCRIPTIONS.keys()), set(ISSUE_TYPE_TAXONOMY))

    def test_other_has_its_own_literal_description(self):
        """'other' isn't a real operator, so it can't be sourced from
        OPERATOR_REGISTRY -- confirms it still got a real, non-empty,
        hand-written description rather than being silently skipped."""
        self.assertIn("other", ISSUE_TYPE_DESCRIPTIONS)
        self.assertTrue(ISSUE_TYPE_DESCRIPTIONS["other"].strip())

    def test_descriptions_actually_match_each_operators_own_description_verbatim(self):
        """Confirms this is genuinely sourced live, not a parallel
        hand-copied list that happens to agree today."""
        for transformation_type, operator in OPERATOR_REGISTRY.items():
            with self.subTest(transformation_type=transformation_type):
                self.assertEqual(ISSUE_TYPE_DESCRIPTIONS[transformation_type], operator.description)


class TestPromptRendersDescriptions(unittest.TestCase):
    """ark.harness.prompt.build_agent_prompt() -- confirms the descriptions
    don't just exist somewhere, they're actually formatted correctly into
    the real prompt text an agent would receive."""

    def test_every_issue_type_appears_as_a_correctly_formatted_taxonomy_line(self):
        prompt = build_agent_prompt(SAMPLE_ARTIFACTS)
        for issue_type in ISSUE_TYPE_TAXONOMY:
            expected_line = f"- {issue_type}: {ISSUE_TYPE_DESCRIPTIONS[issue_type]}"
            self.assertIn(
                expected_line, prompt, f"expected a correctly formatted line for '{issue_type}' in the prompt"
            )

    def test_domain_implausible_component_specifically_gets_a_real_description_not_a_bare_name(self):
        """The exact symptom this session exists to fix: previously the
        prompt only ever contained the bare string
        'domain_implausible_component' with nothing explaining what it
        means. Confirms that's no longer true."""
        prompt = build_agent_prompt(SAMPLE_ARTIFACTS)
        self.assertIn(
            "- domain_implausible_component: A component that is realistic and well-formed on its own",
            prompt,
        )

    def test_taxonomy_section_appears_before_the_files_section(self):
        prompt = build_agent_prompt(SAMPLE_ARTIFACTS)
        self.assertLess(prompt.index("# Issue type taxonomy"), prompt.index("# Files"))

    def test_build_agent_prompt_signature_still_takes_only_artifacts(self):
        """Locks in scope item 3: no profile_name or any other
        profile-specific parameter was added -- the fix is richer content
        in the one fixed prompt, not per-profile prompt variation."""
        params = list(inspect.signature(build_agent_prompt).parameters.keys())
        self.assertEqual(params, ["artifacts"])

    def test_prompt_still_contains_every_artifact_path_and_content(self):
        """Sanity check that this session's rewrite of the instructions
        template didn't disturb the existing, already-tested artifact
        rendering (tests/test_milestone7.py's TestPromptConstruction
        covers this in more depth; this is a quick smoke check specific
        to this file's own fixture)."""
        prompt = build_agent_prompt(SAMPLE_ARTIFACTS)
        for path, content in SAMPLE_ARTIFACTS.items():
            self.assertIn(path, prompt)
            self.assertIn(content, prompt)


if __name__ == "__main__":
    unittest.main()
