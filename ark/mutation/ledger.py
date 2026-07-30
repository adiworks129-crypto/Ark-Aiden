"""
The mutation ledger — the authoritative answer key describing exactly how
a transformed estate differs from its baseline.

This is not a debug log. Every record here is meant to be consumed
programmatically by a future evaluator (Milestone 6): affected_entity_ids
cross-references directly against the Milestone 2 rendering manifest's
entity_index, so a scorer can go artifact file -> entity id -> "was this
mutated, and how" -> "did the agent correctly say so."
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field

from ark.core.models import GroundTruthEstate

LEDGER_SCHEMA_VERSION = "0.1.0"


@dataclass
class MutationRecord:
    mutation_id: str
    """Deterministic, not random: f'{profile_name}-seed{seed}-{sequence_index:03d}'."""
    transformation_type: str
    affected_entity_ids: list[str]
    original_state: dict
    """entity_id -> dict of only the fields that changed (or None if the
    entity didn't exist before this mutation)."""
    transformed_state: dict
    """entity_id -> dict of only the fields that changed (or the full
    entity dict, for newly created entities)."""
    severity: float
    rationale: str
    sequence_index: int
    timestamp: str
    """Wall-clock ISO8601 — informational only, NOT part of the
    reproducibility guarantee (seed + sequence_index + profile is)."""
    seed: int
    record_schema_version: str = LEDGER_SCHEMA_VERSION


@dataclass
class MutationLedger:
    baseline_estate_id: str
    baseline_schema_version: str
    trajectory_seed: int
    profile_name: str
    engine_version: str
    ledger_schema_version: str
    records: list[MutationRecord] = field(default_factory=list)


@dataclass
class TransformationResult:
    baseline_estate: GroundTruthEstate
    """The untouched GroundTruthEstate this trajectory started from."""
    transformed_estate: GroundTruthEstate
    ledger: MutationLedger


def ledger_to_dict(ledger: MutationLedger) -> dict:
    return dataclasses.asdict(ledger)


def ledger_to_json(ledger: MutationLedger, indent: int | None = 2) -> str:
    return json.dumps(ledger_to_dict(ledger), indent=indent)


def ledger_from_dict(data: dict) -> MutationLedger:
    """Reconstruct a MutationLedger from a dict produced by
    ledger_to_dict()/json.loads(ledger_to_json(...)) -- the reciprocal of
    ledger_to_dict(), added for the estate persistence layer
    (ark.generator.persistence), which needs to reload a previously-saved
    trajectory's ledger without re-running any mutation.

    Same "deliberately lightweight" contract ark.evaluator.report's own
    *_from_dict() helpers already use: assumes the input has exactly the
    shape ledger_to_dict() produces, and lets plain dataclass construction
    raise a normal TypeError on anything missing or extra. No schema
    migration across ledger_schema_version/record_schema_version changes."""
    restored = dict(data)
    restored["records"] = [MutationRecord(**record) for record in data.get("records", [])]
    return MutationLedger(**restored)


def ledger_from_json(text: str) -> MutationLedger:
    return ledger_from_dict(json.loads(text))
