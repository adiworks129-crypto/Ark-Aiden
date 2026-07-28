"""
TrajectorySpec — one requested (baseline, profile, seed) combination for
the experiment runner to produce and evaluate.

Deliberately a thin, declarative description, not a running computation
-- building a list of these is how a caller describes an entire
experiment batch (e.g. "every profile, four seeds each") before anything
actually runs, matching the same "config describes intent, a separate
function executes it" split `ark.generator.config.GeneratorConfig` and
`ark.mutation.profiles.MutationProfile` already use.
"""

from __future__ import annotations

from dataclasses import dataclass

from ark.generator.config import GeneratorConfig


@dataclass
class TrajectorySpec:
    label: str
    """A human-readable, unique-within-the-batch identifier, used as the
    EvaluationReport's trajectory_id and (if persisting) its output
    filename -- e.g. "level_2_structural-seed3\"."""
    profile_name: str
    """Must be a key in ark.mutation.profiles.PROFILES."""
    seed: int

    baseline_estate_path: str | None = None
    """Path to a hand-authored ground-truth JSON file (e.g. Milestone 1's
    examples/milestone1/ground_truth.json), loaded via
    ark.core.validate.validate_ground_truth(). Mutually exclusive with
    generator_config -- exactly one must be set."""
    generator_config: GeneratorConfig | None = None
    """A procedural-generation recipe (Milestone 3) to build the baseline
    estate from instead of a fixed file. Mutually exclusive with
    baseline_estate_path."""

    def __post_init__(self) -> None:
        has_path = self.baseline_estate_path is not None
        has_config = self.generator_config is not None
        if has_path == has_config:
            raise ValueError(
                f"TrajectorySpec {self.label!r} must set exactly one of "
                "baseline_estate_path or generator_config "
                f"(got baseline_estate_path={self.baseline_estate_path!r}, "
                f"generator_config={self.generator_config!r})."
            )
