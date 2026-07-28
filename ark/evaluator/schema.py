"""
Agent-output contract — Milestone 6.1.

This is the ONLY thing an evaluated agent ever produces, and the agent
never sees anything defined in issues.py, ark.mutation.ledger, or
ark.core.models while producing it — it only ever inspects rendered
artifacts (Mule XML, DataWeave, API specs) exactly as a real enterprise
engineer would (Ark_Evaluator_Design.md Section 1's "critical point").

Schema, exactly as specified (Ark_Evaluator_Design.md Section 5.2):

    {
      "findings": [
        {
          "artifact_reference": "customer-api.xml",
          "entity_reference": "Customer API",
          "issue_type": "documentation_decay",
          "explanation": "The API documentation is incomplete because migration information is missing.",
          "confidence": 0.87
        }
      ]
    }

`artifact_reference` and `entity_reference` are singular strings (an agent
names one artifact and one entity per finding, never a list) and use
artifact-visible identifiers only — rendered file names / rendered display
labels, never Ark's internal entity ids. Resolving these against the
rendering manifest is Milestone 6.2's parser.py; this module only defines
and validates the contract's shape.

Technology independence is enforced here, not just documented: issue_type
must be one of Ark's six technology-agnostic transformation types, or the
catch-all "other". Milestone 4's mutation operators guarantee every
transformed estate renders to valid, well-formed output (its own no-op/
validity invariant) — so a technology-syntax complaint like "invalid Mule
XML attribute" can never correspond to a real injected issue. Rather than
trust every agent/harness to only ever emit taxonomy values, parse_agent_output
normalizes any non-taxonomy issue_type to "other" (recording the original
value for audit), which guarantees such a claim can never match a real
Issue's specific issue_type in later matching (Milestone 6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ark.mutation.registry import OPERATOR_REGISTRY

AGENT_OUTPUT_SCHEMA_VERSION = "0.1.0"

# Ark's six operator transformation_type values, plus "other" for anything
# outside that taxonomy. Derived from the live registry (not hand-copied)
# so this can never silently drift from ark/mutation/registry.py if a 7th
# operator is ever added.
ISSUE_TYPE_TAXONOMY: frozenset[str] = frozenset(OPERATOR_REGISTRY.keys()) | {"other"}

_REQUIRED_FINDING_FIELDS = (
    "artifact_reference",
    "entity_reference",
    "issue_type",
    "explanation",
    "confidence",
)


class AgentOutputValidationError(Exception):
    """Raised when a raw agent-output dict fails structural validation.
    Mirrors ark.core.validate.GroundTruthValidationError's style: collect
    every problem found, never fail on just the first one."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(
            f"Agent output failed validation with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


@dataclass
class Finding:
    """One parsed, validated finding from an agent's output."""

    artifact_reference: str
    entity_reference: str
    issue_type: str
    """Normalized: always a member of ISSUE_TYPE_TAXONOMY. See raw_issue_type
    if the agent's original value fell outside the taxonomy."""
    explanation: str
    confidence: float
    raw_issue_type: str = ""
    """The agent's original issue_type string, preserved verbatim for audit
    even when normalized to "other". Equal to issue_type when the agent's
    value was already in the taxonomy."""


@dataclass
class AgentOutput:
    findings: list[Finding] = field(default_factory=list)
    agent_output_schema_version: str = AGENT_OUTPUT_SCHEMA_VERSION


def _validate_finding(raw: Any, index: int, errors: list[str]) -> Finding | None:
    ctx = f"findings[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{ctx}: must be an object, got {type(raw).__name__}.")
        return None

    missing = [f for f in _REQUIRED_FINDING_FIELDS if f not in raw]
    if missing:
        errors.append(f"{ctx}: missing required field(s): {missing}.")
        return None

    string_fields = ("artifact_reference", "entity_reference", "issue_type", "explanation")
    local_errors: list[str] = []
    for f in string_fields:
        if not isinstance(raw[f], str) or not raw[f].strip():
            local_errors.append(f"{ctx}.{f}: must be a non-empty string, got {raw[f]!r}.")

    confidence = raw["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        local_errors.append(f"{ctx}.confidence: must be a number, got {confidence!r}.")
    elif not (0.0 <= float(confidence) <= 1.0):
        local_errors.append(f"{ctx}.confidence: must be between 0.0 and 1.0, got {confidence!r}.")

    if local_errors:
        errors.extend(local_errors)
        return None

    raw_issue_type = raw["issue_type"]
    normalized_issue_type = raw_issue_type if raw_issue_type in ISSUE_TYPE_TAXONOMY else "other"

    return Finding(
        artifact_reference=raw["artifact_reference"],
        entity_reference=raw["entity_reference"],
        issue_type=normalized_issue_type,
        explanation=raw["explanation"],
        confidence=float(confidence),
        raw_issue_type=raw_issue_type,
    )


def parse_agent_output(raw: dict) -> AgentOutput:
    """Validate and parse a raw agent-output dict (as produced by
    json.loads on the agent's response) into an AgentOutput.

    Raises AgentOutputValidationError, listing every problem found, if the
    top-level shape or any finding is malformed. Does not raise for an
    unrecognized issue_type — that is a normalization (-> "other"), not a
    structural error, since it's a real (if low-quality) agent response,
    not malformed JSON.
    """
    errors: list[str] = []

    if not isinstance(raw, dict):
        raise AgentOutputValidationError([f"top-level agent output must be an object, got {type(raw).__name__}."])

    if "findings" not in raw:
        raise AgentOutputValidationError(["agent output missing required field: 'findings'."])

    if not isinstance(raw["findings"], list):
        raise AgentOutputValidationError(
            [f"'findings' must be a list, got {type(raw['findings']).__name__}."]
        )

    findings: list[Finding] = []
    for i, raw_finding in enumerate(raw["findings"]):
        finding = _validate_finding(raw_finding, i, errors)
        if finding is not None:
            findings.append(finding)

    if errors:
        raise AgentOutputValidationError(errors)

    return AgentOutput(findings=findings)
