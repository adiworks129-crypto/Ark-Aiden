"""
Normalized Issue layer — Milestone 6.1.

The mutation ledger (ark/mutation/ledger.py) is the authoritative record of
every transformation operator application, but it is not what an agent
should be scored against directly. Ark_Evaluator_Design.md Section 2 spells
out why:

- Multiple ledger records can affect the same entity (compounding). An
  agent inspecting the final rendered artifacts sees one final state, not
  a sequence of edits — it cannot and should not be expected to report
  "two separate decay events" for one step's description.
- Some compounding nets out to *no observable difference at all*. The
  Milestone 4 example ledger contains exactly this case:
  `step-process-verify-customer`'s `target_api_id` was changed away from
  `api-customer-system-v1` and then changed back to it three steps later
  (see examples/milestone4/README.md). The final rendered artifact is
  identical to the untouched baseline for that field — there is nothing
  for an agent to observe, so this must not become a scoreable Issue, even
  though the ledger legitimately recorded two real mutation events.

derive_issues() is the single function that turns a MutationLedger into a
deduplicated list of Issue objects representing only what is actually
different in the final transformed estate, one entry per (transformation
type, affected-entity-set) group — the unit an agent's findings should be
matched against.

Nothing in this module is exposed to the evaluated agent. Issue and
TransformationHistoryEntry are evaluator-internal/audit objects; the
agent-visible contract lives entirely in schema.py and shares no fields
with these (no mutation_id, no rationale, no internal entity ids beyond
what the manifest independently already exposes in Milestone 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ark.mutation.ledger import MutationLedger, MutationRecord

ISSUE_SCHEMA_VERSION = "0.1.0"


@dataclass
class TransformationHistoryEntry:
    """One raw ledger record that contributed to an Issue's final
    observable state. Kept for audit/traceability only (Ark's "no hidden
    assumptions" principle: every score should be explainable back to the
    exact ledger records that produced it) — never serialized into
    anything the evaluated agent sees."""

    mutation_id: str
    sequence_index: int
    original_state: dict
    transformed_state: dict
    severity: float
    rationale: str


@dataclass
class Issue:
    """One evaluator-ready, deduplicated observable issue: the unit an
    agent's findings are scored against (matching itself is Milestone
    6.2's matcher.py; this is just the normalized representation it will
    match against).
    """

    issue_id: str
    """Deterministic: f"{transformation_type}:{'+'.join(sorted(affected_entity_ids))}"
    over the FINAL surviving entity set (see derive_issues), so it's stable
    across re-runs and independent of ledger record ordering."""
    issue_type: str
    """Matches a MutationRecord.transformation_type value — one of Ark's
    six operator names. Always a member of schema.py's ISSUE_TYPE_TAXONOMY
    minus "other" (ground-truth issues are never "other"; that value only
    exists for agent-supplied issue_type values outside the taxonomy)."""
    affected_entity_ids: list[str]
    """Only entities with a genuine NET observable difference between the
    earliest known prior state and the final transformed state — entities
    whose contributing records cancelled out entirely are dropped, see
    derive_issues()."""
    observable_symptom: dict
    """entity_id -> {field: final_value}, containing only fields with a
    genuine net change. This is the *cumulative* end state (last-writer per
    field across all contributing records), not any single record's
    transformed_state — matching what an agent inspecting only the final
    rendered artifacts could ever see."""
    severity: float
    """max(severity) across every contributing record in this issue's
    group — a conservative "worst observed" rollup (Ark_Evaluator_Design.md
    Section 2)."""
    expected_detection_target: str
    """Plain-language description of what a correct agent finding should
    identify. Evaluator/audit-facing only: not part of the agent-visible
    contract, and not used by any matching logic (matching is on
    issue_type + entity id, never free text)."""
    transformation_history: list[TransformationHistoryEntry] = field(default_factory=list)
    issue_schema_version: str = ISSUE_SCHEMA_VERSION

    @property
    def mutation_count(self) -> int:
        """How many raw ledger records compounded into this one Issue.
        Feeds the complexity model's compounding factor directly
        (complexity.py)."""
        return len(self.transformation_history)


def _group_key(record: MutationRecord) -> tuple[str, tuple[str, ...]]:
    return (record.transformation_type, tuple(sorted(record.affected_entity_ids)))


def _cumulative_original(entity_records: list[MutationRecord], entity_id: str) -> dict | None:
    """Merge original_state[entity_id] across entity_records with
    "earliest record wins" per field. Returns None if the entity's very
    first contributing record shows it didn't exist yet (a creation
    event) — in that case there is no meaningful "prior state" to diff
    against at all."""
    first = entity_records[0]
    if first.original_state.get(entity_id) is None:
        return None

    merged: dict = {}
    for record in reversed(entity_records):
        orig = record.original_state.get(entity_id)
        if orig is not None:
            merged.update(orig)
    return merged


def _cumulative_transformed(entity_records: list[MutationRecord], entity_id: str) -> dict:
    """Merge transformed_state[entity_id] across entity_records with
    "latest record wins" per field — the final observable state."""
    merged: dict = {}
    for record in entity_records:
        trans = record.transformed_state.get(entity_id)
        if trans is not None:
            merged.update(trans)
    return merged


@dataclass
class IssueDerivationDiagnostics:
    """Milestone 6.4 addition: what derive_issues() silently discards,
    surfaced for reporting purposes. Added alongside derive_issues() (not
    inside it) so the existing function's signature and behavior are
    completely unchanged — this is purely additive.

    Ark_Architecture_and_Plan.md's evaluator write-up already documents
    that some raw mutation groups (e.g. a dependency_change reverted later
    in the same trajectory) cancel out to no observable difference and
    produce no Issue. Until this milestone there was no way to know
    afterward how many groups that happened to, without re-deriving the
    grouping logic a second time somewhere else — which would have created
    a second, drift-prone copy of derive_issues()'s private grouping rule.
    This dataclass is that missing number, computed by the SAME pass
    derive_issues() already does, not a separate recomputation.
    """

    total_groups: int
    """Every distinct (transformation_type, affected_entity_ids-as-a-set)
    group found in the ledger, before net-zero filtering."""
    surviving_issue_count: int
    """== len(derive_issues(ledger)) for the same ledger -- included here
    too so a caller with only the diagnostics object still has this
    number without needing to call derive_issues() separately."""
    net_zero_group_count: int
    """Groups where every entity's net change came out empty -- the
    "compounding cancels out" case. total_groups - surviving_issue_count."""
    net_zero_groups: list[dict] = field(default_factory=list)
    """One {"transformation_type": ..., "affected_entity_ids": [...]} entry
    per dropped group, for audit -- e.g. reporting.py's Issue Summary can
    show exactly which raw groups were cancelled out, not just a count."""


def _derive_issues_and_diagnostics(
    ledger: MutationLedger,
) -> tuple[list[Issue], IssueDerivationDiagnostics]:
    """The one real implementation behind both derive_issues() and
    derive_issue_diagnostics() below -- see derive_issues()'s docstring
    for the grouping/net-zero rules this applies. Kept private so there is
    exactly one place this logic can ever be edited.
    """
    groups: dict[tuple[str, tuple[str, ...]], list[MutationRecord]] = {}
    order: list[tuple[str, tuple[str, ...]]] = []

    for record in sorted(ledger.records, key=lambda r: r.sequence_index):
        key = _group_key(record)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)

    issues: list[Issue] = []
    net_zero_groups: list[dict] = []

    for key in order:
        transformation_type, grouped_entity_ids = key
        records = groups[key]

        surviving_entity_ids: list[str] = []
        observable_symptom: dict[str, dict] = {}

        for entity_id in grouped_entity_ids:
            entity_records = [r for r in records if entity_id in r.affected_entity_ids]
            cumulative_original = _cumulative_original(entity_records, entity_id)
            cumulative_transformed = _cumulative_transformed(entity_records, entity_id)

            if cumulative_original is None:
                # Creation event: everything in the final state is new,
                # by definition observable.
                net_changed = dict(cumulative_transformed)
            else:
                net_changed = {
                    field_name: value
                    for field_name, value in cumulative_transformed.items()
                    if cumulative_original.get(field_name) != value
                }

            if net_changed:
                surviving_entity_ids.append(entity_id)
                observable_symptom[entity_id] = net_changed

        if not surviving_entity_ids:
            # Every entity in this group cancelled out to no net
            # observable difference (e.g. a dependency_change that was
            # reverted later in the same trajectory) — nothing for an
            # agent to find, so this is not a scoreable Issue.
            net_zero_groups.append(
                {"transformation_type": transformation_type, "affected_entity_ids": list(grouped_entity_ids)}
            )
            continue

        surviving_entity_ids = sorted(surviving_entity_ids)
        severity = max(r.severity for r in records)
        history = [
            TransformationHistoryEntry(
                mutation_id=r.mutation_id,
                sequence_index=r.sequence_index,
                original_state=r.original_state,
                transformed_state=r.transformed_state,
                severity=r.severity,
                rationale=r.rationale,
            )
            for r in records
        ]

        issue_id = f"{transformation_type}:{'+'.join(surviving_entity_ids)}"
        expected_detection_target = (
            f"A '{transformation_type}' issue affecting {surviving_entity_ids}, "
            f"observable as: {observable_symptom}."
        )

        issues.append(
            Issue(
                issue_id=issue_id,
                issue_type=transformation_type,
                affected_entity_ids=surviving_entity_ids,
                observable_symptom=observable_symptom,
                severity=severity,
                expected_detection_target=expected_detection_target,
                transformation_history=history,
            )
        )

    diagnostics = IssueDerivationDiagnostics(
        total_groups=len(order),
        surviving_issue_count=len(issues),
        net_zero_group_count=len(net_zero_groups),
        net_zero_groups=net_zero_groups,
    )
    return issues, diagnostics


def derive_issues(ledger: MutationLedger) -> list[Issue]:
    """Consolidate a MutationLedger's raw records into deduplicated,
    observable Issues.

    Grouping rule: records with the same (transformation_type,
    affected_entity_ids-as-a-set) belong to one Issue — this is a
    deliberate, documented simplification: Ark's six current operators
    each touch a fixed, predictable field (or field set) per
    transformation_type, so entity-set + type is sufficient to identify
    "the same observable thing happening again." A future operator that
    could touch *different* fields on overlapping-but-not-identical entity
    sets across records would need a richer grouping rule than this one;
    flagged here rather than silently assumed to generalize.

    Within each group, entities whose net change (earliest known prior
    value -> final value, merged field-by-field across every contributing
    record) is empty are dropped — this is the "compounding cancels out"
    case (see module docstring). A group where every entity nets to no
    change produces no Issue at all.

    Signature and behavior unchanged since Milestone 6.1 -- see
    derive_issue_diagnostics() below for the Milestone 6.4 addition that
    exposes what this function silently discards.
    """
    issues, _ = _derive_issues_and_diagnostics(ledger)
    return issues


def derive_issue_diagnostics(ledger: MutationLedger) -> IssueDerivationDiagnostics:
    """Milestone 6.4: the same derivation derive_issues() performs, but
    returning what got dropped (net-zero groups) rather than just the
    survivors. Computed via the exact same pass, never a second
    recomputation of the grouping rule."""
    _, diagnostics = _derive_issues_and_diagnostics(ledger)
    return diagnostics
