# Ark: Complete Technical Reference

A ground-up, exhaustive account of every stage of the Ark pipeline — every
data model, every formula, every algorithm, every edge case — as they
exist in the codebase today. This supersedes `Ark_System_Overview.md`
(which stays as a one-page map); this document is the "read the code so
you don't have to" version.

## What Ark is, and why it's built this way

Ark builds small, synthetic "enterprise integration estates" (fake but
structurally realistic API/flow systems, e.g. Mule applications) **with a
known answer key recorded up front, before any imperfection is
introduced.** A mutation engine then deliberately degrades the estate in
controlled, logged ways. The estate is rendered into real target-platform
artifacts (currently MuleSoft XML + API YAML) and shown to an AI agent,
which is asked to find integration problems — with zero access to the
ground truth, the mutation ledger, or the manifest that would let it cheat.
Ark's evaluator scores the agent's findings against the hidden answer key,
and a cross-experiment analysis layer looks for patterns across many runs.
A Streamlit UI drives and displays all of this locally.

The one idea that recurs at every layer: **manufacture the exact answer
key first, then never let the thing being evaluated see it.** Every
design choice below — the separation of `ark/core` from `ark/adapters`,
the harness's `dict[str,str]`-only input, the UI's `logic.py`/`app.py`
split — exists in service of that boundary.

```
Generator (seeded)  or  hand-authored ground_truth.json
        │
        ▼
GroundTruthEstate  (ark/core/models.py — the answer key's foundation)
        │
        ▼
Mutation Engine (ark/mutation)  ──────►  Mutation Ledger (hidden)
        │
        ▼
Adapter / Renderer (ark/adapters/mulesoft)  ──►  Rendered artifacts (XML/YAML)  +  Manifest (hidden)
        │
        ▼
Agent Harness (ark/harness)  ◄── AgentClient (Scripted / Anthropic / Gemini)
        │   (agent sees ONLY rendered artifacts — nothing else, ever)
        ▼
Agent findings (raw JSON text)
        │
        ▼
Evaluator (ark/evaluator): Issues ← ledger  |  Matcher ← manifest  |  Metrics + Calibration + Complexity + Explanation
        │
        ▼
EvaluationReport  (one per trajectory)
        │
        ▼
Cross-Experiment Analysis (ark/evaluator/analysis.py) — many reports → trends
        │
        ▼
Streamlit UI (ark/ui) — config, dashboard, charts, artifact viewer, export
```

---

## 1. Ground truth model — `ark/core`

Everything downstream reads or mutates one object graph, built from plain
stdlib `dataclasses` (`from __future__ import annotations`) — a
deliberate zero-third-party-dependency choice for the core schema.
`SCHEMA_VERSION = "0.3.0"`; `SUPPORTED_SCHEMA_VERSIONS = {"0.1.0", "0.2.0", "0.3.0"}`
(older exports still validate).

**`GroundTruthEstate`**: `estate_id`, `schema_version=SCHEMA_VERSION`,
`applications: list[Application]`, `domain: Literal["finance","retail"] | None = None`
(added in the domain-injection feature; optional, defaults to `None`).

**`Application`**: `id`, `name`, `apis: list[API]`, `flows: list[Flow]`.

**`API`**: `id`, `name`, `version` (deliberately a free-form string, not
an int), `entry_flow_id`.

**`Flow`**: `id`, `name`, `flow_type: "flow"|"sub_flow" = "flow"`,
`trigger: Trigger | None`, `steps: list[Step]`.

**Triggers** — `HttpListenerTrigger` (`path`, `method`, `listener_config_ref`)
or `SchedulerTrigger` (`cron_expression`, `description`). A `"flow"` must
have a trigger; a `"sub_flow"` must not — enforced at validation time, not
just convention.

**Steps** (five kinds, tagged by `kind`):
- `TransformStep` — `id, name, description, dataweave`. Represents a
  DataWeave transform.
- `FlowRefStep` — `id, target_flow_id`. Resolves only **within the same
  Application** (a hard scoping rule enforced by referential-integrity
  checks).
- `LoggerStep` — `id, message, level` (`TRACE/DEBUG/INFO/WARN/ERROR`,
  default `INFO`).
- `ApiCallStep` — `id, name, description, target_api_id`. The **only**
  place a cross-application dependency lives — it resolves against the
  whole estate's API registry, not just the owning app's.
- `ConnectorStep` — `id, name, description, connector_type`. Added for
  domain-conditioned component injection; `connector_type` is a key into
  `domain_plausibility.json`'s component catalog, deliberately **not**
  cross-checked against that catalog by `validate.py` (checking it would
  invert the intended one-way dependency direction, `ark.generator →
  ark.core`, not the reverse) — only checked for being a non-empty string.

### Validation (`ark/core/validate.py`)

Two distinct jobs, kept apart on purpose: **structural parsing**
(`_parse_*` functions, one per entity type, each collecting errors into a
shared list rather than raising on the first problem) and **referential
integrity** (`_check_referential_integrity`, run only after structural
parsing succeeds).

`validate_ground_truth(path)` reads a JSON file: checks
`schema_version` against `SUPPORTED_SCHEMA_VERSIONS` first (raises
immediately, before any structural work, if unsupported); then parses;
then checks referential integrity; raises `GroundTruthValidationError`
(constructed from the full accumulated error list, never just the first
one found) if either stage produced errors. `validate_estate_object(estate)`
is the mutation engine's per-step guard — it runs *only* referential
integrity on an already-parsed object, returning the error list rather
than raising, so the mutation engine can decide what to do with it.

Referential-integrity rules, all checked in one pass: every id
estate-wide must be unique (`Duplicate id '...' — every id in an estate
must be unique.`); `flow_type=="flow"` requires a trigger and
`flow_type=="sub_flow"` forbids one; `FlowRefStep.target_flow_id` must
resolve to a flow **in the same Application**; `ApiCallStep.target_api_id`
must resolve **anywhere in the estate** (a pre-pass computes
`all_api_ids` across every application first, specifically to give
`ApiCallStep` this wider scope); `API.entry_flow_id` must resolve within
its own app. `domain`, if present and not in `("finance", "retail")`,
produces an error string but the estate is still constructed with
`domain` reset to `None` (not aborted) — the same "never invent behavior
you can't stand behind" instinct that appears everywhere else in this
codebase.

`estate_to_dict()`/`estate_to_json()` (`ark/core/serialize.py`) are the
literal inverse: `dataclasses.asdict(estate)` — no separate schema logic
duplicated here.

---

## 2. Generator — `ark/generator`

Builds a synthetic estate from an integer seed instead of hand-authoring
one, deterministically: same seed, same estate, forever (assuming the
generator's own logic doesn't change).

### `GeneratorConfig`

```
seed: int                              (required — the sole randomness source)
num_experience_apis: int = 1
num_process_apis: int = 1
num_system_apis: int = 2
dependency_density: float = 0.6        (fan-out into the next layer down)
shared_component_frequency: float = 0.5
scheduled_job_ratio: float = 0.3
topology_style: str = "layered"        (only value supported today)
naming_style: str = "kebab-case"       (only value supported today)
vocabulary_domain: str = "enterprise_default"  (only value supported today)
estate_id_prefix: str = "generated"    (→ estate_id = f"{prefix}-seed{seed}")
domain: str | None = None              ("finance" / "retail" / None)
```

`__post_init__` validates every field, **collects all violations, then
raises once** as `GeneratorConfigError`, rather than failing on the first
bad field: app counts must each be `>= 0` and sum to `> 0`; the three
probability knobs (`dependency_density`, `shared_component_frequency`,
`scheduled_job_ratio`) must each be in `[0.0, 1.0]`; `topology_style`/
`naming_style`/`vocabulary_domain` must be in their (currently
single-valued) supported sets; `domain`, if given, must be `"finance"` or
`"retail"`.

### Randomness discipline — `ark/generator/seeds.py`

One explicit `random.Random` instance is threaded through everything;
Python's global `random` module state is never touched, and every helper
takes/returns **lists**, never sets — Python's `set` iteration order for
strings isn't stable across process runs (hash randomization), which
would silently break the seed → identical-estate guarantee.

- `make_rng(seed) → random.Random(seed)`.
- `draw_nouns(rng, vocabulary, k)`: shuffles a copy of `vocabulary` once
  (`rng.shuffle`), then walks it — if `k <= len(vocabulary)` returns the
  first `k` distinct entries; if `k` exceeds the vocabulary size, it
  **cycles back through the same shuffled order again with a numeric
  suffix** (`f"{noun}{cycle+1}"`) rather than raising, so large estates
  degrade gracefully instead of failing. Exactly one `rng.shuffle` call is
  consumed regardless of `k` (even `k=0`), keeping rng-state consumption
  uniform.
- `sample_subset(rng, population, density)`: `[]` immediately for an
  empty population (no rng consumed); otherwise
  `k = max(1, min(len(population), round(density * len(population))))`
  and `rng.sample(population, k)` — note the `max(1, ...)`: even
  `density=0.0` still returns **at least one** item, and `density=1.0`
  returns the whole population (permuted).
- `decide(rng, probability) → rng.random() < probability` — a single
  Bernoulli draw; `probability=0.0` is always `False`, `probability=1.0`
  is always `True` (since `rng.random()` is drawn from `[0.0, 1.0)`).

### Topology — `ark/generator/topology.py`

A strictly feed-forward three-layer graph: **experience → process →
system**. System apps are always leaves (call nothing); edges only ever
point one layer down, never sideways or upward, so `dependency_density`
can never produce a fully-meshed or cyclic graph no matter how high it's
set.

`build_topology(config, rng)`, in exact order (this order matters for
determinism — it fixes the rng-consumption sequence):
1. Raises `NotImplementedError` if `topology_style != "layered"`.
2. Draws names **layer by layer**, each an independent `draw_nouns` call
   against the *same* generic vocabulary list (see below) — so
   experience/process/system app names are not thematically related to
   each other; only the layer changes the id/label template, not the noun
   pool.
3. Builds every `AppSpec` (`key`, `layer`, `noun`,
   `has_secondary_flow=False`, `shares_subflow_across_flows=False`).
4. Builds dependency edges, iterating apps in creation order: each
   experience app samples a subset of process-app keys via
   `sample_subset(rng, process_keys, dependency_density)`; each process
   app samples a subset of system-app keys the same way; system apps get
   no edges at all.
5. A secondary-flow pass, again over all apps in order, but **only** for
   process/system apps (experience apps are skipped, consuming zero rng
   calls for this step): `has_secondary_flow = decide(rng,
   scheduled_job_ratio)`; if true, `shares_subflow_across_flows =
   decide(rng, shared_component_frequency)` (this second draw only
   happens conditionally on the first).

### Vocabulary and naming — `ark/generator/vocabulary.py`

There is **one** generic 16-word noun list (order, invoice, catalog,
pricing, etc.) — there is no separate finance/retail noun vocabulary.
`GeneratorConfig.domain` does **not** change which nouns get drawn; it
only gets attached directly to `GroundTruthEstate.domain` for the
mutation engine to read later (see §3.7). All domain-specific *content*
(the finance/retail component catalog) lives entirely in
`domain_plausibility.json`.

Id/name templates are pure string formatting (no randomness): app ids are
kebab-case (`app-{noun}-{layer}`), API names are Title Case With Spaces
(`{Noun} {Layer} API`) — this asymmetry between kebab-case entity ids/
names and Title-Case API names is deliberate, and is exactly the
structural signature `NamingDriftOperator`'s style functions (and the
scripted agent's own heuristic) are built around. DataWeave payloads
follow a fixed pattern too: process-layer transforms emit
`{noun}Id: payload.id`; every other layer emits `{noun}Id:
attributes.uriParams.id` — this `<word>Id:` field naming convention is
exactly what `SchemaInconsistencyOperator` later targets.

### `generate_estate()` — end to end

1. `rng = make_rng(config.seed)`.
2. `plan = build_topology(config, rng)` — **all randomness in the entire
   generation process is consumed here**; nothing below this line touches
   `rng` again.
3. Builds every `Application` from the topology plan, purely via
   deterministic string formatting: an entry flow (HTTP-triggered, one
   `ApiCallStep` per downstream dependency + one `TransformStep` + one
   `FlowRefStep` to a primary sub-flow — step *ordering* differs by layer:
   experience puts calls before the transform, process puts the sub-flow
   ref first, system has no calls at all), a primary sub-flow (a
   `TransformStep` for process apps, a `LoggerStep` for experience/system
   apps), and, if `has_secondary_flow`, a scheduler-triggered secondary
   flow (cron `"0 2 * * *"`) either sharing the primary sub-flow or
   getting its own distinct one, per the topology plan's earlier decision.
4. `GroundTruthEstate(estate_id=f"{prefix}-seed{seed}", applications=...,
   domain=config.domain)` — **this is the only line in the entire
   generator that ever touches `domain`**, a direct, untransformed
   pass-through from the config.
5. Returns `GeneratedEstate(estate, GenerationManifest(seed,
   generator_version, schema_version, config=dataclasses.asdict(config)))`.

### Domain plausibility mapping — `ark/generator/domain_plausibility.py` + `.json`

`SUPPORTED_DOMAINS = ("finance", "retail")` — deliberately re-declared
(not imported) in three separate places (`ark/core/validate.py`'s
`_VALID_DOMAINS`, `ark/generator/config.py`'s `SUPPORTED_DOMAINS`, and
here) to avoid inverting the `ark.core`/`ark.generator` dependency
direction; a dedicated test cross-checks all three stay in sync.

`load_domain_plausibility()` reads the JSON and validates its shape
(non-empty `"domains"` dict; each entry's `"plausible_components"`
non-empty; each component has `key`/`display_name`/`justification`) —
raising `DomainPlausibilityError` only for a malformed file, never for an
unrecognized domain name (that's a plain `KeyError` from
`plausible_components_for()`, by design — a validity check belongs
upstream, in `validate.py`/`config.py`, not duplicated here).

The mapping itself is deliberately small — exactly two domains, exactly
three components each, chosen for high-confidence domain-exclusivity
(components like payment gateways that both retail and finance
plausibly use were explicitly excluded, per the file's own
`justification` fields):

| Domain | Components |
|---|---|
| finance | `core_banking_platform` (Temenos T24 / FIS Profile / Finastra Fusion), `interbank_payment_rail` (SWIFT / ACH / Fedwire), `regulatory_compliance_reporting` (AML/KYC, Basel/Dodd-Frank) |
| retail | `sap_retail_scm` (SAP Retail/SCM), `pos_integration`, `warehouse_management_system` (WMS/logistics) |

---

## 3. Mutation Engine — `ark/mutation`

Applies realistic, imperfection-introducing transformations to an estate
and records every single change in a **Mutation Ledger** — this ledger
*is* the hidden answer key; nothing downstream of rendering ever sees it.

### Shared machinery — `ark/mutation/base.py`

`clone_estate()` is `copy.deepcopy` — every operator works on a fresh,
independent copy. `find_application`/`find_flow`/`find_step` are lookup
helpers that raise a descriptive `KeyError` on a miss rather than
returning `None`. `MutationRecordDraft` (`transformation_type`,
`affected_entity_ids`, `original_state`, `transformed_state`, `rationale`)
is what an operator returns; the engine fills in the remaining bookkeeping
fields (`mutation_id`, `sequence_index`, `timestamp`, `seed`).

Every `MutationOperator` implements two methods under one contract:
`find_candidates(estate)` must return `[]` — never raise — when its
preconditions aren't met, and runs against the pre-clone estate;
`apply(estate, target, severity, rng, mutation_ordinal)` must re-resolve
ids inside its *own* freshly-cloned copy (never reuse pre-clone object
references) and must never mutate the estate it was given.

### The seven operators

**1. Naming drift** (`naming_drift`) — renames `.name` on an Application,
API, or Flow (**never** `.id`). `find_candidates` returns one candidate
per Application/API/Flow, sorted by entity id. `apply` picks
`num_styles = clamp(1 + round(severity * 2), 1, 3)` — so severity 0.0
applies 1 style, severity 1.0 applies up to 3 — and repeatedly
`rng.choice`s among four style functions: kebab→camelCase join,
appending a legacy suffix (`rng.choice(["_v2_final", "_old",
"_deprecated", "-copy2"])`), uppercasing only the *first* segment of a
multi-part name (case shift), or truncating the last segment to 4 chars
(abbreviate). If the final name is unchanged after all style
applications (a no-op), it forces one more legacy-suffix application as a
guard. The rationale text is drawn from four fixed "plausible cause"
strings (team reorg, rebranding, onboarding inconsistency, rushed
migration).

**2. Documentation decay** (`documentation_decay`) — targets any
`TransformStep`/`ApiCallStep` with a non-empty `.description`. Three
severity bands, same 0.34/0.67 thresholds reused across several operators:
`severity < 0.34` truncates to `~40%` of the original word count;
`0.34–0.67` replaces with the literal placeholder `"TODO: document this
step."`; `>= 0.67` empties it entirely (`""`). A no-op escalation chain
(truncated→placeholder→`""`→`"N/A"`) guarantees the description always
actually changes.

**3. Duplicate processing** (`duplicate_processing`) — finds
`FlowRefStep`s whose target resolves to a `sub_flow`. Clones that
sub-flow under a new id/name (`{id}-dup{ordinal}`, `{name}-copy`) with
all its steps re-ided, appends it to the app, and rewires **only the one
triggering caller** to point at the duplicate (other callers of the
original are left untouched — this is what makes it a genuine "now there
are two near-identical implementations" scenario, not a global rename).
At `severity >= 0.5`, the duplicated content itself drifts slightly
(`LoggerStep` message gets `" (updated)"` appended, `TransformStep`
description gets `" (updated copy)"` appended).

**4. Legacy version introduction** (`legacy_version_introduction`) — one
candidate per (app, API) pair. Clones the entry flow's steps under new
ids (`{id}-legacy{ordinal}`), and at `severity >= 0.5` (with more than one
step) drops the last step entirely — "the legacy version predates the
most recent addition." Builds a new, additive `legacy_api`
(`{id}-legacy{ordinal}`, name `"{name} (Legacy)"`, version
`"{version}-legacy"`) alongside the original, unmodified API — this
operator consumes **zero** rng calls in `apply` at all (no random choice
involved, only the severity-gated step-truncation decision, which the
engine already drew for this mutation).

**5. Schema inconsistency** (`schema_inconsistency`) — finds
`TransformStep`s whose `dataweave` matches `(\w+)Id:`. Picks a random
restyling (`camelCase`/`snake_case`/`PascalCase`/`abbreviated`, first 3
chars) via `rng.choice`, and replaces the **first** occurrence of
`{field}:` with the restyled field name. A deterministic no-op fallback
(iterate the four styles in fixed order, no extra rng draw; if literally
none differ, force an `"Alt"` suffix) guarantees an actual change.

**6. Dependency change** (`dependency_change`) — for `ApiCallStep`s,
alternatives are every API id belonging to a **different** application
than the step's own (repointing must cross app boundaries, not just pick
a different API in the same app); for `FlowRefStep`s, alternatives are
every other sub-flow id **within the same app**, excluding the step's own
containing flow (no self-reference). `apply` does one `rng.choice` over
whichever alternative list applies and repoints the target field.

**7. Domain-implausible component injection** (`domain_implausible_component`)
— the one operator whose eligibility depends on estate-level metadata
(`estate.domain`), not structural properties. `find_candidates` returns
`[]` immediately, always, if `estate.domain` isn't `"finance"` or
`"retail"` — this is the exact, by-design mechanism behind "the UI
produced zero mutations because no domain was set" from an earlier
session; the fix there was giving the UI a way to set `domain`, not
changing this check. Otherwise, one candidate per (app, flow) pair.
`apply` picks the **foreign** domain (`next(d for d in SUPPORTED_DOMAINS
if d != estate.domain)` — no rng needed, since there are only two
domains total), picks one of that foreign domain's three plausible
components via `rng.choice`, and appends a new `ConnectorStep`
(`connector_type=component["key"]`) to the flow. Severity only affects
the generated description's wording (three bands, same 0.34/0.67
thresholds: neutral → "vendor evaluation last quarter" → explicitly
flags the mismatch as "carried over from a prior vendor relationship or
acquisition").

### Profiles — `ark/mutation/profiles.py`

Difficulty levels are **additive**, and severity ranges widen as levels
rise:

| Profile | level | operators | num_mutations | severity_range |
|---|---|---|---|---|
| `level_0_clean` | 0 | none | 0 | (0.0, 0.0) |
| `level_1_minor` | 1 | naming_drift, documentation_decay | 3 | (0.1, 0.4) |
| `level_2_structural` | 2 | + duplicate_processing, dependency_change | 6 | (0.3, 0.6) |
| `level_3_legacy` | 3 | + legacy_version_introduction, schema_inconsistency | 10 | (0.5, 0.9) |
| `domain_injection_preview` | −1 (sentinel, descriptive only) | domain_implausible_component only | 1 | (0.2, 0.8) |

`domain_injection_preview` is deliberately **not** folded into
`LEVEL_1/2/3_OPERATORS` — it's reachable only through its own opt-in
profile, so no past batch's behavior changes silently just because the
operator exists in the registry.

### `run_trajectory()` — the engine's core loop (`ark/mutation/engine.py`)

```
rng = make_rng(seed)
current_estate = clone_estate(baseline_estate)
records = []
for i in range(profile.num_mutations):
    eligible = [(op_type, candidates)
                for op_type in profile.operator_types
                if (candidates := OPERATOR_REGISTRY[op_type].find_candidates(current_estate))]
    if not eligible:
        break                                    # graceful early stop — NOT a failure
    op_type, candidates = rng.choice(eligible)    # 1st rng call this iteration
    target = rng.choice(candidates)               # 2nd rng call
    severity = rng.uniform(*profile.severity_range) if profile.severity_range[1] > 0.0 else 0.0   # 3rd (skipped only for level_0_clean)
    new_estate, draft = operator.apply(current_estate, target, severity, rng, mutation_ordinal=i)
    if validate_estate_object(new_estate):        # any referential error
        raise MutationEngineError(...)            # immediate hard failure, no retry, no skip
    if draft.original_state == draft.transformed_state:
        raise MutationEngineError(...)            # no-op detected, also a hard failure
    records.append(MutationRecord(mutation_id=f"{profile.name}-seed{seed}-{i:03d}", sequence_index=i, ...))
    current_estate = new_estate                   # mutations COMPOUND — next iteration sees this, not the baseline
```

Three properties worth being explicit about: (1) **candidate collection
consumes zero randomness** — only the choice *among* eligible
candidates does; (2) an empty `eligible` list is graceful degradation,
not an error — the ledger just ends up with fewer than
`num_mutations` records, which is exactly the "this estate wasn't
big/rich enough, or this profile's precondition wasn't met" signal the
domain-injection-with-no-domain scenario relies on; (3) a genuine
validation failure or a detected no-op is **not** retried or
silently skipped — it raises immediately, on the stated assumption that
this should be unreachable given correctly implemented operators (a
safety net, not a designed control-flow path).

`MutationLedger` carries `baseline_estate_id`, `baseline_schema_version`,
`trajectory_seed`, `profile_name`, `engine_version`,
`ledger_schema_version`, and the `records` list.
`TransformationResult` bundles the untouched `baseline_estate`, the final
`transformed_estate`, and the `ledger` together.

---

## 4. Adapter / Renderer — `ark/adapters/mulesoft`

Converts a (possibly mutated) estate into real MuleSoft XML + API YAML,
plus a **manifest** — the evaluator-only artifact↔entity↔dependency map.
Both outputs come from one `render()` call and are kept structurally
separate from that point on: artifacts go to the agent, the manifest
never does.

### Rendering

One combined XML file per Application at
`{app.name}/src/main/mule/{app.name}.xml`, and one YAML file per API at
`{app.name}/src/main/resources/{api.id}.yaml` (a minimal, non-RAML/OAS
shape: `title`, `version`, `entryFlow`).

Global HTTP connector configs are computed and emitted once per app,
before any flow: `http:listener-config` elements, one per **distinct**
`listener_config_ref` string across all the app's HTTP-triggered flows
(deduplicated via a `set`, then sorted for deterministic ordering; ports
assigned `8081 + index`), and — only if the app has at least one
`ApiCallStep` anywhere — exactly one `http:request-config` (fixed name
`HTTP_Request_config`, port `8082`). This fixed a real historical bug:
earlier renders emitted `<http:request>` elements with a `config-ref`
that pointed at nothing at all (a dangling reference), since no
`http:request-config` global was ever emitted.

Step rendering, one case per `kind`:
- `TransformStep` → `<ee:transform doc:name="...">` wrapping a CDATA
  `<ee:set-payload>` of the raw DataWeave string.
- `FlowRefStep` → `<flow-ref name="{target flow's name}"/>` (resolves the
  target's *name*, not its id, since Mule XML references flows by name).
- `LoggerStep` → `<logger level="..." message="..."/>`.
- `ApiCallStep` → resolves the target API's entry flow; if that flow
  isn't HTTP-triggered, **raises** — rendering only supports HTTP-to-HTTP
  calls today, a known, explicit limitation, not a silent gap. Otherwise
  emits `<http:request method="..." path="..." config-ref="HTTP_Request_config" .../>`.
- `ConnectorStep` → deliberately **not** invented XML: real MuleSoft
  connectors each define their own namespace/operations with no generic
  "call any connector" element, so fabricating one would misrepresent
  what a real render would look like. Instead:
  ```xml
  <!-- External connector reference: {connector_type} -->
  <logger level="INFO" doc:name="{name}" message="{description}"/>
  ```

Every rendered file also produces a parallel `entities` list (one dict
per Application/Flow/Step/API), used to build the manifest's
`entity_index`. Entity ids are deliberately **not** embedded as comments
in the artifacts themselves — that would create a second, potentially
drifting source of truth; the manifest is the sole map.

### Manifest — `ark/adapters/mulesoft/manifest.py`

```python
{
  "estate_id": ..., "schema_version": ..., "adapter": "mulesoft", "adapter_version": ...,
  "artifacts": [{"path": ..., "entities": [...]}, ...],   # sorted by path
  "entity_index": {entity_id: {"artifact_path", "entity_type", "name", "aliases"}},
  "dependencies": [{"kind": "flow-ref"|"api-call", "source_entity_id", "source_step_id", "target_entity_id", "target_entity_type"}],
}
```
`entity_index` is the lookup an agent's plain-text reference resolves
through (see §7's matcher/parser); `dependencies` captures every
`FlowRefStep`/`ApiCallStep` edge (a `TransformStep`/`LoggerStep`/
`ConnectorStep` never produces a dependency entry — they don't reference
other entities).

---

## 5. Validation pipeline — `ark/validation` + `ark/schemas`

A **separate, additive** check layered on top of rendering: does the
rendered MuleSoft XML actually use the HTTP Connector the way its real
documented schema says it should? This never influences agent-performance
scoring — it's a property of *rendering*, not of *the agent*.

`ark/schemas/mulesoft/http_connector.json` is a hand-curated, cited
schema (every element/attribute sourced from a specific
docs.mulesoft.com page URL, version 1.11/Mule 4) covering
`http:listener-config`, `http:listener-connection`, `http:listener`,
`http:request-config`, `http:request-connection`, `http:request`, and the
five real authentication schemes (`http:basic-authentication`,
`http:digest-authentication`, `http:ntlm-authentication`,
`oauth:authorization-code-grant-type`, `oauth:client-credentials-grant-type`)
— explicitly correcting an earlier draft's invented "OAuth1"/generic
"OAuth" scheme that doesn't exist in the real connector. `tls:context`,
`http:load-static-resource`, and `http:basic-security-filter` are
explicitly excluded by design (generic Mule runtime concern, or unused by
any Step type Ark actually renders).

`validate_http_connector_xml(xml_text)`: parses via `ElementTree`; on a
parse failure, returns immediately with a single "not well-formed XML"
issue. Otherwise, for every element whose (namespace-recovered) qualified
tag has a rule in the schema — non-HTTP elements like `<flow>` or
`<logger>` are silently out of scope, never flagged — checks: every
required attribute present, no attribute present that isn't in the
documented required-or-optional set (an "unknown attribute" catches
anything invented), every required child element present, and — for
`http:authentication` — exactly one of the five auth-scheme children
present (zero or more-than-one both flagged). `http:listener`'s and
`http:request`'s `config-ref` attributes are cross-checked against the
*other* kind's config names too, so pointing a listener at a
request-config (or vice versa) is caught as a wrong-kind reference, not
just "resolves to nothing."

`ark/validation/pipeline.py` wires this in as a standing, automatic step:
`validate_rendered_estate_safe(rendered)` runs the validator over every
`.xml` artifact (`.yaml` files are skipped — not Mule XML, nothing to
check) and degrades any *unexpected* exception (a wiring bug, not a
content issue) to a `validation_error` string rather than aborting the
trajectory. The result becomes `EvaluationReport.rendering_validation` —
a new, additive, sibling field next to `agent_performance`, never folded
into it.

---

## 6. Agent Harness — `ark/harness` + `integrations/`

### The contract

```python
class AgentClient(ABC):
    def generate(self, prompt: str) -> str: ...
```
One method, no conversation state, no tool loop, no streaming — one
prompt string in, one raw text response out. Any exception the client
raises propagates uncaught; a failed agent call is a failed evaluation
run, not something silently papered over.

### Prompt construction — `ark/harness/prompt.py`

The **one** place this layer is allowed to import from `ark.evaluator`:
`ISSUE_TYPE_TAXONOMY` (see §7.1), so the agent-facing category list is
always sourced live from the actual registered operators — never
hand-copied and never able to silently drift out of sync with what the
evaluator will actually score against. The instructions template asks for
a single bare JSON object (no prose, no code fence) of the shape
`{"findings": [{"artifact_reference", "entity_reference", "issue_type",
"explanation", "confidence"}]}`, states `{"findings": []}` is valid for
"nothing wrong," and that `issue_type` should be `"other"` if truly
nothing in the list fits. Artifact files are rendered as
`## {path}\n\`\`\`\n{content}\n\`\`\`` sections, **sorted alphabetically
by path** for reproducibility, joined with blank lines. The final prompt
is exactly `instructions + artifacts_section` — a plain `dict[str, str]`
in, nothing else; no manifest, ground truth, or ledger ever enters this
function's input type at all.

### Response parsing — `ark/harness/response_parsing.py`

Recovers a `{"findings": [...]}` dict from arbitrary agent text, trying
strategies **in this exact order**, stopping at the first success:
1. Parse the whole stripped response as JSON; if it's a `dict`, done. If
   it parses but is **not** a dict (a bare list, a bare string), this
   raises immediately rather than falling through — deliberately, to
   avoid "recovering" an unrelated object nested inside something that
   isn't the real answer.
2. Search for a fenced code block (` ```json ... ``` ` or bare ` ``` `),
   parse its contents.
3. Brace-substring heuristic: from the first `{` to the last `}` in the
   raw text, attempt to parse that slice.
4. If none produced a dict, raise `AgentResponseParsingError` (message
   includes the first 500 characters of the raw response, for
   debugging).

### The three `AgentClient` implementations

- **`ScriptedAgentClient`** — not itself a heuristic; a thin, generic
  wrapper around any `(prompt) -> str` callable, plus a `.fixed(findings)`
  classmethod for a truly static canned response, plus a
  `prompts_received` audit log. All content-dependent behavior lives in
  whatever responder it's constructed with.
- **`HeuristicNamingAgentClient`** — the real logic behind Ark's default
  offline demo agent. Re-parses the prompt's own rendered artifact
  sections (regex over the `## path\n\`\`\`...\`\`\`` shape), extracts
  candidate names (YAML `title:` lines, or XML `name="..."` attributes),
  and flags two very specific signatures: (a) a name whose *first*
  segment is all-caps while the rest isn't (exactly what
  `NamingDriftOperator`'s case-shift style produces) at confidence 0.7,
  checked first; (b) a name matching a legacy-suffix pattern
  (`_v\d+_final`, `_old`, `_deprecated`, `-copy\d*`, or the substring
  "legacy", case-insensitive — exactly what the legacy-suffix drift style
  and `LegacyVersionOperator`'s naming both produce) at confidence 0.65,
  checked only if the case-shift check didn't already match. Every
  emitted finding is hardcoded `issue_type="naming_drift"` — this
  heuristic implements a signal for exactly one of the seven
  transformation types and nothing else, by design (it's a baseline demo
  agent, not a claim of full coverage).
- **`AnthropicAgentClient`** / **`GeminiAgentClient`** (both in
  `integrations/`, outside `ark/`'s zero-dependency core, imported
  lazily): single-turn calls (`messages.create` / `generate_content`)
  with the raw prompt as the sole user-role content, default models
  `claude-haiku-4-5-20251001` (UI demo default; `DEFAULT_MODEL` inside the
  Anthropic client module is `claude-sonnet-5`) and `gemini-3.1-flash-lite`
  respectively. Gemini reads its own `GEMINI_API_KEY` env var (not the
  SDK's default `GOOGLE_API_KEY`), and raises a clear `ValueError` if
  neither that nor an explicit `api_key=` is supplied.

`run_agent_harness(artifacts, agent_client)` is three unwrapped steps:
build the prompt, call `.generate()`, `extract_json_object()` the result
— no retry, no error catching. Deliberately does **not** call the
evaluator's shape-validator itself, so a harness-produced output and a
hand-authored test fixture go through the identical downstream validation
path.

---

## 7. Evaluator — `ark/evaluator`

### 7.1 Taxonomy (`schema.py`)

```python
ISSUE_TYPE_TAXONOMY = frozenset(OPERATOR_REGISTRY.keys()) | {"other"}
```
Derived **live** from the mutation registry — currently 7 real
transformation types (`naming_drift`, `documentation_decay`,
`duplicate_processing`, `legacy_version_introduction`,
`schema_inconsistency`, `dependency_change`,
`domain_implausible_component`) plus the catch-all `"other"`, 8 members
total. `parse_agent_output()` validates every finding has all five
required fields (non-empty strings; `confidence` a real number in
`[0.0, 1.0]`, not a bool), collecting **every** violation before raising.
An `issue_type` the agent claims that isn't in the taxonomy is **silently
normalized to `"other"`**, not rejected — the raw string is preserved
separately for audit (`Finding.raw_issue_type`).

### 7.2 Issue derivation — `derive_issues(ledger)` (`issues.py`)

This is the algorithm that turns a raw, chronological ledger into the
actual scoreable "answer key" — and it is genuinely subtle, because
mutations can compound or cancel.

**Grouping**: records are grouped by `(transformation_type,
sorted(affected_entity_ids))`, processed in `sequence_index` order,
tracking first-appearance order for deterministic output.

**Per-entity original state — "earliest wins"**: if the very first
contributing record shows the entity had no prior state (a creation
event), original state is `None` entirely. Otherwise, fields are merged
by iterating the group's records **in reverse** and calling `.update()` —
since a reversed-order `.update()` sequence means the *earliest* record's
field values get applied last (and so win), this correctly captures "what
the field looked like before any mutation in this group touched it," even
across several compounding records.

**Per-entity transformed state — "latest wins"**: the mirror image,
merged in forward order, so the last record's values win.

**Net-change diff**: for a non-creation entity, only fields where
`cumulative_transformed[field] != cumulative_original[field]` survive
into `net_changed`. **If every entity in a group nets to a completely
empty diff — e.g. a field changed and then changed straight back — the
entire group produces no Issue at all.** It's recorded instead in a
`net_zero_groups` diagnostic list (`IssueDerivationDiagnostics`, alongside
`total_groups`/`surviving_issue_count`/`net_zero_group_count`), and the
Issue derivation `continue`s past it. This is the precise mechanism
behind "compounding mutations that cancel out produce no scoreable
issue" — a real, intentional behavior, not a bug to guard against.

**Severity rollup**: `max()` across every raw record contributing to a
surviving group — a conservative, worst-observed rollup, never an
average.

**Issue id**: `f"{transformation_type}:{'+'.join(sorted(surviving_entity_ids))}"`
— built only from the entities that actually survived the net-zero
filter, so it's stable regardless of how many records or which order
contributed.

`Issue.mutation_count` = the number of raw ledger records that
contributed to it (used later by the complexity model's "compounding"
term).

### 7.3 Reference resolution and matching (`parser.py` + `matcher.py`)

Deliberately **no fuzzy/edit-distance/substring matching anywhere** —
only exact matches after a fixed normalization:
`_normalize(name) = re.sub(r"[\s_\-]+", " ", name.strip().lower())`
(case-fold, collapse `_`/`-`/whitespace to single spaces).

**Artifact path resolution**: exact full-path match against the
manifest first; failing that, an exact basename match — but **only** if
exactly one artifact shares that basename (an ambiguous basename resolves
to nothing, rather than guessing).

**Entity resolution**, in order: (1) resolve the artifact reference; (2)
if resolved, search only that artifact's entities by normalized
name/alias — exactly one match resolves; more than one is `"ambiguous"`;
zero falls through; (3) search the **whole manifest**'s entities by name —
exactly one match resolves (status `"resolved"`, tagged
`"exact_name_whole_manifest_fallback"`); more than one is `"ambiguous"`;
zero is `"unresolved"`.

**Matching** (`match_findings`): only a `status == "resolved"` entity
reference is considered at all (ambiguous/unresolved findings never get
matched to anything). `issue_types_present` = the set of transformation
types that exist **anywhere** in the estate's real issues — this is the
literal implementation of "the claimed category exists somewhere in the
estate" (an estate-wide check, independent of which entity was named). If
the resolved entity has any real issue(s) against it, the matcher prefers
one whose `issue_type` equals the agent's claim; if none of that entity's
issues match the claim, it falls back to the **first** issue on that
entity anyway (in deterministic ledger order) — so the agent still gets
credited with finding *something real* even if it mis-typed it, which is
exactly what lets "correct location, wrong diagnosis" be measured as a
distinct failure mode later. Two independent booleans come out of this:
`entity_correct` (matched *some* real issue on that entity, regardless of
type) and `category_correct` (claimed type exists anywhere in the
estate's real issues, regardless of entity) — these are never blended
into one score.

### 7.4 Metrics — `metrics.py`

The strict, both-axes true-positive definition, shared with calibration:
```python
def is_true_positive(match, issues_by_id):
    issue = issues_by_id.get(match.matched_issue_id)
    return issue is not None and match.claimed_issue_type == issue.issue_type
```

**Category precision/recall/F1**:
```
TP = findings that are true positives (matched issue AND correct type)
FP = len(matches) − TP                                    (every non-TP match — wrong type OR hallucination)
FN = issues whose id is not in {m.matched_issue_id for m in true_positive_findings}   (a set — multiple claims on one issue don't inflate recall)

precision = TP / len(matches)   if matches else None       (undefined, not 0, if the agent made no claims)
recall    = TP / len(issues)    if issues  else None       (undefined, not 0 or 1, on a clean/Level-0 estate)
f1 = 2·precision·recall/(precision+recall)   if both defined
     0.0                                     if precision+recall == 0 (both zero — avoids /0 while still a real number)
     None                                    if either input is None
```
`compute_category_metrics_by_type` re-runs the identical formula scoped
to each distinct issue type present, giving a per-transformation-type
precision/recall/F1 breakdown.

**Entity localization** uses the exact same TP/FP/FN/precision/recall/
(harmonic-mean, renamed `localization_accuracy`) shape, but substitutes
the **looser** `entity_correct` boolean for `is_true_positive` — right
location scores here regardless of claimed type.

### 7.5 Calibration — `calibration.py`

Computed only over resolved matches (agent claims that resolved to
something at all).

**Brier score** — mean squared error between stated confidence and
1-if-true-positive-else-0:
```
brier_score = mean( (confidence − [1 if is_true_positive else 0])² )   for confidence, is_true_positive in every match
```
`None` only when there are zero matches at all — no separate minimum
sample size beyond that.

**ECE (Expected Calibration Error)** — gated behind a minimum of 5
matches (`DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE = 5`; below that, reported as
`None`, never a noisy number from 1-2 samples). 10 fixed-width bins
(`DEFAULT_ECE_BIN_COUNT = 10`) by raw confidence value (`index =
min(int(confidence * 10), 9)` — clamps a confidence of exactly `1.0` into
the last bin rather than overflowing):
```
ece = Σ over non-empty bins of  (bin_size / total_matches) · |avg_confidence_in_bin − avg_accuracy_in_bin|
```
A standard weighted-sum ECE — lower is better, 0 is perfect calibration.

### 7.6 Complexity score — `complexity.py`

One number per trajectory, summarizing how much the mutation engine
actually changed the estate — **independent of how the agent performed**,
used purely as the analysis layer's x-axis.

Six sub-terms, each normalized/clamped to `[0, 1]`:

```
norm(mutation_count)          = min(1, len(ledger.records) / 15)
severity_mean                  = mean(record.severity for record in ledger.records)     (already 0-1)
transformation_diversity       = distinct_transformation_types_used / len(OPERATOR_REGISTRY)   (live operator count, not hardcoded)
norm(dependency_impact_mean)   = min(1, mean(in_degree(entity) for entity in affected_entities) / 5)
interaction_score               = see below                                              (already 0-1)
norm(compounding_count)        = min(1, count(entities hit by >1 ledger record) / 5)
```

`interaction_score` — a union-find over the distinct Flows/APIs owning
every affected entity, with edges = direct `FlowRefStep`/`ApiCallStep`
adjacency (not transitive reachability through untouched intermediaries):
```
interaction_score = 1 − (num_connected_components − 1) / max(1, unique_affected_node_count − 1)
```
1.0 means every affected entity sits in one connected cluster (a
concentrated, interacting set of changes); progressively lower values
mean the affected entities are scattered into separate, unrelated
islands. Returns `0.0` outright if there's ≤1 distinct affected node
(nothing to be "connected" at all).

Final rollup — an **equal-weight average of all six** (default weights
all `1.0`, so this is literally the arithmetic mean):
```
complexity_score = ( norm(mutation_count) + severity_mean + transformation_diversity
                    + norm(dependency_impact_mean) + interaction_score + norm(compounding_count) ) / 6
```

### 7.7 Explanation signals — `explanation.py`

Deliberately **not** an LLM-judged quality score — every signal is a
shallow, deterministic, purely textual check, run per finding against its
own normalized `explanation` string:

- `mentions_affected_artifact` — does the explanation mention its own
  claimed artifact path (or that path's bare filename)?
- `references_observable_symptom` — does it mention any field name or
  short value string from the matched issue's `observable_symptom` dict
  (values over 200 chars skipped, never partial-matched)? Always `False`
  if nothing matched at all.
- `identifies_plausible_cause` — does it contain any of nine fixed
  causal-language markers ("because", "due to", "caused by", "since", "as
  a result", "leading to", "resulting in", "which caused", "the reason")?
- `unsupported_assumption_flag` — `True` when the explanation is
  non-empty but grounds itself in **neither** its own artifact **nor**
  any real observable symptom — described as "a structural proxy for
  purely speculative," explicitly not a correctness judgment on its own.

### 7.8 Failure analysis and report assembly

Five buckets, computed from the same match list, each independently
meaningful:
- **Missed issues** — real Issues with zero true-positive claims against
  them.
- **Hallucinated findings** — `matched_issue_id is None` (resolved to
  nothing real at all).
- **Wrong category predictions** — `category_correct` is false.
- **Correct-location-incorrect-diagnosis** — `entity_correct` true, a
  real issue was matched, but it wasn't a true positive (right place,
  wrong label).
- **Overconfidence patterns** — `confidence >= 0.7`
  (`DEFAULT_OVERCONFIDENCE_THRESHOLD`) on a claim that wasn't a true
  positive.

`assemble_report()` performs **no scoring of its own** — every number was
already produced by the modules above; it only buckets, labels, and
packages everything into one `EvaluationReport`
(`metadata, environment_summary, transformation_summary, issue_summary,
agent_performance, failure_analysis, research_hooks, issues,
raw_agent_output, rendering_validation`).

`orchestrator.py`'s `evaluate()` is the single entry point wiring §7.2
through §7.8 together in one fixed call order (issues → parse agent
output → resolve findings → match → metrics/calibration/explanation/
complexity → assemble). It is given exactly four inputs — the
*transformed* estate, the mutation ledger, the rendered manifest, and the
raw agent output — and never imports an adapter itself; this is the exact
boundary that keeps evaluation reproducible from saved artifacts alone.

---

## 8. Cross-Experiment Analysis — `ark/evaluator/analysis.py`

Given many `EvaluationReport`s from one batch, this layer answers Ark's
research questions by pure aggregation — it introduces **no new scoring
rules** of its own, only statistics over numbers already computed.

**Complexity buckets** — 5 fixed, equal-width bands over the guaranteed
`[0,1]` complexity range (`(0,0.2), (0.2,0.4), (0.4,0.6), (0.6,0.8),
(0.8,1.0)`, boundaries computed once as `i/5` to `(i+1)/5`, not derived
from the batch's observed min/max) — so "the 0.4–0.6 band" means the same
thing across different batches. Each report is assigned to exactly one
bucket via `index = clamp(int(score * 5), 0, 4)`.

**Pearson correlation** — the textbook formula, computed manually:
```
r = Σ(x−mean_x)(y−mean_y) / sqrt( Σ(x−mean_x)² · Σ(y−mean_y)² )
```
Returns `None` (never `0.0`) if fewer than 2 data points, or if either
series has zero variance (a constant series has no defined correlation).
Additionally gated at the call site: only computed at all with **≥5**
non-null pairs (`DEFAULT_MIN_SAMPLE_SIZE_FOR_CORRELATION`); every
`CorrelationStatistic` carries a fixed disclaimer that this is
association, not causation, and not confound-adjusted.

**Transformation impact / degradation** — for each transformation type,
compares that type's observed average performance against a **clean
baseline** (the same batch's mutation-free, `mutation_count==0`
trajectories, if any exist; `None` — not assumed zero — if the batch has
none). Two sign conventions, unified so **positive always means
"worse than baseline"**:
```
category_f1_degradation        = baseline_f1  − observed_f1      (higher-is-better metric: lower observed = positive degradation)
calibration_ece_degradation    = observed_ece − baseline_ece      (lower-is-better metric: sign flipped so positive still = worse)
```
Rows are sorted worst-first by degradation, with any `None` sorted last
(never silently treated as zero or as "best").

**Calibration drift** — per complexity bucket, average stated confidence
vs. average actual F1, and
`confidence_minus_accuracy_gap = average_confidence − average_category_f1`
(positive = overconfident as complexity rises, negative =
underconfident) — a growing gap across buckets is exactly "the agent
stays confident even as it actually gets less accurate." The correlation
between complexity and ECE shown alongside this is the **literal same
object** as the one in the complexity-correlation table (not
recomputed), "so there's exactly one number for this relationship."

**Experiment summary** — trajectory count and simple, `None`-safe
averages (only non-`None` values are averaged; an empty list averages to
`None`, never `0`) of complexity score, category F1, localization
accuracy, and calibration ECE across the whole batch — the numbers the
UI's top-level summary card reads directly. Also tallies
`transformation_type_distribution`: how many trajectories in the batch
realized each transformation type at least once.

`skipped_report_count` is purely a file-loading concern
(`load_reports_from_files()` catching `OSError`/`json.JSONDecodeError`/
`KeyError`/`TypeError` per report file and recording the reason) — it has
nothing to do with a trajectory failing to *run*; a failed trajectory run
aborts the whole experiment (see §9), it doesn't get silently skipped
here.

---

## 9. Experiment Runner — `ark/experiment`

`TrajectorySpec` (`label, profile_name, seed, baseline_estate_path,
generator_config`) enforces, in `__post_init__`, that **exactly one** of
`baseline_estate_path`/`generator_config` is set — never both, never
neither.

`run_trajectory_spec_with_artifacts()` is the one place every stage above
actually gets wired together, in this exact order: resolve the baseline
estate (`validate_ground_truth()` for a hand-authored path, or
`generate_estate()` for a `GeneratorConfig`) → `run_trajectory()` (the
mutation engine) → `adapter.render()` → `run_agent_harness(rendered.artifacts, ...)`
(the agent sees **only** `rendered.artifacts` — never the manifest, the
ledger, or the ground truth) → `validate_rendered_estate_safe(rendered)`
(only if the adapter is `MuleSoftAdapter`) → `evaluate(transformed_estate,
ledger, rendered.manifest, raw_agent_output, rendering_validation=...)`.
`run_experiment()` runs this once per spec in a batch, collecting every
`EvaluationReport`, then calls `analyze_reports()` once over the whole
batch. A single spec's failure (an agent error, malformed output, an
unrecognized profile name) is **not** caught here — it propagates and
aborts the entire run, rather than silently producing a partial batch;
"skipped" trajectories only exist at the file-loading layer (§8), never
at run time.

---

## 10. Interactive UI — `ark/ui` (Streamlit)

A hard architectural split, enforced by both docstring convention and an
import-boundary test: `logic.py` contains **zero** Streamlit imports —
every request-building and data-shaping function is plain, unit-testable
Python; `app.py` contains (almost) nothing but `st.*` calls consuming
`logic.py`'s outputs.

`logic.py`'s surface, grouped by what it does: agent selection
(`build_agent_client`, `*_missing_requirements` readiness checks,
`agent_model_label` reading the *actual constructed client's* model
rather than a hardcoded string) → trajectory spec building
(`build_trajectory_specs`, with the keyword-only `domain=` parameter that
only affects the generator branch, and only actually matters for the
`domain_injection_preview` profile) → running
(`run_ui_experiment`, a bare passthrough to `run_experiment`) → data
extraction, one function per dashboard section
(`environment_summary_rows`, `agent_performance_rows`,
`failure_analysis_rows`, `experiment_summary_rows`) → research
visualization rows, both the older bucketed views
(`complexity_vs_performance_rows`, `complexity_correlation_rows`,
`transformation_impact_rows`, `calibration_drift_rows`) and the newer
per-trajectory scatter views (`complexity_scatter_rows`,
`calibration_scatter_rows`, each explicitly skipping any trajectory
whose relevant metric is `None` rather than plotting a fake zero) →
`linear_trendline()`, a plain-Python ordinary-least-squares fit:
```
slope     = Σ(x−mean_x)(y−mean_y) / Σ(x−mean_x)²
intercept = mean_y − slope·mean_x
```
returning `None` if there are fewer than 2 points or the x-values are
constant (no meaningful slope) → export (`export_report_json`,
`export_analysis_json`, thin passthroughs) → the isolation guard,
`assert_artifacts_contain_no_evaluator_metadata()`, which checks every
artifact-dict key/value against a fixed denylist of evaluator-only key
names and asserts every value is a plain string — a structural,
test-covered proof that the artifact viewer's "visible to agent" panel
really can't leak anything.

`app.py`'s structure, top to bottom: page setup → sidebar Experiment
Configuration (agent choice, estate source, profile, the
profile-conditional domain selector, seed, trajectory count) → Run button
handling (spinner, session-state storage, broad exception handling
surfaced as `st.error`) → a trajectory selector → the Results Dashboard
(Experiment Summary cards with direction hints, Environment
Summary/Agent Performance side by side, Failure Analysis buckets) →
Research Visualization (the scatter+trendline charts, the transformation-
type bar chart guarded against an empty-rows crash, the calibration drift
scatter, with the older bucketed views kept in collapsed expanders for
reference rather than removed) → the Artifact Viewer's explicit
visible/hidden split → Export download buttons.

---

## Design invariants that hold across every layer above

- **The agent never sees ground truth, the mutation ledger, or the
  manifest — only rendered artifact text.** Enforced structurally
  (`ark/harness/prompt.py`'s input type is `dict[str,str]`, full stop)
  and with dedicated wiring-level tests, not just by convention or
  docstring.
- **Every average or ratio that's mathematically undefined is reported
  as `None`, never a misleading `0` or `1`.** Precision with no claims,
  recall with no issues, F1 with either undefined, ECE below 5 samples,
  correlation below 5 pairs or zero variance, degradation with no clean
  baseline in the batch — all `None`, checked explicitly at every layer
  above, not just at the metrics layer.
- **Everything seeded is deterministic.** One `random.Random` instance
  per trajectory, threaded explicitly, never Python's global `random`
  state; lists (never sets) used wherever order-stability across
  processes matters.
- **Zero-dependency core.** `ark/core`, `ark/generator`, `ark/mutation`,
  `ark/adapters`, `ark/evaluator`, `ark/experiment` have no third-party
  dependencies at all. Vendor SDKs (`anthropic`, `google-genai`) live
  only in `integrations/`, imported lazily; Streamlit lives only in
  `ark/ui/app.py`.
- **Graceful degradation over silent invention or hard failure, except
  where correctness is actually at stake.** Zero eligible mutation
  candidates → early stop, not an error. Zero realized mutations → the
  UI shows an explanatory message, not a crash. An unresolvable
  `FlowRefStep` target → a loud, explicit render-time error, because
  silently rendering *something* there would misrepresent what the
  estate actually contains.
- **Association, not causation.** Every correlation and degradation
  number in the analysis layer carries this disclaimer explicitly, in
  the data structure itself, not just in surrounding prose.
- **New capabilities are additive, never retrofit onto old behavior.**
  `ConnectorStep`/`domain` extended the schema without bumping meaning of
  older fields; `domain_implausible_component` was registered as a 7th
  operator but deliberately excluded from the existing Level 1–3
  profiles; `rendering_validation` was added as a new sibling field on
  `EvaluationReport`, never folded into `agent_performance`.
