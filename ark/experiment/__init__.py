"""
Ark's experiment runner — Milestone 7.

Ties every prior milestone together into one runnable arc: a baseline
estate (hand-authored or `ark.generator`-produced) -> a mutation
trajectory (`ark.mutation`) -> a rendered artifact bundle
(`ark.adapters`) -> an agent's findings (`ark.harness`) ->
an `EvaluationReport` (`ark.evaluator.orchestrator`) -> across many
trajectories, an `ExperimentAnalysis` (`ark.evaluator.analysis`).

This package is free to import anything under `ark/` (it's Ark's own
orchestration of Ark's own subsystems) and `ark.harness`'s `AgentClient`
interface, but it never imports a specific vendor SDK and never imports
anything from the repo-root `integrations/` directory — it only ever
receives an already-constructed `AgentClient`, whatever concrete
implementation that happens to be (a real `AnthropicAgentClient`, the
offline `ScriptedAgentClient`, or a test double), and calls it through
that one interface. See `ark/harness/__init__.py` and
`integrations/__init__.py` for why that boundary matters.

`run_experiment()` is a plain Python entry point. Milestone 8's
`ark/ui/` is a thin Streamlit consumer of it (a UI, not a redesign — see
`ark/ui/__init__.py`); a command-line wrapper around it remains future
work.
"""

from __future__ import annotations
