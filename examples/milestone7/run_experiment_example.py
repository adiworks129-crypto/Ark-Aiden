"""
Milestone 7 worked example: running Ark end-to-end with an agent that
talks through the real harness round trip (prompt -> response text ->
parsed JSON), via `ark.experiment.run_experiment()`.

Uses `HeuristicNamingAgentClient` (heuristic_agent_client.py, in this
same directory) -- a fully offline, deterministic `AgentClient` that
looks only at rendered artifact text (never ground truth, the ledger, or
the manifest) and flags a couple of real, visible naming irregularities.
It is NOT a good agent (it can only ever catch naming_drift, and even
then only when a drift style happens to produce a textually obvious
irregularity) -- that's the honest, expected result of a simple
heuristic, and precisely the kind of partial-credit outcome Ark's
metrics (precision/recall/F1, never one pass/fail number) exist to
characterize.

To run this same script against a REAL LLM agent instead, swap the
`agent_client` construction below for:

    from integrations.anthropic_agent_client import AnthropicAgentClient
    agent_client = AnthropicAgentClient()   # reads ANTHROPIC_API_KEY from the environment

No other line of this script -- or of ark/experiment/runner.py -- needs
to change. That interchangeability (real agent vs. scripted/heuristic
double, both satisfying the same `ark.harness.contract.AgentClient`
interface) is Milestone 7's actual point.

Run: PYTHONPATH=. python3 examples/milestone7/run_experiment_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heuristic_agent_client import HeuristicNamingAgentClient  # noqa: E402

from ark.experiment.runner import run_experiment  # noqa: E402
from ark.experiment.spec import TrajectorySpec  # noqa: E402
from ark.generator.config import GeneratorConfig  # noqa: E402
from ark.mutation.profiles import PROFILES  # noqa: E402

MILESTONE1_GROUND_TRUTH = "examples/milestone1/ground_truth.json"


def build_specs() -> list[TrajectorySpec]:
    specs = []
    # Hand-authored baseline (Milestone 1), across all four profiles.
    for profile_name in PROFILES:
        specs.append(
            TrajectorySpec(
                label=f"m1-{profile_name}-seed1",
                profile_name=profile_name,
                seed=1,
                baseline_estate_path=MILESTONE1_GROUND_TRUTH,
            )
        )
    # Generator-produced estates (Milestone 3), a couple of seeds each,
    # demonstrating the other supported estate source in the same batch.
    for seed in (1, 2):
        specs.append(
            TrajectorySpec(
                label=f"generated-level_2_structural-seed{seed}",
                profile_name="level_2_structural",
                seed=seed,
                generator_config=GeneratorConfig(seed=seed),
            )
        )
    return specs


def main() -> None:
    agent_client = HeuristicNamingAgentClient()
    specs = build_specs()

    result = run_experiment(specs, agent_client, output_dir="examples/milestone7/run_output")

    print(f"Ran {len(result.reports)} trajectories.")
    print(f"Reports + analysis.json written to: {result.output_dir}")
    print()
    print("Per-trajectory category F1 (None = no real issues to score, e.g. the clean profile):")
    for report in result.reports:
        print(f"  {report.metadata.trajectory_id:40s} f1={report.research_hooks.category_f1}")
    print()
    print(f"Experiment-wide average category F1: {result.analysis.experiment_summary.average_category_f1}")
    print(f"Experiment-wide average complexity:  {result.analysis.experiment_summary.average_complexity_score}")


if __name__ == "__main__":
    main()
