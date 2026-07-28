"""
Agent prompt construction — Milestone 7.

Builds the single prompt string an agent sees, from ONLY a rendered
artifact bundle (`dict[str, str]`, artifact path -> file contents) — see
this package's `__init__.py` docstring for why that's the only input type
this module (or anything downstream of it in this package) ever accepts.

The instructions embedded here restate the exact `AgentOutput` JSON
contract `ark.evaluator.schema.parse_agent_output()` validates, and the
exact `issue_type` taxonomy it accepts — sourced directly from
`ark.evaluator.schema` (never hand-copied) so the prompt can never drift
from what the parser actually enforces. This is the one, deliberate,
documented exception to "never import ark.evaluator internals here": the
taxonomy and JSON shape are the agent-VISIBLE output contract, not ground
truth, not the ledger, not the manifest.
"""

from __future__ import annotations

from ark.evaluator.schema import ISSUE_TYPE_TAXONOMY

_INSTRUCTIONS_TEMPLATE = """\
You are reviewing an exported enterprise integration project (API
specifications and integration-flow configuration files). Your job is to
find issues in these files: places where naming looks inconsistent,
documentation is missing or incomplete, logic appears duplicated,
components look outdated relative to the rest of the project, schemas
look inconsistent, or a dependency between components looks wrong.

You do not have access to anything beyond the files shown below — no
version history, no internal identifiers, no external documentation.
Base every finding only on what is actually visible in these files.

Respond with a single JSON object, and nothing else (no prose before or
after it, no markdown code fence), matching exactly this shape:

{{
  "findings": [
    {{
      "artifact_reference": "<the file path, exactly as shown below, that this finding is about>",
      "entity_reference": "<the specific name/label, exactly as it appears in that file, that this finding is about>",
      "issue_type": "<one of: {issue_types}>",
      "explanation": "<why you believe this, in plain language, referencing what you actually observed>",
      "confidence": <a number between 0.0 and 1.0>
    }}
  ]
}}

If you find nothing wrong, respond with {{"findings": []}}. Every field
above is required for every finding. `issue_type` should be your best
match from the list above; if truly none fit, use "other".

# Files

"""


def _render_artifacts_section(artifacts: dict[str, str]) -> str:
    """Deterministic, sorted-by-path rendering of every artifact into one
    text block -- sorted so the prompt (and therefore, for a
    temperature-0 agent, its response) is reproducible across runs, not
    dependent on dict insertion order."""
    sections = []
    for path in sorted(artifacts):
        sections.append(f"## {path}\n```\n{artifacts[path]}\n```")
    return "\n\n".join(sections)


def build_agent_prompt(artifacts: dict[str, str]) -> str:
    """Build the single prompt string for one evaluation run.

    `artifacts` must be exactly `RenderedEstate.artifacts` (or an
    equivalent plain `dict[str, str]`) -- this function does not accept,
    and has no way to accidentally receive, anything richer (a manifest,
    a ground-truth estate, a mutation ledger). See the package docstring
    for why that's the isolation boundary's whole point.
    """
    instructions = _INSTRUCTIONS_TEMPLATE.format(
        issue_types=", ".join(sorted(ISSUE_TYPE_TAXONOMY)),
    )
    return instructions + _render_artifacts_section(artifacts)


__all__ = ["build_agent_prompt"]
