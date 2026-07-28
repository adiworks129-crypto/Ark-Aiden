"""
Mutation difficulty profiles — controlled transformation trajectories, not
arbitrary mutation counts.

Each level's operator set is a strict superset of the level below it
(additive nesting): Level 2 doesn't replace Level 1's issue types, it adds
structural ones on top, and Level 3 adds legacy/schema issues on top of
that. This matches "progressively more challenging," not "different kinds
of challenging at each level."
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MutationProfile:
    name: str
    level: int
    """0-3 for the official progression below. A profile outside that
    progression (see DOMAIN_INJECTION_PREVIEW_OPERATORS' profile) uses the
    sentinel -1 here instead of a misleading 0-3 value -- level is
    descriptive provenance metadata only (never used to compute anything;
    see ComplexityProfile.profile_name's own note in
    ark/evaluator/complexity.py), so this never changes any scoring
    behavior, only what a human reading this field sees."""
    operator_types: tuple[str, ...]
    num_mutations: int
    severity_range: tuple[float, float]
    description: str


LEVEL_1_OPERATORS = ("naming_drift", "documentation_decay")
LEVEL_2_OPERATORS = LEVEL_1_OPERATORS + ("duplicate_processing", "dependency_change")
LEVEL_3_OPERATORS = LEVEL_2_OPERATORS + ("legacy_version_introduction", "schema_inconsistency")

# Feature 2 (domain-conditioned component injection, "organized
# randomness") — operator #7, deliberately NOT added to LEVEL_1/2/3_OPERATORS
# above. Folding it in would silently change what every existing Level 1-3
# profile does, making old experiment batches run under those profile
# names no longer comparable to new ones run under the same names. Kept as
# its own single-operator tuple, only ever used by its own opt-in profile
# below.
DOMAIN_INJECTION_OPERATORS = ("domain_implausible_component",)

PROFILES: dict[str, MutationProfile] = {
    "level_0_clean": MutationProfile(
        name="level_0_clean",
        level=0,
        operator_types=(),
        num_mutations=0,
        severity_range=(0.0, 0.0),
        description="The untouched baseline estate. No transformations applied.",
    ),
    "level_1_minor": MutationProfile(
        name="level_1_minor",
        level=1,
        operator_types=LEVEL_1_OPERATORS,
        num_mutations=3,
        severity_range=(0.1, 0.4),
        description="Minor inconsistencies: naming drift, incomplete documentation.",
    ),
    "level_2_structural": MutationProfile(
        name="level_2_structural",
        level=2,
        operator_types=LEVEL_2_OPERATORS,
        num_mutations=6,
        severity_range=(0.3, 0.6),
        description="Structural complexity: adds duplicate flows and dependency changes on top of Level 1's issues.",
    ),
    "level_3_legacy": MutationProfile(
        name="level_3_legacy",
        level=3,
        operator_types=LEVEL_3_OPERATORS,
        num_mutations=10,
        severity_range=(0.5, 0.9),
        description="Legacy enterprise complexity: adds version conflicts and schema inconsistency on top of Level 2's issues.",
    ),
    "domain_injection_preview": MutationProfile(
        name="domain_injection_preview",
        level=-1,
        operator_types=DOMAIN_INJECTION_OPERATORS,
        num_mutations=1,
        severity_range=(0.2, 0.8),
        description=(
            "Feature 2 opt-in preview (not part of the Level 0-3 progression): injects "
            "exactly one domain-implausible component. Requires the baseline estate to "
            "have GroundTruthEstate.domain set to 'finance' or 'retail' -- otherwise this "
            "operator finds no candidates and the trajectory realizes zero mutations "
            "(graceful degradation, same as any other profile run against too small an "
            "estate; see ark/mutation/engine.py)."
        ),
    ),
}
