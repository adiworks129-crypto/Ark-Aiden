"""
Session B prerequisite: "a couple of saved estates on disk to build and
test against (a Level 3 test estate or two is enough -- no new trajectory
batches needed [for Session B itself])."

This script is the one place that actually runs those trajectories --
Session B's own code never runs a trajectory; it only reads what this
script (or `ark.experiment.run_experiment(..., save_estates=True)` more
generally) already wrote to disk. Uses `ScriptedAgentClient` (offline, no
network, no API key), the same reference double every other example/test
in this repo already uses for exactly this reason.

Run: PYTHONPATH=. python3 examples/estate_browser_demo/generate_demo_estates.py
"""

from __future__ import annotations

from ark.experiment.runner import run_experiment
from ark.experiment.spec import TrajectorySpec
from ark.generator.config import GeneratorConfig
from ark.harness.scripted_client import ScriptedAgentClient

_FIXED_AGENT_OUTPUT = {"findings": []}


def build_specs() -> list[TrajectorySpec]:
    return [
        TrajectorySpec(
            label="demo-level_3_legacy-seed1",
            profile_name="level_3_legacy",
            seed=1,
            generator_config=GeneratorConfig(seed=1),
        ),
        TrajectorySpec(
            label="demo-level_3_legacy-seed2",
            profile_name="level_3_legacy",
            seed=2,
            generator_config=GeneratorConfig(seed=2, domain="finance"),
        ),
    ]


def main() -> None:
    agent_client = ScriptedAgentClient.fixed(_FIXED_AGENT_OUTPUT)
    specs = build_specs()

    output_dir = "examples/estate_browser_demo/run_output"
    result = run_experiment(specs, agent_client, output_dir=output_dir, save_estates=True)

    print(f"Ran {len(result.reports)} trajectories.")
    # Session D: print this unambiguously, every time -- the whole point is
    # that a script's chosen output_dir shouldn't be something you have to
    # go re-read the source to find. Point the Project Browser's estates
    # directory field (or let it auto-discover, if this lives under
    # examples/) at exactly this path.
    print(f"Estates saved to: {output_dir}/estates")
    print(f"Reports + analysis.json written to: {output_dir}")
    for spec in specs:
        print(f"  estates/{spec.label}/  <->  reports/{spec.label}.json")


if __name__ == "__main__":
    main()
