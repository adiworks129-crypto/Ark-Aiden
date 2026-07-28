"""
The mutation engine's top-level entry point: run_trajectory().

    baseline estate (untouched)
            |
            v
     [ pick eligible operator -> pick candidate -> pick severity -> apply ]  x N
            |
            v
    transformed estate  +  MutationLedger

Every step operates on the estate produced by the PREVIOUS step (mutations
compound into a genuine trajectory, not N independent one-off edits), and
every step is validated before being accepted — see validate_estate_object.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ark.core.models import GroundTruthEstate
from ark.core.validate import validate_estate_object
from ark.generator.seeds import make_rng
from ark.mutation.base import clone_estate
from ark.mutation.ledger import MutationLedger, MutationRecord, TransformationResult
from ark.mutation.ledger import LEDGER_SCHEMA_VERSION
from ark.mutation.profiles import MutationProfile
from ark.mutation.registry import OPERATOR_REGISTRY

ENGINE_VERSION = "0.1.0"


class MutationEngineError(Exception):
    """Raised if an operator ever produces a referentially-invalid estate.
    This should be unreachable given how the operators are written — this
    exception is the safety net proving that invariant, not an expected
    code path."""


def run_trajectory(baseline_estate: GroundTruthEstate, profile: MutationProfile, seed: int) -> TransformationResult:
    """Apply `profile` to `baseline_estate` using `seed`, returning the
    untouched baseline, the transformed estate, and the full mutation
    ledger. Deterministic: the same (baseline_estate, profile, seed)
    always produces the same transformed estate and the same ledger
    (except each record's wall-clock `timestamp`, which is informational
    only — see ledger.py).

    If the estate runs out of eligible candidates for every operator the
    profile allows before reaching profile.num_mutations, the trajectory
    stops early rather than failing — the ledger's records list is simply
    shorter than num_mutations. This is intentional graceful degradation:
    it means "this estate wasn't big/rich enough to sustain the requested
    profile," which is itself useful information, not a crash.
    """
    rng = make_rng(seed)
    current_estate = clone_estate(baseline_estate)
    records: list[MutationRecord] = []

    for i in range(profile.num_mutations):
        eligible: list[tuple[str, list[dict]]] = []
        for op_type in profile.operator_types:
            operator = OPERATOR_REGISTRY[op_type]
            candidates = operator.find_candidates(current_estate)
            if candidates:
                eligible.append((op_type, candidates))

        if not eligible:
            break  # graceful early stop; see docstring

        op_type, candidates = rng.choice(eligible)
        operator = OPERATOR_REGISTRY[op_type]
        target = rng.choice(candidates)
        severity = (
            rng.uniform(*profile.severity_range) if profile.severity_range[1] > 0.0 else 0.0
        )

        new_estate, draft = operator.apply(current_estate, target, severity, rng, mutation_ordinal=i)

        errors = validate_estate_object(new_estate)
        if errors:
            raise MutationEngineError(
                f"Operator '{operator.transformation_type}' (step {i}) produced an invalid "
                f"estate:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        # Safety net: a ledger record that claims a mutation happened but
        # whose before/after states are identical would be lying about
        # what changed — undermining the whole "ledger is the answer key"
        # premise. This should be unreachable given how operators are
        # written (see the naming_drift no-op guard for the bug this
        # caught during development); enforced here as a hard invariant
        # covering every current and future operator, not just one.
        if draft.original_state == draft.transformed_state:
            raise MutationEngineError(
                f"Operator '{operator.transformation_type}' (step {i}) produced a no-op "
                f"mutation record (original_state == transformed_state) for target {target}."
            )

        records.append(
            MutationRecord(
                mutation_id=f"{profile.name}-seed{seed}-{i:03d}",
                transformation_type=draft.transformation_type,
                affected_entity_ids=draft.affected_entity_ids,
                original_state=draft.original_state,
                transformed_state=draft.transformed_state,
                severity=severity,
                rationale=draft.rationale,
                sequence_index=i,
                timestamp=datetime.now(timezone.utc).isoformat(),
                seed=seed,
                record_schema_version=LEDGER_SCHEMA_VERSION,
            )
        )
        current_estate = new_estate

    ledger = MutationLedger(
        baseline_estate_id=baseline_estate.estate_id,
        baseline_schema_version=baseline_estate.schema_version,
        trajectory_seed=seed,
        profile_name=profile.name,
        engine_version=ENGINE_VERSION,
        ledger_schema_version=LEDGER_SCHEMA_VERSION,
        records=records,
    )
    return TransformationResult(baseline_estate=baseline_estate, transformed_estate=current_estate, ledger=ledger)
