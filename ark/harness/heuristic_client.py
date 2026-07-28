"""
HeuristicNamingAgentClient -- Ark's second offline reference AgentClient
(alongside scripted_client.py's ScriptedAgentClient).

Originally written as one-off example code for Milestone 7
(examples/milestone7/heuristic_agent_client.py) and promoted here in
Milestone 8 because it turned out to have a second, legitimate reuse:
ark/ui's "ScriptedAgentClient" demo option needs SOME non-trivial,
fully-offline, no-API-key-required agent behavior to make an interactive
demo worth looking at, and rebuilding that logic a second time in
ark/ui would have been a real duplication this module now avoids.
examples/milestone7/heuristic_agent_client.py now just re-exports this
class, so nothing that already imports from there needs to change.

A genuinely rendered-content-only heuristic "agent": it looks ONLY at the
prompt text an agent would receive (the rendered artifact contents) and
applies a couple of real, visible-in-the-text heuristics -- no access to
ground truth, issues, or the ledger at all, because at the point this
class runs, none of that exists on its side of the isolation boundary;
it only ever receives what `ark.harness.prompt.build_agent_prompt()`
produced.

Two heuristics, chosen because they're real, observable signals in
MuleSoft's rendered output (see `ark/mutation/operators.py`'s
NamingDriftOperator styles):
  1. An API/flow name whose FIRST word/segment is in ALL CAPS while the
     rest isn't looks like the `_style_case_shift` drift style (which
     only uppercases the first segment) -- flagged as naming_drift.
  2. A name ending in one of the exact suffixes `_style_add_legacy_suffix`
     actually appends (`_v2_final`, `_old`, `_deprecated`, `-copy2`), or
     containing the word "legacy" generically -- flagged as naming_drift.

This deliberately does NOT try to catch every mutation type (it can't --
several of Ark's operators, e.g. dependency_change or duplicate_processing,
have no equally simple, reliable, content-only textual signature this
heuristic exploits). That's the honest, expected outcome: a simple
heuristic finds SOME real issues and misses others, which is exactly the
kind of partial-credit result Ark's metrics (precision/recall/F1, not one
pass/fail number) exist to characterize.
"""

from __future__ import annotations

import json
import re

from ark.harness.contract import AgentClient

_TITLE_PATTERN = re.compile(r'^title:\s*(.+)$', re.MULTILINE)
_NAME_ATTR_PATTERN = re.compile(r'name="([^"]+)"')
# The exact suffixes ark.mutation.operators's _style_add_legacy_suffix
# actually appends, plus the generic word "legacy" in case a future
# operator uses it directly -- see that function's source for why these
# specific strings, not a made-up guess at what "looks legacy."
_LEGACY_SUFFIX_PATTERN = re.compile(r'(_v\d+_final|_old\b|_deprecated\b|-copy\d*\b|legacy)', re.IGNORECASE)


def _split_name(name: str) -> list[str]:
    if "-" in name:
        return name.split("-")
    if " " in name:
        return name.split(" ")
    return [name]


def _looks_like_case_shift(name: str) -> bool:
    """ark.mutation.operators's _style_case_shift only uppercases the
    FIRST word/segment of a multi-segment name (e.g. "Order Processing
    Process API" -> "ORDER Processing Process API"), never the whole
    string. The visible, honest-to-detect signal is therefore "the first
    segment shouts in all caps while the rest of the name doesn't" -- a
    real, content-only irregularity, not a peek at the mutation itself."""
    segments = _split_name(name)
    if len(segments) > 1:
        first = segments[0]
        return (
            len(first) > 2 and first.isalpha() and first.isupper()
            and not all(seg.isupper() for seg in segments if seg.isalpha())
        )
    return name.isalpha() and name.isupper() and len(name) > 3


def _looks_like_legacy_suffix(name: str) -> bool:
    return bool(_LEGACY_SUFFIX_PATTERN.search(name))


def _candidate_names_in_artifact(path: str, content: str) -> list[str]:
    names = []
    if path.endswith(".yaml"):
        names.extend(match.strip() for match in _TITLE_PATTERN.findall(content))
    else:
        names.extend(_NAME_ATTR_PATTERN.findall(content))
    return names


class HeuristicNamingAgentClient(AgentClient):
    """A deterministic AgentClient that only ever reads the prompt text
    it's handed -- no ground truth, no issues, no ledger. Implements the
    same `generate(prompt) -> str` contract a real LLM-backed client
    would, so it's a drop-in stand-in for one in `run_experiment()`."""

    def generate(self, prompt: str) -> str:
        # The prompt embeds each artifact as "## <path>\n```\n<content>\n```".
        sections = re.findall(r"## (\S+)\n```\n(.*?)\n```", prompt, re.DOTALL)
        findings = []
        for path, content in sections:
            for name in _candidate_names_in_artifact(path, content):
                if _looks_like_case_shift(name):
                    findings.append(self._finding(path, name, confidence=0.7))
                elif _looks_like_legacy_suffix(name):
                    findings.append(self._finding(path, name, confidence=0.65))
        return json.dumps({"findings": findings})

    @staticmethod
    def _finding(artifact_reference: str, entity_reference: str, confidence: float) -> dict:
        return {
            "artifact_reference": artifact_reference,
            "entity_reference": entity_reference,
            "issue_type": "naming_drift",
            "explanation": (
                f"'{entity_reference}' in {artifact_reference} uses a naming style "
                "(all-caps or a legacy-looking suffix) that's inconsistent with the "
                "rest of the project's naming convention."
            ),
            "confidence": confidence,
        }
