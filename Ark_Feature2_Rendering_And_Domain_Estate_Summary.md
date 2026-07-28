# Ark — Feature 2 Follow-Up: ConnectorStep Rendering + a Domain-Tagged Test Estate

## Scope actually done

Closed the two gaps blocking Feature 2 ("organized randomness") from being
exercisable in a real trajectory: `ConnectorStep` now renders into XML, and
a domain-tagged estate is available for testing. Nothing was run through a
real trajectory, and no agent was called.

## 1. ConnectorStep rendering — the fidelity decision

`ark/adapters/mulesoft/renderer.py`'s `_render_step()` gained a
`ConnectorStep` branch. The task's primary suggestion was to model this on
`ApiCallStep`'s outbound-call pattern — but `ApiCallStep` only renders as
`<http:request .../>` because it specifically targets another Ark-modeled,
HTTP-triggered API, whose real path/method ground truth actually supplies.
A `ConnectorStep` names a real-world external system (`connector_type`,
e.g. `"sap_retail_scm"`) that Ark has no path/method/schema for at all —
and in real MuleSoft, every connector (SAP, Salesforce, Database, ...)
defines its own dedicated XML namespace and operations; there is no
generic "call any connector" element to legitimately reuse. Inventing one
(e.g. a fabricated `<sap:...>` tag) would be exactly the kind of
ungrounded syntax the task explicitly ruled out.

So the actual rendering uses only real, generic, already-used-elsewhere
Mule syntax:

```xml
<!-- External connector reference: sap_retail_scm -->
<logger level="INFO" doc:name="Integrate with SAP Retail / Supply Chain Management (SCM)"
        message="..."/>
```

`<logger>` is the same real, core Mule element `LoggerStep` already
renders (and a genuine real-world pattern — logging around a connector
invocation); `doc:name` is the same universal Mule documentation-namespace
attribute already used on `<ee:transform>`/`<http:request>` elsewhere in
this file, not something new. The preceding XML comment makes the
connector type explicit and still fully visible to an agent reading raw
artifact text, without being a fabricated executable element. `_step_entity()`
was extended so `ConnectorStep.name` is used as its manifest label, exactly
like `TransformStep`/`ApiCallStep` already are.

Verified directly (not just asserted): every `.xml` artifact from a
domain-injected estate still parses as well-formed XML via
`xml.etree.ElementTree`, and the rendered text contains no fabricated
connector-specific tag (checked for `<sap:`, `<connector:`, and the
connector_type's own name as a tag prefix).

## 2. Confirmed: the HTTP connector validator ignores it

`ark/validation/mulesoft_http_connector.py` was not modified. Since
`ConnectorStep` renders as `<logger>`/a comment — neither an HTTP-schema
element — `validate_http_connector_xml()`'s existing "elements not in
schema are walked over but never flagged" behavior already covers this
correctly. Two tests confirm it directly: no validator issue's message or
element references the connector step at all, and — the stronger check —
injecting a `ConnectorStep` into an artifact does not change that
artifact's validator issue count at all (before vs. after, same count).

## 3. The domain-tagged test estate — generator path chosen (option b)

Per the task's own preference ("whichever is less duplicative of existing
fixtures"): `GeneratorConfig.domain` already existed from the prior Feature
2 session and already works end-to-end — confirmed here with new tests
generating an estate via `generate_estate(GeneratorConfig(seed=..., domain=
"finance"))`, then running it through `domain_injection_preview`. No new
hand-authored fixture file was added, and the existing Milestone 1
hand-authored `ground_truth.json` was never modified — every test that
needs a domain-tagged copy of it uses `dataclasses.replace(estate,
domain=...)` on an in-memory copy, exactly the pattern the prior Feature 2
session's own tests already established. All existing Milestone 1 golden
tests remain untouched and passing.

## 4. Tests — unit/component level only

New file `tests/test_domain_component_injection_rendering.py` (10 tests):

- Every rendered artifact stays well-formed XML after injection.
- The rendered `ConnectorStep` uses only real, generic Mule syntax (comment
  + `<logger>` + `doc:name`), never a fabricated namespace.
- The new step's manifest entry has the correct name and `Step:connector`
  type; it produces **no** dependency edge (it references an external
  system, not another Ark entity — `build_manifest()` needed no change for
  this to already be correct).
- The HTTP connector validator ignores it, both directly and via a
  before/after issue-count comparison.
- The generator path (`GeneratorConfig.domain`) produces a usable
  domain-tagged estate, and the full chain — injection → render → validator
  → manifest — completes without error on a **generated** estate.
- A structural self-check (mirroring `test_milestone7.py`'s existing
  `TestNoIntegrationsImportUnderArk` pattern) confirms this test file
  itself imports neither `ark.experiment`, `ark.harness`, `integrations`,
  nor `anthropic` — the "no real trajectory, no agent" boundary is
  enforced by an assertion, not just a comment.

Full suite: **338 → 348 tests, all 10 additions, 0 failures** (1
pre-existing, unrelated skip). No existing test needed updating this
session — the renderer change is purely additive (a new `isinstance`
branch), and no existing pin test asserts anything about step-kind
rendering that this new branch touches.

## What deliberately did NOT happen

No trajectory was run through `ark.experiment` (`run_trajectory_spec`/
`run_experiment`), and no agent (scripted, heuristic, or real) was called,
anywhere in this session — every check above used
`ark.mutation.engine.run_trajectory()` and/or
`ark.generator.generator.generate_estate()` directly, the same
zero-agent, mutation-engine-only pattern every prior Milestone 4/Feature 2
test already used.

**After this session, a real trajectory could be run** — generate or
hand-tag a domain estate, run it through `domain_injection_preview`,
render it, and hand the result to a real or scripted agent via
`ark.experiment.run_trajectory_spec`/`run_experiment` — to see whether an
agent actually notices the injected, domain-implausible component. That
has **not** happened yet. It's a separate, deliberate, compute-costing
decision left for the user to make next, exactly as this session's task
spec asked to keep it.
