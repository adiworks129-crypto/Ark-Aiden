# Ark — Wiring the HTTP Connector Validator Into the Rendering Pipeline

## Scope actually done

Made `ark/validation/mulesoft_http_connector.py`'s validator a standing,
automatic step in `ark/experiment/runner.py`, attached to
`EvaluationReport` as a new, additive, sibling field
(`rendering_validation`) — never folded into `agent_performance`,
`research_hooks`, or any other agent-performance metric. The validator and
its schema were **called, never edited**.

## 1. Granularity decision (checked against actual code, not assumed)

Read `ark/adapters/mulesoft/renderer.py` and `adapter.py` directly.
Confirmed: `MuleSoftAdapter` renders exactly **one combined XML file per
Application** (`render_application_xml`'s own docstring: "One combined XML
file per application"; `adapter.py` uses one path,
`f"{app.name}/src/main/mule/{app.name}.xml"`, per app — never per flow).
`_render_http_connector_configs()` always emits every
`http:listener-config`/`http:request-config` global element into that
*same* file, alongside every flow referencing it — including
`ApiCallStep`s that target a *different* Application's API: the
`http:request` element and the `http:request-config` it points at are
both still rendered into the *calling* app's own file, since both
`_render_step`'s `ApiCallStep` branch and `_render_http_connector_configs`
are scoped to one `app` parameter at a time.

**Decision: global config and usage never split across files for this
adapter today, so per-file validation (one call to
`validate_http_connector_xml` per `.xml` artifact, no cross-file
concatenation) is correct and sufficient.** This is enforced with an
executable test (`TestGranularityDecisionHoldsForRealOutput`, in the new
test file), not just asserted in prose — it walks every real Milestone-1
`.xml` artifact and confirms every `config-ref` resolves to a global
element declared in that same file. If a future adapter or renderer change
ever splits them across files, this assumption needs revisiting — flagged
in `ark/validation/pipeline.py`'s own docstring, not silently assumed to
keep holding.

Only `.xml` artifacts are validated — the adapter's `.yaml` API-metadata
files aren't Mule XML and have no HTTP-connector elements.

## 2. Where it's wired in

New file `ark/validation/pipeline.py` (does not touch the validator or
schema):

- `validate_rendered_estate(rendered)` — runs `validate_http_connector_xml`
  against every `.xml` artifact, returns a `RenderingValidationSummary`.
- `validate_rendered_estate_safe(rendered)` — same, but catches *any*
  exception and degrades it to `validation_error`, so a bug in this wiring
  itself can never crash a trajectory.

`ark/experiment/runner.py`'s `run_trajectory_spec_with_artifacts()` calls
`validate_rendered_estate_safe(rendered)` right after `run_agent_harness`
(same tier as `manifest`/`ledger`/`transformed_estate` — used strictly
after the agent has already produced its output, never shown to it), and
only when the resolved adapter is `MuleSoftAdapter` (a non-MuleSoft
adapter gets `rendering_validation=None` rather than nonsense results from
a validator that assumes Mule XML shapes). The result is threaded through
`evaluate()` as a new optional keyword argument and into
`assemble_report()`.

## 3. Additive field placement

`EvaluationReport.rendering_validation: RenderingValidationSummary | None
= None` — a new field on `EvaluationReport`, a sibling to
`agent_performance`, never nested inside it. `orchestrator.py`'s
`evaluate()` never imports `ark.validation` or runs any validator itself —
`rendering_validation` is typed `Any` there (matching the existing
`generation_manifest: Any` pattern) and simply threaded through, keeping
`evaluate()`'s technology-independence and its "four required parameters"
isolation boundary completely unchanged. `report.py` does import the
concrete `RenderingValidationSummary` type (documented as the one
deliberate exception to its "never imports `ark.adapters`" promise — it
imports `ark.validation`, not `ark.adapters`, so that promise is
unbroken), purely to store and serialize it verbatim; nothing in
`report.py` reads its contents to compute anything else.
`report_from_dict()` reconstructs it with `.get(...)`, so historical
reports serialized before this field existed still reconstruct cleanly
with `rendering_validation=None`.

## 4. Non-blocking-failure decision — confirmed as recommended, not changed

Validation failures never block or fail a trajectory. Two distinct
failure modes, both non-blocking:

- **Content issues** (e.g. a dangling config-ref): `validate_http_connector_
  xml` already converts these into `ValidationIssue`s, never an exception.
  Confirmed end-to-end: deliberately breaking the renderer (monkeypatching
  `_render_http_connector_configs` to emit nothing, reproducing the exact
  bug the prior session fixed) still produces a complete `EvaluationReport`
  with the issue visible in `rendering_validation`, and
  `agent_performance`/`research_hooks` are byte-for-byte identical to a
  clean run with the same agent output — proving validation content can
  never leak into agent-performance scoring.
- **Pipeline-side failures** (a bug in the wiring itself, not the XML):
  `validate_rendered_estate_safe()` catches any exception and records it in
  `validation_error` instead of propagating. Confirmed both as a unit test
  (a malformed input that would raise `AttributeError`) and end-to-end
  (patching the runner's call to simulate an internal failure) — the
  trajectory still completes normally either way.

This matches the task's recommended default exactly; no reason found to
choose differently.

## 5. Tests

New file `tests/test_http_connector_pipeline_wiring.py` (12 tests, all
using the existing Milestone 1 hand-authored estate — no new trajectory
batches run):

- Granularity decision, checked against real output.
- Validation runs automatically; result attached to
  `EvaluationReport.rendering_validation`.
- Milestone 1 estate has zero **config-ref** issues via the automatic
  pipeline path (see the important scoping note below).
- `rendering_validation` structurally lives only on `EvaluationReport`,
  never inside `agent_performance`.
- The agent harness call site never receives anything
  validation-shaped (extends `test_milestone7.py`'s existing isolation
  pattern).
- A non-MuleSoft adapter gets `rendering_validation=None`.
- Deliberately-broken rendering surfaces as a non-blocking issue, not a
  crash, and doesn't change a single agent-performance number.
- An internal validator-side exception degrades to `validation_error`
  rather than propagating, both at the wiring level and the unit level.
- `rendering_validation` survives a JSON round-trip; a report dict
  missing the key entirely (pre-existing/backward-compat case)
  reconstructs with `rendering_validation=None`.

Full suite: **287 → 299 tests, all 12 additions, 0 failures** (1
pre-existing, unrelated skip).

## Important scoping note carried forward from the prior session

Milestone 1's real output is confirmed to have **zero config-ref issues**
via the new automatic pipeline path — this is the literal thing this
session's renderer fix + wiring guarantee, and what the tests assert.
It is **not** fully `is_valid`/zero-issues-overall: the separate,
pre-existing, still-unfixed `doc:name` attribute-namespace bug in the
validator (flagged as a follow-up in the prior renderer-fix session, out
of scope for both that session and this one per the "DO NOT TOUCH" list)
still produces a small number of non-config-ref issues on real output.
Asserting full validity here would have been a false claim; the tests and
this summary are scoped to config-ref issues specifically, matching the
prior session's own honest scoping exactly. Still a good candidate for a
future, separate session to fix.

## Confirmed unchanged

`ark/validation/mulesoft_http_connector.py` and
`ark/schemas/mulesoft/http_connector.json` (docstrings updated to reflect
that they're now called from the pipeline; zero logic changes). The six
mutation operators, complexity score formula, evaluator core metrics
(category F1, entity localization accuracy, Brier/ECE), the structural
agent/ground-truth boundary, both golden-file tests from the prior
session, and existing `experiment_analysis.json`/`report_example.json`/
`analysis_example.json` exports. No new trajectory batches were run.
