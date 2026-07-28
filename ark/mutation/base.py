"""
Shared building blocks for every mutation operator.

Design principles every operator must follow (enforced here, not just by
convention):

1. Never mutate the estate you're given. clone_estate() deep-copies it;
   every operator's apply() must call this first and modify only the copy.
2. Candidates are always ID-based descriptors (plain dicts of ids/strings),
   never live object references into the estate you were handed. An
   operator's find_candidates() runs against the estate BEFORE cloning,
   but apply() must re-resolve those ids inside the freshly-cloned copy —
   never reuse an object found before the clone. The lookup helpers below
   exist specifically to make that easy and hard to get wrong.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ark.core.models import Application, Flow, GroundTruthEstate


def clone_estate(estate: GroundTruthEstate) -> GroundTruthEstate:
    """Deep-copy an estate. Every dataclass involved is plain
    dataclasses/lists/primitives, so a stdlib deepcopy is exact and safe."""
    return copy.deepcopy(estate)


def find_application(estate: GroundTruthEstate, app_id: str) -> Application:
    for app in estate.applications:
        if app.id == app_id:
            return app
    raise KeyError(f"No Application with id '{app_id}' in this estate.")


def find_flow(app: Application, flow_id: str) -> Flow:
    for flow in app.flows:
        if flow.id == flow_id:
            return flow
    raise KeyError(f"No Flow with id '{flow_id}' in Application '{app.id}'.")


def find_step(flow: Flow, step_id: str) -> Any:
    for step in flow.steps:
        if step.id == step_id:
            return step
    raise KeyError(f"No Step with id '{step_id}' in Flow '{flow.id}'.")


@dataclass
class MutationRecordDraft:
    """Everything a MutationOperator reports about one application of
    itself, short of the fields only the engine can fill in (mutation_id,
    sequence_index, timestamp, seed) — see ledger.py for the full record.
    """

    transformation_type: str
    affected_entity_ids: list[str]
    original_state: dict[str, dict | None]
    transformed_state: dict[str, dict | None]
    rationale: str


class MutationOperator(ABC):
    """Base class every mutation operator implements.

    Operators are independent and composable: each only needs to know how
    to find its own candidates and apply itself, given whatever estate
    state exists *right now* (which may already include entities created
    by earlier operators in the same trajectory — see engine.py). An
    operator must never assume it's the only one that will ever run, or
    that the estate still looks like the original baseline.
    """

    transformation_type: str

    @abstractmethod
    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        """Return a stable-ordered list of candidate targets (plain dicts
        of ids/strings, operator-specific shape) this operator could be
        applied to right now. Empty list if this operator's preconditions
        aren't met anywhere in the estate — never raise here."""
        raise NotImplementedError

    @abstractmethod
    def apply(
        self,
        estate: GroundTruthEstate,
        target: dict,
        severity: float,
        rng,
        mutation_ordinal: int,
    ) -> tuple[GroundTruthEstate, MutationRecordDraft]:
        """Apply this operator to `target` (one item previously returned
        by find_candidates) within a *clone* of estate. `severity` is
        0.0-1.0 and must meaningfully change what this operator does, not
        just be recorded cosmetically. `mutation_ordinal` is this
        operator's position in the overall trajectory, useful for
        constructing guaranteed-unique ids for any newly created entities.
        Must never mutate `estate` itself."""
        raise NotImplementedError
