"""
run_agent_harness() — Milestone 7's single entry point tying prompt
construction, the agent call, and response parsing together.

This is the ONE function in Ark that actually hands artifact content to
an agent and gets a response back. Its signature is the isolation
boundary made concrete: `artifacts: dict[str, str]` is the only estate-
shaped input it accepts -- not a `RenderedEstate`, not a manifest, not a
`GroundTruthEstate`, not a `MutationLedger`. See this package's
`__init__.py` docstring for the full reasoning.

Returns a plain `dict` -- exactly the shape
`ark.evaluator.orchestrator.evaluate()`'s `agent_output` parameter
already expects (raw, unvalidated JSON). This function does not call
`ark.evaluator.schema.parse_agent_output()` itself; validating the
contract stays evaluate()'s job, same as it already is for a hand-written
`AGENT_OUTPUT` dict in the Milestone 6 examples. Keeping that validation
call in exactly one place (evaluate()) means a harness-produced output
and a hand-authored test fixture are evaluated through the identical
code path, not two slightly-different ones.
"""

from __future__ import annotations

from ark.harness.contract import AgentClient
from ark.harness.prompt import build_agent_prompt
from ark.harness.response_parsing import extract_json_object


def run_agent_harness(artifacts: dict[str, str], agent_client: AgentClient) -> dict:
    """Run one full agent-harness round trip: build the prompt from
    `artifacts` alone, call `agent_client.generate()`, and extract the
    raw JSON object from its response.

    Raises `ark.harness.response_parsing.AgentResponseParsingError` if the
    agent's response contains no recoverable JSON object at all. Does not
    validate the recovered object's shape against the AgentOutput
    contract -- pass the return value straight to
    `ark.evaluator.orchestrator.evaluate()`, which does that validation
    (and will raise `ark.evaluator.schema.AgentOutputValidationError` if
    it's malformed).
    """
    prompt = build_agent_prompt(artifacts)
    raw_response = agent_client.generate(prompt)
    return extract_json_object(raw_response)
