"""
Regenerates the committed Milestone 4 example transformation trajectory.

Baseline: examples/milestone1/ground_truth.json (the hand-authored Order
Management estate). Applies each of the three non-trivial mutation
profiles (Level 1-3) with a fixed seed, so re-running this script produces
byte-identical output (except each ledger record's wall-clock timestamp).
"""

from __future__ import annotations

import json
import os

from ark.core.serialize import estate_to_json
from ark.core.validate import validate_ground_truth
from ark.mutation.engine import run_trajectory
from ark.mutation.ledger import ledger_to_json
from ark.mutation.profiles import PROFILES

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(HERE, "..", "milestone1", "ground_truth.json")
SEED = 1


def main() -> None:
    baseline = validate_ground_truth(BASELINE_PATH)

    for level_name in ["level_1_minor", "level_2_structural", "level_3_legacy"]:
        profile = PROFILES[level_name]
        result = run_trajectory(baseline, profile, seed=SEED)

        estate_path = os.path.join(HERE, f"transformed_{level_name}.json")
        with open(estate_path, "w", encoding="utf-8") as f:
            f.write(estate_to_json(result.transformed_estate))

        ledger_path = os.path.join(HERE, f"ledger_{level_name}.json")
        with open(ledger_path, "w", encoding="utf-8") as f:
            f.write(ledger_to_json(result.ledger))

        print(
            f"{level_name}: requested {profile.num_mutations}, "
            f"applied {len(result.ledger.records)} mutations "
            f"-> {len(result.transformed_estate.applications)} applications, "
            f"{sum(len(a.flows) for a in result.transformed_estate.applications)} flows"
        )


if __name__ == "__main__":
    main()
