# Ark — Renderer Fix: Missing Global Config Elements for HTTP Connector

## Scope actually done

Renderer-only fix, per the task spec. The validator was **not wired into
the pipeline** (deferred, separate future task) and the validator/schema
files (`ark/schemas/mulesoft/http_connector.json`,
`ark/validation/mulesoft_http_connector.py`) were **not modified at all**.

## The bug

`MuleSoftAdapter` emitted `config-ref="..."` attributes on
`<http:listener>`/`<http:request>` elements but never emitted the
`<http:listener-config>`/`<http:request-config>` global elements those refs
are supposed to resolve to. Every artifact Ark rendered had dangling
config-refs — confirmed with the existing validator against real
Milestone-1 output before making any change.

## The fix

`ark/adapters/mulesoft/renderer.py`:

- New `_render_http_connector_configs(app)` emits one `http:listener-config`
  (wrapping an `http:listener-connection`) per distinct
  `listener_config_ref` name used anywhere in the app's flows, and at most
  one `http:request-config` (wrapping an `http:request-connection`), only if
  the app has at least one `ApiCallStep`. Shared config-ref names correctly
  produce exactly one global element, not one per flow/usage.
- New placeholder constants `_LISTENER_HOST = "0.0.0.0"`,
  `_LISTENER_BASE_PORT = 8081` (incrementing per distinct listener name),
  `_REQUEST_HOST = "localhost"`, `_REQUEST_PORT = 8082` — adapter-side
  rendering plumbing, not ground-truth concepts (confirmed via grep: no
  host/port/base_url field exists anywhere in `ark/core/models.py` or
  `ark/generator/`). Values mirror docs.mulesoft.com's own reference
  examples rather than being invented.
- `render_application_xml()` inserts the new global config lines before the
  flow loop.
- No authentication, TLS, or reconnection-strategy generation added — out
  of scope per the task, left for a future session.

## Before/after: existing validator against real Milestone-1 output

Unmodified validator, run against the actual `MuleSoftAdapter` output for
`examples/milestone1/ground_truth.json`:

| Artifact | config-ref issues (before) | config-ref issues (after) |
|---|---|---|
| order-status-experience.xml | 2 | 0 |
| order-processing-process.xml | 4 | 0 |
| inventory-system.xml | 1 | 0 |
| customer-system.xml | 1 | 0 |
| **Total** | **8** | **0** |

Config-ref issues: 8 → 0. This is the task's literal definition of done.

## Newly-exposed, unfixed follow-up finding (not in scope here)

With config-ref issues gone, the validator's `_check_attributes()` now
surfaces a separate, previously-masked bug: it compares raw attribute keys
against the schema's plain (unprefixed) names, but ElementTree resolves
prefixed attributes like `doc:name` to their fully-qualified
`{namespace-uri}name` form, so every `<http:request>` with a `doc:name`
(nearly all of them) gets flagged as an "unknown attribute." This bug
already existed; it was simply masked by the more prominent config-ref
issue before. Per this task's explicit instruction not to touch the
validator, it was left alone and is recorded here as a candidate for a
future session — same pattern as how the config-ref bug itself was flagged
as a follow-up by the prior session.

Residual "other" issue counts (all doc:name, not config-ref):
order-status-experience.xml: 1, order-processing-process.xml: 3,
inventory-system.xml: 0, customer-system.xml: 0.

## Tests

- `tests/test_milestone2.py`: added `TestHttpConnectorConfigRefsResolve`
  (5 new tests — every config-ref resolves across all Milestone-1 and
  Milestone-0 artifacts, shared names produce exactly one global element,
  apps with no outbound calls get no request-config, and the validator
  reports zero config-ref issues against real output). Also rewrote the
  Milestone-0-vs-general-adapter golden comparison test to assert the
  general adapter's output equals the frozen hand-authored file with
  exactly the expected listener-config block inserted and nothing else
  different (see below for why plain equality no longer holds).
- `tests/test_http_connector_validation.py`: rewrote
  `TestAgainstRealArkRenderedOutput` — it used to assert dangling
  config-refs were present (accurate at the time); now asserts they are
  gone, without over-claiming the artifact has zero issues overall (it
  still has the unrelated doc:name issues noted above).
- Golden files regenerated: `tests/golden/milestone0/.../order-status-service.xml`
  and all four `tests/golden/milestone1/.../*.xml` files now include the new
  global config elements. Corresponding `manifest.json` files confirmed
  byte-identical (config elements are adapter-side plumbing, not
  ground-truth entities).
- `examples/milestone0/expected_render.xml` — left untouched (reverted after
  a false start). This file is shared between `tests/test_milestone0.py`
  (which exercises the deliberately frozen, historical, out-of-scope
  `examples/milestone0/render.py` one-off script) and
  `tests/test_milestone2.py` (which exercises the fixed general adapter).
  Since only the general adapter was fixed, byte-identical comparison
  against one shared file can't hold for both; the file stays pinned to the
  frozen script, and the general-adapter test was updated instead (see
  above) to assert the fix inserted exactly the expected block and nothing
  else.

Full suite: **287 tests, 0 failures** (up from 282 before this session's 5
new tests; 1 pre-existing skip, unrelated).

## Confirmed unchanged

Evaluator metrics, structural agent/ground-truth boundary, the six mutation
operators and Level 0–3 groupings, complexity score formula, validator and
schema files, existing `experiment_analysis.json` exports, mutation
engine/pipeline wiring (validator still not called from `ark/adapters`,
by design).
