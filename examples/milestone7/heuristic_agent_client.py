"""
Milestone 7 worked example's agent -- re-exported from its Milestone 8
home, `ark.harness.heuristic_client`.

This class was originally written here, as one-off example code. Milestone
8's ark/ui needed the exact same "genuinely offline, ground-truth-blind
heuristic agent" behavior for its own scripted-agent demo option, so
rather than duplicate the logic a second time, it was promoted into
`ark/harness/heuristic_client.py` as a proper, reusable reference
`AgentClient` (alongside `ark.harness.scripted_client.ScriptedAgentClient`).
This module now just re-exports it, so
`examples/milestone7/run_experiment_example.py`'s existing
`from heuristic_agent_client import HeuristicNamingAgentClient` import
keeps working unchanged -- see `ark/harness/heuristic_client.py` for the
full implementation and docstring.
"""

from __future__ import annotations

from ark.harness.heuristic_client import HeuristicNamingAgentClient

__all__ = ["HeuristicNamingAgentClient"]
