"""
The agent-client interface — Milestone 7.

Deliberately the smallest possible contract: an `AgentClient` takes one
prompt string and returns one raw text response string. Nothing about
this interface knows or cares whether the concrete implementation behind
it is a real LLM API call, a scripted offline double, or (in principle)
a human typing into a terminal. `ark/experiment/runner.py` depends only
on this ABC, never on any specific implementation — the same
"depend on the interface, not the concrete class" discipline
`ark.adapters.base.TargetAdapter` already established for rendering
targets.

Kept intentionally free of any prompt-construction or response-parsing
logic (that's prompt.py / response_parsing.py) and free of any vendor SDK
import (that's what keeps this module — and everything that depends only
on it — zero-dependency).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AgentClient(ABC):
    """Abstract interface every concrete agent backend implements.

    A single round-trip: one prompt in, one raw text response out. No
    conversation state, no tool-use loop, no streaming — the simplest
    contract that can still drive a real single-shot LLM call
    (Milestone 7's approved scope; a richer tool-based harness, where the
    agent requests individual files rather than receiving them all
    up-front, is a natural but explicitly out-of-scope future extension).
    """

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Send `prompt` to the agent and return its raw text response.

        Implementations may raise any exception on failure (network
        error, missing API key, rate limit, ...) — `run_agent_harness()`
        does not catch or reinterpret these; a failed agent call is a
        failed evaluation run, not something to silently paper over.
        """
        raise NotImplementedError
