# Milestone 4 example transformation trajectory

Baseline: `examples/milestone1/ground_truth.json` (the hand-authored 4-application
"Order Management" estate). `generate_examples.py` applies each non-trivial profile
(Level 1-3) to that same baseline with a fixed seed (1), producing:

| File | What it is |
|---|---|
| `transformed_level_1_minor.json` | Baseline + 3 mutations (naming drift, doc decay only) |
| `transformed_level_2_structural.json` | Baseline + 6 mutations (adds duplicate flows, dependency changes) |
| `transformed_level_3_legacy.json` | Baseline + 10 mutations (adds legacy versions, schema inconsistency) |
| `ledger_level_*.json` | The full answer key for the corresponding transformed estate |

The baseline itself is never modified — it's the same file Milestone 1 already commits.

## Walking through the Level 3 ledger

These are real entries from `ledger_level_3_legacy.json` (seed 1), not illustrative examples:

- **`level_3_legacy-seed1-000` / `-002` (`documentation_decay`)**: `step-inventory-build-response`'s
  description went `"Builds the JSON response describing current stock levels for the requested
  SKU."` → `"TODO: document this step."` → `""`. The same step was hit twice in this trajectory —
  the second decay had to escalate past the placeholder stage since the placeholder was already in
  place (see "a bug this caught," below).
- **`-001` / `-003` (`dependency_change`)**: `step-process-verify-customer` was repointed from
  `api-customer-system-v1` to `api-order-status-experience-v1` and then, three steps later, back to
  `api-customer-system-v1` — a real consequence of compounding: later mutations operate on
  whatever the trajectory currently looks like, including earlier mutations' own changes.
- **`-004` (`schema_inconsistency`)**: `orderId` in `step-order-status-build-response`'s DataWeave
  became `order_id` (snake_case) — a plausible schema-consistency bug between components that
  should share a naming convention.
- **`-005` (`legacy_version_introduction`)**: added `api-customer-system-v1-legacy5` pointing at a
  new frozen flow `flow-customer-get-main-legacy5` — the original `api-customer-system-v1` and its
  flow are untouched; this is purely additive.
- **`-006` (`naming_drift`)**: `api-customer-system-v1`'s display name went `"Customer System API"`
  → `"Customer"` (two compounding styles: convert-to-camel-ish, then abbreviate). The API's `id`
  never changed — every reference to it elsewhere in the estate still resolves.

## A bug this process caught (worth knowing about)

While building this, the engine's validity checks caught three separate cases where an operator's
"mutation" produced an *identical* before/after state — a genuine no-op masquerading as a logged
mutation. All three were naming/content operators computing a value that could coincidentally match
what was already there (e.g. `naming_drift`'s style functions assumed dash-separated names, which
silently no-op on API names like `"Customer System API"`; `documentation_decay` and
`schema_inconsistency` could re-select a decay stage or field style the same step was already at
from an earlier mutation in the same trajectory). All three were fixed with an escalation guard
(if the computed result doesn't actually differ, deterministically move to a stage that does), and
the engine now hard-fails (`MutationEngineError`) if this class of bug ever recurs — it's not just
fixed for these three operators, it's structurally guarded against for any future one.

## Regenerating

```bash
PYTHONPATH=. python3 examples/milestone4/generate_examples.py
```
