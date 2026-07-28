"""
Explanation quality signals — Milestone 6.3.

Deliberately NOT a semantic quality score. Per your instruction, this
module does not reach for an LLM judge (yet) — it extracts a small set of
structural, rule-based signals from an agent's free-text explanation, so a
later milestone (or a human reviewer) can decide how to weigh them into an
actual quality judgment. Every signal here is a shallow, deterministic,
technology-independent text check (substring/keyword presence, after
case/whitespace normalization) — it answers "does the text mention X,"
never "does the agent understand X." Treat these as structured hints for
a future scoring pass, not as a finished verdict on explanation quality.

Architecture: reads only a FindingMatchResult (for the agent's own claim
text, artifact/entity references, and matched_issue_id) and the Issue list
it matched against — no mutation ledger, rendering manifest, or
ground-truth estate access, and nothing MuleSoft-specific
(Issue.observable_symptom is already technology-agnostic).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ark.evaluator.issues import Issue
from ark.evaluator.matcher import FindingMatchResult

EXPLANATION_SIGNALS_SCHEMA_VERSION = "0.1.0"

_CAUSAL_MARKERS = (
    "because",
    "due to",
    "caused by",
    "since",
    "as a result",
    "leading to",
    "resulting in",
    "which caused",
    "the reason",
)

_MAX_SYMPTOM_VALUE_LENGTH = 200
"""observable_symptom values can be entire DataWeave scripts; only
shorter values are fair substring-match candidates for "did the
explanation reference this" — matching a 400-character script verbatim
isn't a realistic bar, so longer values are skipped rather than silently
never matching (and, consistent with parser.py's documented tradeoff,
never fuzzily/partially matched either)."""


@dataclass
class ExplanationSignals:
    finding_id: str
    mentions_affected_artifact: bool
    """Whether the explanation text contains the agent's OWN claimed
    artifact_reference (or its basename). A self-consistency check, not a
    check against ground truth: does the explanation actually talk about
    the file the finding claims to be about?"""
    references_observable_symptom: bool
    """Whether the explanation text contains any short, string-valued
    field name or value from the MATCHED Issue's observable_symptom.
    Always False when matched_issue_id is None — there is nothing real to
    reference."""
    identifies_plausible_cause: bool
    """Shallow keyword heuristic: does the text contain common causal
    language ("because", "due to", "caused by", ...)? A genuinely causal
    explanation could still lack these words, and their presence doesn't
    guarantee the stated cause is actually correct — this is a surface
    signal, not a correctness judgment."""
    unsupported_assumption_flag: bool
    """True when the explanation makes a claim but grounds it in NEITHER
    the artifact it names nor any real observable symptom — a structural
    proxy for "purely speculative," not a semantic judgment of whether the
    claim itself is plausible."""
    explanation_length_chars: int


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _symptom_candidates(issue: Issue | None) -> list[str]:
    if issue is None:
        return []
    candidates: list[str] = []
    for entity_fields in issue.observable_symptom.values():
        for field_name, value in entity_fields.items():
            candidates.append(field_name)
            if isinstance(value, str) and value and len(value) <= _MAX_SYMPTOM_VALUE_LENGTH:
                candidates.append(value)
    return candidates


def extract_explanation_signals(match: FindingMatchResult, issues: list[Issue]) -> ExplanationSignals:
    """Extract structural signals for one already-matched finding."""
    explanation = _normalize(match.explanation_score_input)

    artifact_candidates = {match.artifact_reference, match.artifact_reference.rsplit("/", 1)[-1]}
    mentions_artifact = any(_normalize(c) in explanation for c in artifact_candidates if c)

    matched_issue = None
    if match.matched_issue_id is not None:
        matched_issue = next((i for i in issues if i.issue_id == match.matched_issue_id), None)
    symptom_candidates = _symptom_candidates(matched_issue)
    references_symptom = any(_normalize(c) in explanation for c in symptom_candidates if c)

    identifies_cause = any(marker in explanation for marker in _CAUSAL_MARKERS)

    unsupported_assumption = bool(explanation) and not mentions_artifact and not references_symptom

    return ExplanationSignals(
        finding_id=match.finding_id,
        mentions_affected_artifact=mentions_artifact,
        references_observable_symptom=references_symptom,
        identifies_plausible_cause=identifies_cause,
        unsupported_assumption_flag=unsupported_assumption,
        explanation_length_chars=len(match.explanation_score_input),
    )


def extract_explanation_signals_for_matches(
    matches: list[FindingMatchResult], issues: list[Issue]
) -> list[ExplanationSignals]:
    return [extract_explanation_signals(match, issues) for match in matches]
