# Ark — Investigation: Why `domain_implausible_component` Doesn't Appear in the Two Existing Batches

## Conclusion

**Explanation (a): expected, not a bug.** Both batches ran entirely under
`level_3_legacy` (a Level 0–3 profile) against the hand-authored Milestone 1
estate, which has no `domain` set. `domain_implausible_component` was never
eligible to run — not because of any wiring defect, but for two
independent, both-expected reasons: the profile actually used never
includes that operator, and the estate actually used has no domain for it
to check plausibility against even if it had. No code was changed; none was
needed.

## Evidence

1. **The one available individual report**
   (`JSON outputs/level_3_legacy-seed2.report.json`) states directly, in
   `metadata`:
   - `"profile_name": "level_3_legacy"`
   - `"baseline_estate_id": "milestone1-order-management-estate"`
   - `"baseline_schema_version": "0.2.0"`
   - `"rendering_validation"` key is **absent entirely** from the report.

2. **Both aggregate exports'** `experiment_summary.transformation_type_distribution`
   contain only the six original operator names and nothing else:

   | | n=5 (`experiment.analysis.json`) | n=15 (`experiment.analysis (1).json`) |
   |---|---|---|
   | documentation_decay | 5 | 14 |
   | duplicate_processing | 4 | 13 |
   | legacy_version_introduction | 5 | 12 |
   | naming_drift | 4 | 14 |
   | schema_inconsistency | 4 | 10 |
   | dependency_change | 4 | 13 |
   | **domain_implausible_component** | **absent** | **absent** |

   report_count/skipped_report_count (5/0 and 15/0) confirm every single
   trajectory in both batches is accounted for in this table — this isn't
   a partial view.

3. **Cross-checked directly against the current codebase** (read-only —
   no trajectory run, no agent call):
   - `examples/milestone1/ground_truth.json`, loaded via
     `validate_ground_truth()`, has `estate_id ==
     "milestone1-order-management-estate"` (matching the report exactly)
     and `estate.domain is None`.
   - `ark.mutation.profiles.PROFILES["level_3_legacy"].operator_types`
     is the original six-operator tuple; it does not and never did
     include `"domain_implausible_component"` (unchanged since the
     Feature 2 session, which added the new operator only to its own,
     separate `domain_injection_preview` profile — verified again here by
     inspecting the live registry, not just recalling it).
   - `ark.mutation.profiles.PROFILES["domain_injection_preview"]` **is**
     correctly registered in the current codebase, exposing
     `operator_types=("domain_implausible_component",)`, `level=-1`,
     `num_mutations=1` — confirming the profile itself is real and
     available, just never selected for these two batches.
   - `DomainComponentInjectionOperator.find_candidates()` returns `[]`
     for any estate with `domain not in {"finance", "retail"}` (by
     design, documented, and unit-tested in the Feature 2 session) — so
     even in the counterfactual where `level_3_legacy` somehow included
     this operator, it would still have found zero candidates against
     this particular domain-less estate.

The `"rendering_validation"` key's total absence from the individual
report, and `baseline_schema_version: "0.2.0"` (the pre-Feature-2 schema
version, before the 0.2.0 → 0.3.0 bump), both independently corroborate
that these two batches were generated using a codebase snapshot from
**before** Feature 2 existed at all in whatever environment produced
them — not merely "the user picked the wrong profile once." Either way,
the conclusion is identical: nothing to fix, the feature simply hasn't
been invoked yet.

## No defect found — nothing proposed, nothing applied

`ark/experiment/runner.py`'s profile lookup (`PROFILES[spec.profile_name]`,
a plain dict lookup) and `TrajectorySpec` (no restriction on which
registered profile name may be used) both work correctly today, and
`domain_injection_preview` is present and correctly shaped in the live
registry. No wiring gap, typo, or lookup bug was found anywhere in this
path. Per this task's scope, no code was touched.

## What would actually exercise the feature

A future batch would need to explicitly select `profile_name=
"domain_injection_preview"` **and** use an estate with `domain` set to
`"finance"` or `"retail"` (either a hand-tagged copy of an existing estate,
or `GeneratorConfig(domain=...)`) — neither condition was true for either
existing batch. This is a decision for a later, separate session (per this
task's own scope, no new trajectory or agent call was run here).
