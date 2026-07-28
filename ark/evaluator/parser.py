"""
Agent-reference resolution — Milestone 6.2.

Resolves an agent Finding's artifact-visible references
(`artifact_reference`, `entity_reference`) back into Ark's internal
ground-truth entity ids, using ONLY the rendering manifest — never the
ground-truth estate or mutation ledger directly. The manifest is
deliberately the sole bridge between what an agent can observe and what
Ark knows internally (Ark_Evaluator_Design.md Section 5.5 and Section 7's
isolation principle): this module never imports ark.core.models,
ark.mutation.ledger, or ark.mutation.engine, and never receives a
GroundTruthEstate or MutationLedger object as an argument. If it can't
answer a question from the manifest dict alone, it reports "unresolved"
rather than reaching for a richer source it isn't supposed to have.

Entity resolution strategy — the tradeoff, stated up front:

This module supports exact artifact-path matching, an exact-basename
fallback, exact rendered-name matching, and a small set of deterministic,
normalized aliases (case-folding and separator-collapsing only:
"Customer_API_v2" and "Customer API v2" are treated as the same
reference, and a `FlowRefStep`'s bare target-flow name is accepted as well
as its full synthesized label — see renderer.py's `_step_entity`). It
deliberately does NOT do fuzzy, edit-distance, or substring matching.

The goal, per your framing, is "could a competent AI agent infer this from
the artifacts?" — a competent agent would reproduce a rendered name close
enough to survive case/separator normalization, but a fuzzy matcher that
tolerates typos or partial substrings would let an evaluator "find"
matches a real agent never actually demonstrated, silently inflating
scores and making the whole benchmark less meaningful. Any reference that
doesn't resolve under this conservative rule is reported as `unresolved`,
never guessed at — and a reference that matches more than one entity is
reported as `ambiguous`, never silently resolved to "the first match" (a
real risk once two entities happen to share a rendered name, which is
exactly one of this milestone's required test cases).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ark.evaluator.schema import Finding

ResolutionStatus = Literal["resolved", "ambiguous", "unresolved"]


@dataclass
class EntityResolution:
    status: ResolutionStatus
    entity_id: str | None
    entity_type: str | None
    candidate_entity_ids: list[str] = field(default_factory=list)
    """Populated only when status == "ambiguous" — every entity id that
    matched, for audit. Never used to silently pick a winner."""
    basis: str = ""
    """Which rule resolved this reference (or why it didn't) — an audit
    string, not used by any downstream matching logic."""


@dataclass
class ResolvedFinding:
    """One Finding plus everything the parser could determine about its
    references, ready for matcher.py. Carries no ground-truth or ledger
    data beyond what the manifest already exposed."""

    finding: Finding
    finding_id: str
    """Synthesized (finding_id is not part of the required agent-output
    contract — Ark_Evaluator_Design.md Section 5.2 — so this module
    assigns one deterministically from list position: f"finding-{i:03d}")."""
    artifact_resolved: bool
    resolved_artifact_path: str | None
    artifact_matches_entity: bool
    """True only if the resolved entity's OWN artifact (per the manifest's
    entity_index) is the SAME artifact the finding's artifact_reference
    resolved to. False whenever entity resolution fell back to a
    whole-manifest name search because the artifact reference didn't
    resolve, or resolved to a different file than where the entity
    actually lives — a real, useful distinction between "wrong file
    entirely" and "right entity, mismatched file claim."""
    entity_resolution: EntityResolution


def _normalize(name: str) -> str:
    """Case-fold and collapse separators (`_`, `-`, whitespace) to a
    single space. Deliberately the ONLY normalization applied here — no
    fuzzy matching, per the module's documented tradeoff above."""
    return re.sub(r"[\s_\-]+", " ", name.strip().lower())


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _entities_for_artifact(manifest: dict, artifact_path: str) -> list[dict]:
    for artifact in manifest.get("artifacts", []):
        if artifact["path"] == artifact_path:
            return artifact["entities"]
    return []


def _resolve_artifact_path(manifest: dict, artifact_reference: str) -> str | None:
    """Exact full-path match first; otherwise an exact-basename match, but
    only if exactly one artifact in the manifest has that basename (an
    ambiguous basename resolves to nothing, not a guess)."""
    paths = [a["path"] for a in manifest.get("artifacts", [])]
    if artifact_reference in paths:
        return artifact_reference

    target_basename = _basename(artifact_reference)
    basename_matches = [p for p in paths if _basename(p) == target_basename]
    if len(basename_matches) == 1:
        return basename_matches[0]
    return None


def _candidate_names(entity: dict) -> set[str]:
    names = {entity.get("name") or ""}
    names.update(entity.get("aliases", []))
    return {n for n in names if n}


def _match_entities_by_name(entities: list[dict], entity_reference: str) -> list[dict]:
    target = _normalize(entity_reference)
    return [entity for entity in entities if any(_normalize(c) == target for c in _candidate_names(entity))]


def resolve_entity_reference(
    manifest: dict, artifact_reference: str, entity_reference: str
) -> tuple[str | None, bool, EntityResolution]:
    """Resolve one (artifact_reference, entity_reference) pair against a
    rendering manifest. Returns (resolved_artifact_path,
    artifact_matches_entity, EntityResolution) — see ResolvedFinding's
    docstring for what each means.

    Resolution order:
    1. Resolve the artifact reference (exact path, then unambiguous
       basename).
    2. If the artifact resolved, look for the entity by name/alias WITHIN
       that artifact's own entities first (the common, expected case: an
       agent naming a file and a thing rendered inside it).
    3. If that didn't find exactly one entity (either the artifact didn't
       resolve, or the name wasn't found within it), fall back to a
       whole-manifest name search — this recovers "right entity, wrong/
       vague file reference" rather than giving up entirely, at the cost
       of flagging artifact_matches_entity=False when the entity's real
       artifact differs from what the agent claimed.
    4. More than one entity matching a name at any stage is reported as
       `ambiguous`, never resolved by picking one.
    """
    resolved_artifact_path = _resolve_artifact_path(manifest, artifact_reference)

    if resolved_artifact_path is not None:
        scoped_entities = _entities_for_artifact(manifest, resolved_artifact_path)
        scoped_matches = _match_entities_by_name(scoped_entities, entity_reference)

        if len(scoped_matches) == 1:
            entity = scoped_matches[0]
            return (
                resolved_artifact_path,
                True,
                EntityResolution(
                    status="resolved",
                    entity_id=entity["id"],
                    entity_type=entity["type"],
                    basis="exact_name_within_resolved_artifact",
                ),
            )
        if len(scoped_matches) > 1:
            return (
                resolved_artifact_path,
                True,
                EntityResolution(
                    status="ambiguous",
                    entity_id=None,
                    entity_type=None,
                    candidate_entity_ids=[e["id"] for e in scoped_matches],
                    basis="multiple_entities_share_this_name_within_the_resolved_artifact",
                ),
            )
        # No match within the resolved artifact -- fall through.

    all_entities = [e for artifact in manifest.get("artifacts", []) for e in artifact["entities"]]
    whole_matches = _match_entities_by_name(all_entities, entity_reference)

    if len(whole_matches) == 1:
        entity = whole_matches[0]
        entity_artifact_path = manifest.get("entity_index", {}).get(entity["id"], {}).get("artifact_path")
        artifact_matches_entity = (
            resolved_artifact_path is not None and entity_artifact_path == resolved_artifact_path
        )
        return (
            resolved_artifact_path,
            artifact_matches_entity,
            EntityResolution(
                status="resolved",
                entity_id=entity["id"],
                entity_type=entity["type"],
                basis="exact_name_whole_manifest_fallback",
            ),
        )
    if len(whole_matches) > 1:
        return (
            resolved_artifact_path,
            False,
            EntityResolution(
                status="ambiguous",
                entity_id=None,
                entity_type=None,
                candidate_entity_ids=[e["id"] for e in whole_matches],
                basis="multiple_entities_share_this_name_across_the_estate",
            ),
        )

    return (
        resolved_artifact_path,
        False,
        EntityResolution(
            status="unresolved",
            entity_id=None,
            entity_type=None,
            basis="no_matching_entity_name_found_anywhere_in_the_manifest",
        ),
    )


def parse_and_resolve_findings(findings: list[Finding], manifest: dict) -> list[ResolvedFinding]:
    """Resolve every finding in an agent's output against one rendering
    manifest. Order-preserving; finding_id reflects list position."""
    resolved: list[ResolvedFinding] = []
    for index, finding in enumerate(findings):
        artifact_path, artifact_matches_entity, entity_resolution = resolve_entity_reference(
            manifest, finding.artifact_reference, finding.entity_reference
        )
        resolved.append(
            ResolvedFinding(
                finding=finding,
                finding_id=f"finding-{index:03d}",
                artifact_resolved=artifact_path is not None,
                resolved_artifact_path=artifact_path,
                artifact_matches_entity=artifact_matches_entity,
                entity_resolution=entity_resolution,
            )
        )
    return resolved
