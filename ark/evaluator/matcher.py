"""
Finding matcher — Milestone 6.2.

Aligns each of an agent's resolved findings (parser.py) against Ark's
normalized Issues (issues.py) and reports independent, structured signals
per finding — never a blended score, and never a final accuracy metric
(precision/recall/F1/calibration is Milestone 6.3's job; this module only
produces the per-finding inputs those metrics will be computed from).

Per your spec, four questions are answered per finding, deliberately
INDEPENDENTLY of one another rather than all derived from a single
"matched issue, so everything about it must be right" check:

- category_correct: did the agent name a transformation_type that is
  actually present SOMEWHERE in this estate's real issues? (independent of
  which entity the agent named)
- entity_correct: did the agent's entity_reference resolve, unambiguously,
  to a ground-truth entity that actually has a real issue on it?
  (independent of whether the stated issue_type is the right one)
- artifact_reference_correct: did artifact_reference resolve to a real
  rendered artifact at all?
- explanation_score_input: the agent's raw explanation text, carried
  forward unscored for a later, separate explanation-quality pass.

Evaluating these independently is what lets the matcher distinguish "wrong
entity but correct issue type" (partially correct) from "correct entity
but wrong issue type" (incorrect) from "correct on both" (correct) — three
different failure modes that a single boolean would collapse into one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ark.evaluator.issues import Issue
from ark.evaluator.parser import ResolvedFinding


@dataclass
class FindingMatchResult:
    finding_id: str
    matched_issue_id: str | None
    """The specific Issue this finding is being evaluated against: the
    Issue whose affected_entity_ids contains the finding's resolved
    entity, preferring one whose issue_type matches what the agent
    claimed (falling back to the first, in deterministic ledger order, if
    the entity has an issue but of a different type). None whenever the
    entity didn't resolve to a real entity with a real issue on it — a
    hallucination, a claim against a clean/unmutated entity, or an
    ambiguous/unresolved reference."""
    category_correct: bool
    entity_correct: bool
    artifact_reference_correct: bool
    explanation_score_input: str
    confidence: float
    artifact_matches_entity: bool
    """Extension beyond the minimal spec, carried from parser.py: True
    only if the resolved entity's own artifact is the SAME file the agent
    named — distinguishes "named a real file, but not the entity's real
    file" from "didn't name a real file at all" (both leave
    artifact_reference_correct's simpler, literal reading -- "did this
    resolve to a real artifact" -- untouched)."""
    entity_resolution_status: str
    """"resolved" / "ambiguous" / "unresolved" -- carried through for
    audit so a later reporting layer can distinguish "wrong" from "the
    reference was too vague to even attempt" (Ark_Evaluator_Design.md
    Section 6's flagged entity-resolution-ambiguity risk)."""
    claimed_issue_type: str
    """The agent's own (taxonomy-normalized) issue_type string, carried
    through unchanged. Added for Milestone 6.3: category_correct above is
    a coarse, estate-wide check ("is this type present ANYWHERE"), used
    only to distinguish "wrong entity but plausible category" from
    "wrong entity, made-up category" per Section 3's independent axes.
    Precise per-instance classification metrics (Milestone 6.3's
    metrics.py) need the actual claimed value to compare against the
    SPECIFIC matched issue's real type, which category_correct alone
    can't tell you."""
    artifact_reference: str
    """The agent's own claimed artifact_reference string, passed through
    unchanged -- not ground truth, not hidden metadata, just the agent's
    own claim text, needed by Milestone 6.3's explanation-signal
    extraction (does the explanation mention the artifact it named?)."""
    entity_reference: str
    """The agent's own claimed entity_reference string, passed through
    unchanged, for the same reason as artifact_reference above."""


def match_findings(resolved_findings: list[ResolvedFinding], issues: list[Issue]) -> list[FindingMatchResult]:
    """Match a list of already-resolved findings against a list of
    normalized Issues (ark.evaluator.issues.derive_issues output).

    Reads only `issues` (never the mutation ledger or ground-truth estate
    directly) and `resolved_findings` (already-parsed agent claims) —
    matching Ark's isolation principle that nothing downstream of the
    parser needs, or is given, direct access to hidden internals.
    """
    issues_by_entity: dict[str, list[Issue]] = {}
    for issue in issues:
        for entity_id in issue.affected_entity_ids:
            issues_by_entity.setdefault(entity_id, []).append(issue)

    issue_types_present = {issue.issue_type for issue in issues}

    results: list[FindingMatchResult] = []
    for resolved in resolved_findings:
        entity_id = (
            resolved.entity_resolution.entity_id
            if resolved.entity_resolution.status == "resolved"
            else None
        )

        matched_issue: Issue | None = None
        if entity_id is not None:
            candidates = issues_by_entity.get(entity_id, [])
            if candidates:
                matched_issue = next(
                    (i for i in candidates if i.issue_type == resolved.finding.issue_type),
                    candidates[0],
                )

        entity_correct = matched_issue is not None
        category_correct = resolved.finding.issue_type in issue_types_present

        results.append(
            FindingMatchResult(
                finding_id=resolved.finding_id,
                matched_issue_id=matched_issue.issue_id if matched_issue is not None else None,
                category_correct=category_correct,
                entity_correct=entity_correct,
                artifact_reference_correct=resolved.artifact_resolved,
                explanation_score_input=resolved.finding.explanation,
                confidence=resolved.finding.confidence,
                artifact_matches_entity=resolved.artifact_matches_entity,
                entity_resolution_status=resolved.entity_resolution.status,
                claimed_issue_type=resolved.finding.issue_type,
                artifact_reference=resolved.finding.artifact_reference,
                entity_reference=resolved.finding.entity_reference,
            )
        )

    return results
