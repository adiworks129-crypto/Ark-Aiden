# Milestone 7 worked example: the agent harness + experiment runner

`run_experiment_example.py` runs `ark.experiment.run_experiment()` — Milestone 7's
top-level entry point — across six trajectories: all four difficulty profiles against
Milestone 1's hand-authored baseline (seed 1), plus two `ark.generator`-produced
estates (`level_2_structural`, seeds 1-2). Every trajectory goes through the **real**
harness round trip: `ark.harness.prompt.build_agent_prompt()` builds a prompt from
`rendered.artifacts` alone, the agent client's `generate()` returns raw text,
`ark.harness.response_parsing.extract_json_object()` recovers the JSON, and
`ark.evaluator.orchestrator.evaluate()` scores it — exactly the same pipeline a real
LLM-backed run uses.

## The agent: `HeuristicNamingAgentClient`

`heuristic_agent_client.py` implements `ark.harness.contract.AgentClient` with a
genuinely offline, ground-truth-blind heuristic: it looks only at the rendered artifact
text it's handed (API `title:` fields, `name="..."` attributes) and flags two real,
visible irregularities that happen to match how `ark.mutation.operators`'s
`NamingDriftOperator` actually renders:

- a name's first word/segment in ALL CAPS while the rest isn't (`_style_case_shift`
  only uppercases the first segment, e.g. `"Order Processing Process API"` ->
  `"ORDER Processing Process API"`);
- a name ending in one of the exact suffixes `_style_add_legacy_suffix` appends
  (`_v2_final`, `_old`, `_deprecated`, `-copy2`, or the word "legacy" generically).

This is deliberately a **weak** agent — it can only ever catch `naming_drift`, and only
when a drift style happens to leave a textually obvious irregularity (severity/style
choice is random per mutation, so it sometimes misses even that). That's the point:
Ark's per-category, per-axis metrics (not one pass/fail score) are what let a batch of
reports from an agent like this be characterized honestly — see the per-trajectory F1
values below, which range from 0.0 to ~0.55 depending on what each trajectory's random
drift styles happened to produce.

**A first real bug this heuristic's own construction caught** (fixed before committing,
consistent with this project's practice of surfacing rather than hiding real mistakes
found along the way): the first draft's title regex captured only the first
whitespace-delimited token after `title:` (so `"ORDER Processing Process API"` was read
as just `"ORDER"`), and its legacy-suffix pattern didn't match any of the actual suffix
strings the operator generates (it guessed `"legacy"`/`"_v0"`/`"-old"`, none of which
`_style_add_legacy_suffix` ever produces). Both were fixed by reading
`ark/mutation/operators.py`'s actual style functions rather than guessing at their
output.

## Using a real LLM instead

Swap the one `agent_client = HeuristicNamingAgentClient()` line for:

```python
from integrations.anthropic_agent_client import AnthropicAgentClient
agent_client = AnthropicAgentClient()   # reads ANTHROPIC_API_KEY from the environment
```

(requires `pip install -e ".[llm]"` for the `anthropic` package — see
`integrations/__init__.py` for why that dependency lives outside `ark/` entirely).
Nothing else in the script, or in `ark/experiment/runner.py`, needs to change — both
clients satisfy the same `ark.harness.contract.AgentClient` interface.

## Isolation boundary, concretely

At no point does `agent_client.generate()` receive `rendered.manifest`,
`result.transformed_estate`, or `result.ledger` — `run_trajectory_spec()` extracts
`rendered.artifacts` (a plain `dict[str, str]`) before the harness call, and passes the
other three only to `evaluate()`, strictly afterward. `tests/test_milestone7.py` proves
this with both an AST-based import check (`ark/harness/*` never imports
`ark.core.models`/`ark.mutation.*`/`ark.adapters.*`) and a wiring-level spy test
confirming the exact object handed to `run_agent_harness()` in a real trajectory run.

## Reading `run_output/`

- `run_output/reports/<label>.json` — one `EvaluationReport` per trajectory (via
  `report_to_json()`).
- `run_output/analysis.json` — the aggregate `ExperimentAnalysis` across all six (via
  `analysis_to_json()`).

Unlike Milestone 6's committed examples, `generated_at` here is **not** pinned — this
is meant to show what a real experiment run's output looks like, not to be a
byte-for-byte reproducible fixture. Every other field is fully deterministic given the
same seeds/profiles (verified by `tests/test_milestone7.py`'s determinism-adjacent
coverage at the report/analysis level, inherited unchanged from Milestones 6.4/6.5).

## Regenerating

```bash
PYTHONPATH=. python3 examples/milestone7/run_experiment_example.py
```
