"""
Regenerates the committed Milestone 3 example estates.

These files are committed as examples of what the generator produces, not
as fixtures a test compares byte-for-byte against (Milestone 3's tests
validate structural/statistical properties instead — see
tests/test_milestone3.py — since golden-file comparison makes less sense
for a generator whose whole point is that changing the seed or config
should change the output). Run this script any time you want to regenerate
them (they should come out byte-identical, since generation is deterministic).
"""

from __future__ import annotations

import json
import os

from ark.core.serialize import estate_to_json
from ark.generator.config import GeneratorConfig
from ark.generator.generator import generate_estate

HERE = os.path.dirname(os.path.abspath(__file__))

EXAMPLES = {
    "small_seed1.json": GeneratorConfig(
        seed=1, num_experience_apis=1, num_process_apis=1, num_system_apis=2
    ),
    "small_seed2.json": GeneratorConfig(
        seed=2, num_experience_apis=1, num_process_apis=1, num_system_apis=2
    ),
    "medium_seed1_shared_dependency.json": GeneratorConfig(
        seed=1,
        num_experience_apis=1,
        num_process_apis=2,
        num_system_apis=3,
        dependency_density=0.6,
        scheduled_job_ratio=0.6,
    ),
    "large_seed42.json": GeneratorConfig(
        seed=42,
        num_experience_apis=2,
        num_process_apis=3,
        num_system_apis=4,
        dependency_density=0.5,
        scheduled_job_ratio=0.4,
        shared_component_frequency=0.5,
    ),
}


def main() -> None:
    for filename, config in EXAMPLES.items():
        result = generate_estate(config)
        path = os.path.join(HERE, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(estate_to_json(result.estate))
        manifest_path = os.path.join(HERE, filename.replace(".json", ".manifest.json"))
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "seed": result.manifest.seed,
                    "generator_version": result.manifest.generator_version,
                    "schema_version": result.manifest.schema_version,
                    "config": result.manifest.config,
                },
                f,
                indent=2,
            )
        print(f"wrote {filename} ({len(result.estate.applications)} applications)")


if __name__ == "__main__":
    main()
