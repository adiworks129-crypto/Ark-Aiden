"""
Serialization for Ark ground-truth objects — the inverse of validate.py's
parsing.

This exists so anything that constructs a GroundTruthEstate directly in
memory (the generator, as of Milestone 3) can write it back out as the
same JSON shape a hand-authored ground-truth file uses, and be checked
through the exact same validate_ground_truth() path real files go
through — not a shortcut in-memory check that could quietly diverge from
what the validator actually enforces.

This module does not change the ground-truth schema or validation rules
in any way; it only converts already-valid dataclass instances to plain
dicts/JSON.
"""

from __future__ import annotations

import dataclasses
import json

from ark.core.models import GroundTruthEstate


def estate_to_dict(estate: GroundTruthEstate) -> dict:
    """Convert a GroundTruthEstate (and everything nested inside it) into
    plain dicts/lists, matching the JSON shape validate.py's _parse_*
    functions expect."""
    return dataclasses.asdict(estate)


def estate_to_json(estate: GroundTruthEstate, indent: int | None = 2) -> str:
    return json.dumps(estate_to_dict(estate), indent=indent)
