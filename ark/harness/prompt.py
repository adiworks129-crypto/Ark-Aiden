"""
Agent prompt construction — Milestone 7, plus Session E's taxonomy
descriptions.

Builds the single prompt string an agent sees, from ONLY a rendered
artifact bundle (`dict[str, str]`, artifact path -> file contents) — see
this package's `__init__.py` docstring for why that's the only input type
this module (or anything downstream of it in this package) ever accepts.
Still true after Session E: `build_agent_prompt()`'s signature is
unchanged (`artifacts` only, no `profile_name` or other profile-specific
input) -- Session E is richer CONTENT within the one fixed prompt, not
per-profile prompt variation. See ark.experiment.runner's own module
docstring / the harness's "one fixed prompt, not regenerated per profile"
design for why that boundary is deliberate, not an oversight.

The instructions embedded here restate the exact `AgentOutput` JSON
contract `ark.evaluator.schema.parse_agent_output()` validates, and the
exact `issue_type` taxonomy it accepts, NOW each with a short description
(`ISSUE_TYPE_DESCRIPTIONS`) — both sourced directly from
`ark.evaluator.schema` (never hand-copied) so the prompt can never drift
from what the parser actually enforces, or from what each operator's own
`.description` actually says. This is the one, deliberate, documented
exception to "never import ark.evaluator internals here": the taxonomy,
its descriptions, and the JSON shape are the agent-VISIBLE output
contract, not ground truth, not the ledger, not the manifest, and (still,
deliberately) never the estate's own declared domain -- see
Ark_Domain_Injection_Absence_Investigation-adjacent follow-up notes on why
exposing that is a separate, larger, not-yet-approved change.
"""

from __future__ import annotations

from ark.evaluator.schema import ISSUE_TYPE_DESCRIPTIONS, ISSUE_TYPE_TAXONOMY

_INSTRUCTIONS_TEMPLATE = """\
You are reviewing an exported enterprise integration project (API
specifications and integration-flow configuration files). Your job is to
find issues in these files, using the issue-type taxonomy below.

You do not have access to anything beyond the files shown below — no
version history, no internal identifiers, no external documentation.
Base every finding only on what is actually visible in these files.

# Issue type taxonomy

{issue_taxonomy}

Respond with a single JSON object, and nothing else (no prose before or
after it, no markdown code fence), matching exactly this shape:

{{
  "findings": [
    {{
      "artifact_reference": "<the file path, exactly as shown below, that this finding is about>",
      "entity_reference": "<the specific name/label, exactly as it appears in that file, that this finding is about>",
      "issue_type": "<the single best-matching type name from the taxonomy above>",
      "explanation": "<why you believe this, in plain language, referencing what you actually observed>",
      "confidence": <a number between 0.0 and 1.0>
    }}
  ]
}}

If you find nothing wrong, respond with {{"findings": []}}. Every field
above is required for every finding. `issue_type` should be your best
match from the taxonomy above; if truly none fit, use "other".

# Files

"""


def _render_issue_taxonomy() -> str:
    """One `- <type_name>: <description>` line per ISSUE_TYPE_TAXONOMY
    member, sorted by type name -- same determinism reasoning
    _render_artifacts_section() below already documents (a temperature-0
    agent's response shouldn't depend on dict/set iteration order).
    ISSUE_TYPE_DESCRIPTIONS is guaranteed (by
    tests/test_issue_taxonomy_prompt.py, checking the live registry, not
    just this call site) to have a non-empty entry for every taxonomy
    member, so no fallback/default is needed here -- a missing one should
    raise a loud KeyError during development, not render silently blank."""
    lines = [f"- {type_name}: {ISSUE_TYPE_DESCRIPTIONS[type_name]}" for type_name in sorted(ISSUE_TYPE_TAXONOMY)]
    return "\n".join(lines)


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
    a ground-truth estate, a mutation ledger, a profile name). See the
    package docstring for why that's the isolation boundary's whole point,
    and this module's own docstring for why "no profile_name" is
    deliberate, not an oversight Session E left unfinished.
    """
    instructions = _INSTRUCTIONS_TEMPLATE.format(issue_taxonomy=_render_issue_taxonomy())
    return instructions + _render_artifacts_section(artifacts)


__all__ = ["build_agent_prompt"]
