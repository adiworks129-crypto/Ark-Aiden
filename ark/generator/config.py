"""
GeneratorConfig — every parameter that controls estate generation, and
nothing else.

Every generated estate must be reproducible from exactly: seed +
GENERATOR_VERSION (generator.py) + core SCHEMA_VERSION (ark.core.models) +
this config (recorded together in a GenerationManifest, see generator.py).
That guarantee only holds if every knob the generator actually uses lives
here — not as a hidden default buried somewhere in topology.py or
generator.py — so if you add a new generation behavior, add its parameter
here first.
"""

from __future__ import annotations

from dataclasses import dataclass

# Milestone 3 implements exactly one value for each of these — see the
# corresponding "not yet supported" notes in topology.py / vocabulary.py.
# Rejecting anything else explicitly (rather than silently ignoring an
# unsupported value) is deliberate: a config that claims to want
# "mesh" topology but silently gets "layered" instead would quietly
# break the seed+config -> estate reproducibility guarantee above.
SUPPORTED_TOPOLOGY_STYLES = {"layered"}
SUPPORTED_NAMING_STYLES = {"kebab-case"}
SUPPORTED_VOCABULARY_DOMAINS = {"enterprise_default"}

# Feature 2 (domain-conditioned component injection, "organized
# randomness"): the estate's real-world business domain, assigned at
# generation time so ark.mutation.operators.DomainComponentInjectionOperator
# has something to check plausibility against. None (the default) means "no
# domain assigned" -- that operator simply finds no candidates for such an
# estate (see GroundTruthEstate.domain's own docstring in ark/core/models.py).
# Deliberately just two values for now, matching
# ark/core/validate.py's `_VALID_DOMAINS` and
# ark/generator/domain_plausibility.json's top-level domain keys -- kept as
# three independent, self-contained constants (not one shared import)
# rather than inverting ark.core's "never imports ark.generator" boundary;
# cross-checked by a dedicated test instead.
SUPPORTED_DOMAINS = {"finance", "retail"}


class GeneratorConfigError(ValueError):
    """Raised when a GeneratorConfig has an invalid or unsupported combination of values."""


@dataclass
class GeneratorConfig:
    seed: int
    """The single source of randomness for this generation run. Same seed
    + same config -> byte-for-byte identical estate, always."""

    num_experience_apis: int = 1
    num_process_apis: int = 1
    num_system_apis: int = 2
    """How many applications (each with exactly one API) to generate per
    layer. Any of these may be 0 (e.g. a system-only, backend estate) as
    long as at least one is > 0."""

    dependency_density: float = 0.6
    """0.0-1.0. Controls fan-out *within the next layer down only* — e.g.
    how many of the available system APIs a given process API calls. This
    can never create a cross-layer or same-layer edge (see topology.py),
    so it cannot produce an "everything calls everything" mesh regardless
    of its value; it only controls how much fan-out/fan-in exists within
    the fixed layered shape."""

    shared_component_frequency: float = 0.5
    """0.0-1.0. When an app has a secondary (scheduled) flow, the
    probability that flow reuses the entry flow's shared sub-flow (real
    in-app reuse) rather than getting its own independent one (an
    organically-arising near-duplicate — see generator.py)."""

    scheduled_job_ratio: float = 0.3
    """0.0-1.0. Probability that a process- or system-layer app also gets
    a secondary scheduler-triggered flow, in addition to its HTTP entry
    flow. Experience-layer apps never get one in Milestone 3 (out of
    scope — see the Milestone 3 note in Ark_Architecture_and_Plan.md)."""

    topology_style: str = "layered"
    naming_style: str = "kebab-case"
    vocabulary_domain: str = "enterprise_default"
    """Extension points for future generation styles. Only one value each
    is implemented right now; see SUPPORTED_* above."""

    estate_id_prefix: str = "generated"
    """Used to build a deterministic estate_id: f"{prefix}-seed{seed}".
    Customize if you need distinguishable ids across configs sharing a seed."""

    domain: str | None = None
    """Feature 2: the generated estate's real-world business domain
    ("finance" or "retail"), or None (the default) for "no domain
    assigned." See SUPPORTED_DOMAINS above and
    GroundTruthEstate.domain's docstring in ark/core/models.py."""

    def __post_init__(self) -> None:
        errors: list[str] = []

        for field_name in ("num_experience_apis", "num_process_apis", "num_system_apis"):
            value = getattr(self, field_name)
            if value < 0:
                errors.append(f"{field_name} must be >= 0, got {value}.")

        if self.num_experience_apis + self.num_process_apis + self.num_system_apis <= 0:
            errors.append(
                "At least one of num_experience_apis / num_process_apis / "
                "num_system_apis must be > 0."
            )

        for field_name in ("dependency_density", "shared_component_frequency", "scheduled_job_ratio"):
            value = getattr(self, field_name)
            if not (0.0 <= value <= 1.0):
                errors.append(f"{field_name} must be between 0.0 and 1.0, got {value}.")

        if self.topology_style not in SUPPORTED_TOPOLOGY_STYLES:
            errors.append(
                f"Unsupported topology_style '{self.topology_style}'. "
                f"Supported: {sorted(SUPPORTED_TOPOLOGY_STYLES)}."
            )
        if self.naming_style not in SUPPORTED_NAMING_STYLES:
            errors.append(
                f"Unsupported naming_style '{self.naming_style}'. "
                f"Supported: {sorted(SUPPORTED_NAMING_STYLES)}."
            )
        if self.vocabulary_domain not in SUPPORTED_VOCABULARY_DOMAINS:
            errors.append(
                f"Unsupported vocabulary_domain '{self.vocabulary_domain}'. "
                f"Supported: {sorted(SUPPORTED_VOCABULARY_DOMAINS)}."
            )

        if self.domain is not None and self.domain not in SUPPORTED_DOMAINS:
            errors.append(
                f"Unsupported domain '{self.domain}'. "
                f"Supported: {sorted(SUPPORTED_DOMAINS)} (or None for no domain assigned)."
            )

        if errors:
            raise GeneratorConfigError(
                "Invalid GeneratorConfig:\n" + "\n".join(f"  - {e}" for e in errors)
            )
