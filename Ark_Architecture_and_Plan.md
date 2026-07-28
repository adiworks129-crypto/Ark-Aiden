# Ark — Architecture Proposal & Implementation Plan

Status: **DRAFT — awaiting your approval before any code is written.**

This document is the output of Phase 1 (design/analysis). Four research passes were run in parallel — MuleSoft domain modeling, synthetic-benchmark/eval design, core architecture, and testing/documentation strategy — and are synthesized below into one plan. Nothing in the `Ark/` folder exists yet except this document.

**Revision note (rev. 2)**: incorporated your controlled synthetic-drift/transformation-modeling research. This reframed the mutation engine from "apply operators, keep a ledger" into an explicit transformation framework (§1.7): mutation as an operator `T` applied to a baseline, a structured Transformation Record per application, an ordered trajectory (not a single batch diff) enabling progressive difficulty levels, and complexity measured as a per-category vector rather than a single pass/fail. This changed one prior recommendation (transformation-history representation, Section 2) and added two new decisions (severity/magnitude representation, complexity aggregation) — both flagged below for your review since Milestone 4 and the evaluator now depend on them directly.

---

## 0. What Ark is, in plain terms

Ark is not "a script that makes fake MuleSoft files." It's a small pipeline with four moving parts that all point at one shared fact-base:

1. A **ground truth** — a structured, versioned description of a fake enterprise's APIs, flows, mappings, etc. This is the answer key.
2. A **generator** that builds ground truth from scratch (seeded, so it's reproducible).
3. A **mutation engine** that deliberately makes the estate messier (duplicate flows, legacy versions, inconsistent names) and writes down exactly what it changed.
4. An **exporter** that renders the ground truth into realistic-looking files (XML, DataWeave, docs) that an AI agent would actually be handed, plus an **evaluator** that grades the agent's answers against the ground truth and the mutation log.

The reason this is worth building as a framework rather than one-off files: MuleSoft is step one, but the same four parts (ground truth → generate → mutate → export/evaluate) should work for other technologies later without a rewrite.

**Glossary** (terms used below):
- **Ground truth**: the machine-readable "correct answer" describing the estate — what an agent's output is graded against.
- **Adapter**: a plug-in that knows how to render ground truth into one specific technology's file format (MuleSoft first; others later). Core Ark never contains MuleSoft-specific logic.
- **Mutation ledger**: an append-only log of every deliberate change (issue) injected into an estate, used to check whether an agent found it.
- **Calibration**: whether an agent's *stated confidence* matches its *actual correctness rate* — a well-calibrated agent that says "90% sure" is right about 90% of the time. This is graded separately from plain accuracy.
- **Golden-file test**: a test that re-generates output from a fixed input and diffs it against a checked-in "known good" file, to catch silent drift.
- **Seeded randomness**: generation driven by an explicit random seed so the same seed always reproduces the same estate byte-for-byte.

---

## 1. Proposed Architecture

### 1.1 Modules

| Module | Responsibility |
|---|---|
| `core/` (Domain Model) | Technology-agnostic ground-truth schema: `Application`, `API`, `Flow`, `Transform`, `Connector`, `SharedSchema`, `Queue`, `Scheduler`, `Policy`, `Dependency` edges, `Document`. No MuleSoft-specific nouns. |
| `store/` (Ground Truth Store) | Persists a versioned estate; single source of truth everything else reads/writes. |
| `generator/` | Builds a base estate from parameters (size, vocabulary, topology) using seeded randomness. |
| `mutation/` (Transformation Framework) | Applies named, parameterized transformation operators (duplicate flow, deprecate version, rename drift, schema drift, doc decay, dependency change) to an estate as an explicit, traceable operation — not random corruption. Every operator application produces a structured **Transformation Record** (see §1.7); the ordered sequence of records *is* the mutation history, from which any intermediate estate state can be exactly replayed. |
| `adapters/mulesoft/` (first adapter) | Renders ground truth into MuleSoft-shaped artifacts (flow XML, DataWeave, docs) + a rendering manifest mapping files back to ground-truth entity IDs. |
| `traffic/` | Generates representative synthetic runtime logs/traces from the estate's dependency graph (volumes, latency, error rates). |
| `evaluator/` | Scores agent output against ground truth + mutation ledger: precision/recall per issue type, plus calibration (Brier score / expected calibration error). |
| `cli/` | Orchestration commands (`ark generate`, `ark mutate`, `ark export --target=mulesoft`, `ark simulate-traffic`, `ark score`); owns reproducibility manifests (seed, versions used). |

### 1.2 Data flow

```
ark generate  →  base ("clean") estate written to Ground Truth Store (estate@v0, baseline)
      ↓
ark mutate --trajectory=X  →  reads v0, applies an ORDERED sequence of transformation
                               operators T1, T2, ... Tn, one at a time
                               → each Ti produces a Transformation Record (what/where/why/
                                 magnitude/impact) appended to the mutation history
                               → estate@v0 -> v1 -> v2 -> ... -> vn (each step replayable
                                 and individually addressable, not just the final state)
      ↓
ark export --target=mulesoft  →  reads any estate@vk → XML/DataWeave/docs + rendering manifest
      ↓
ark simulate-traffic → reads vk's dependency graph → synthetic logs
      ↓
[ AI agent examines exported files / logs, produces an output — outside Ark ]
      ↓
ark score     →  compares agent output to estate@vk + full transformation history (v0..vk)
                  + rendering manifest
              →  accuracy + calibration report, optionally plotted against the estate's
                 complexity score at vk (see §1.7) to show performance-vs-difficulty curves
```

Note the change from the original draft: mutation is no longer "apply a batch, get one diff." It's a **trajectory** — an ordered walk from a known clean baseline through individually-recorded steps — so that complexity/drift can be measured incrementally, and any point along the walk (v0 through vn) can be exported and evaluated as its own difficulty level.

### 1.3 The adapter boundary (why MuleSoft-first doesn't lock us in)

Core Ark only knows the domain model and an abstract `TargetAdapter` interface (`render_flow`, `render_transform`, `render_connector`, `render_docs`, `build_manifest`). **Rule: the domain model never imports adapter code; adapters may only import the domain model.** MuleSoft-specific knowledge (its XML shape, DataWeave syntax, project layout) lives entirely in `adapters/mulesoft/`. A second adapter (e.g., a different iPaaS) means writing new templates — zero changes to schema, store, mutation engine, or evaluator. We'll stub a second, near-empty adapter early specifically to prove this boundary holds (see Milestone 4).

### 1.4 Suggested directory layout

```
ark/
  core/        # domain model (pydantic), IDs, graph utilities
  store/       # ground truth persistence + versioning
  generator/   # synthesizer, vocabulary/faker providers
  mutation/    # operator registry, mutation ledger schema
  traffic/     # synthetic runtime simulation
  evaluator/   # scoring, calibration metrics
  cli/         # orchestration commands
  adapters/
    base.py         # TargetAdapter interface
    mulesoft/        # templates/, renderer.py, manifest.py
    _stub_adapter/   # near-empty 2nd adapter, proves the boundary
tests/
examples/
  small_estate/      # one static example estate + expected export (golden files)
docs/
  adr/               # architecture decision records
```

### 1.5 Key external dependencies

| Dependency | Why |
|---|---|
| Pydantic | Ground-truth schema definition + validation, versioned models |
| Jinja2 | Templating XML/DataWeave/doc text from domain entities |
| networkx | Dependency-graph modeling (cycle detection, topology-aware mutation/traffic) |
| Faker | Realistic names/IDs for synthetic entities |
| Python `random`/NumPy with explicit seeds | Reproducibility — never ambient randomness |
| DeepDiff (or custom) | Comparing estate versions; verifying the mutation ledger matches actual diffs |
| pytest + snapshot/golden-file testing | Regression-testing adapter output |

### 1.6 Risks / fragile areas

| Risk | Mitigation |
|---|---|
| Ground truth and exported artifacts silently drift apart | Rendering manifest is authoritative; golden-file + round-trip tests in CI (render → re-parse → diff against ground truth) |
| Combinatorial explosion of mutation combinations | Mutation "profiles" (curated sets) rather than free composition of all operators |
| Chasing MuleSoft realism too far (full runtime-executable projects) | Cap fidelity at what affects an agent's *reasoning*, not what's needed to actually deploy/run |
| Adapter interface quietly becoming MuleSoft-shaped | Build the stub second adapter in Milestone 4, not "later" |
| Traffic simulator over-investment | Scope to the statistics actually needed for eval questions, not full observability-platform fidelity |
| Mutation ledger schema sloppiness | Treat ledger schema with the same rigor/testing as the domain model — it *is* the answer key for "did the agent find issue X" |

### 1.7 Transformation Framework (research-driven design)

This section incorporates your research on controlled synthetic-data drift directly into the mutation engine's design — it changes how §1.1's `mutation/` module and Milestone 4 work, not just their description.

**Core idea**: treat every mutation as an explicit transformation operator `T`, applied to a ground-truth estate: `estate' = T(estate, params)`. A synthetic estate is never "randomly generated as broken" — it is always `estate_baseline` plus an ordered, fully-recorded sequence of operators `T1, T2, ..., Tn`. This is the same principle as **event sourcing** in software architecture: the source of truth is the ordered log of changes, not just a final snapshot, and any past state is reconstructed by replaying the log from the baseline.

**Transformation Record** — every application of an operator writes one of these (this directly implements your five required metadata fields):

| Field | Meaning | Maps to your requirement |
|---|---|---|
| `transformation_id` | Unique, stable ID for this record | — (addressability) |
| `operator_type` | e.g. `NAME_DRIFT`, `FLOW_DUPLICATE`, `SCHEMA_LEGACY_VERSION`, `DOC_REMOVAL`, `DEPENDENCY_CHANGE` | *What* changed |
| `target_entity_ids` | Ground-truth entity IDs directly touched | *Where* it occurred |
| `rationale` | Human-readable + structured tag, e.g. "simulates naming drift after a team reorg" | *Why* it was introduced |
| `magnitude` | Continuous parameter(s) controlling how severe this instance is (see below) | *Severity/magnitude* |
| `impact` | Computed: downstream entities reachable from the target via the dependency graph (blast radius), plus which agent-facing artifacts change | *How it affects the overall estate* |
| `sequence_index` / `parent_transformation_id` | Position in the trajectory; optional link if one transformation presupposes another | Ordering, traceability |
| `seed` | RNG seed used for any randomized parameter inside this operator | Reproducibility |
| `record_schema_version` | Versioned like the domain model itself | Long-term stability of the ledger format |

**Trajectory**: an ordered list of Transformation Records applied to one baseline estate. `estate@vk` is always derivable by replaying records `[0..k]` against `estate@v0` — and, mirroring the Milestone-0 testing philosophy, this replay must be tested to reconstruct byte-identical state (a "mutation round-trip" test), not just "produces something plausible."

**Controllable degrees of freedom / complexity levels**: each operator instance carries a continuous `magnitude` (e.g. 0.0–1.0, or operator-specific units like "# of fields drifted"). A **complexity scoring function** aggregates the magnitudes and blast-radius impacts of all records in a trajectory up to step `k` into a complexity profile for `estate@vk`. This lets Ark generate a *progression* — v0 (clean) → v1 (mild) → ... → vn (highly inconsistent) — and lets the evaluator later plot agent accuracy/calibration *as a function of* complexity, directly serving the "measure how agent performance changes as environments become progressively more challenging" goal.

**Baseline-vs-degraded comparison**: because every state is `baseline + replay(records[0..k])`, comparing any two points on the trajectory (or across trajectories) is a structural diff over Transformation Records, not a heuristic file diff — this is what makes drift "measurable" rather than just "different."

---

## 2. Ambiguous decisions — recommendations

These are the calls that are genuinely yours to make, not defaults I should silently assume.

| Decision | Options | Recommendation |
|---|---|---|
| Ground-truth storage format | (a) flat JSON/YAML files in git (b) SQLite (c) graph database | **(a)** — diffable, human-reviewable, zero infra to stand up. Revisit (c) only if estates get large/query-heavy. |
| Traffic realism | (a) static synthetic logs (b) live replay/simulation engine | **(a)** initially — satisfies "representative traffic patterns" without building a runtime. Upgrade path to (b) later if agent eval needs interactive/temporal behavior. |
| Repo structure | (a) monorepo with `adapters/` subpackages (b) separate installable packages per adapter | **(a)** while there's one team and one-to-two adapters; split to (b) once a second real adapter + external contributors exist. |
| **Transformation history representation** *(revised — supersedes the original "Mutation representation: diff vs. snapshot" row now that traceability is a hard requirement)* | (a) store only the final diff/snapshot per version (b) store the ordered Transformation Record log as the source of truth, with snapshots cached/derived for speed (c) full independent snapshots at every step, no operator log | **(b)** — traceability ("why did this change," "what's the blast radius") and complexity measurement both require the *operator log*, not endpoint states; full snapshots become a derived, regenerable cache (verified by round-trip replay), never the primary record. (c) is rejected: independent snapshots with no log can't answer "why." (a) is rejected: it can't support intermediate difficulty levels. |
| Severity/magnitude representation | (a) fixed severity weight per operator *type* (coarse) (b) continuous, parameterized magnitude per operator *instance* (c) severity learned/calibrated from observed agent performance | **(b)** — only this gives real "degrees of freedom" and reproducible, author-controlled complexity levels. (c) is worth revisiting once evaluation data exists, but is circular as a starting design — you'd need the evaluator to calibrate the very generator the evaluator grades. |
| Complexity aggregation (rolling many Transformation Records into a "difficulty level") | (a) single scalar (sum/weighted average of magnitudes) (b) multi-dimensional complexity vector, one axis per operator category, with a scalar rollup for convenience (c) blast-radius-weighted graph metric only | **(b)** — a pure scalar (a) or pure graph metric (c) would each collapse information you want to keep separable, since studying *how* agent performance differs across transformation *types* (not just overall difficulty) is part of the stated research goal. Keep the per-category vector as the analyzable ground truth; derive one scalar from it only for convenience labeling ("Level 1–5"). |
| Schema formalism | (a) Pydantic models only (b) JSON Schema as the canonical source, Pydantic generated from it | **(a)** now, exporting JSON Schema from Pydantic for any non-Python consumers; only invert if outside tooling needs to author the schema directly. |

**I recommend all defaults above** (lower-commitment, easier-to-reverse choices, except where your research requirement forced a specific answer) but want explicit sign-off before locking them in — the transformation-history and complexity-aggregation choices are now load-bearing for the whole framework, since Milestone 4 and the evaluator both build directly on them.

---

## 3. Implementation plan — milestones

Each milestone below is intentionally small enough to review and test before the next begins, per your instruction to prioritize incremental progress and testability over speed.

> **Milestone 0 implementation note (added during build, not part of the original design):**
> the sandbox this was built in has no PyPI access, so Pydantic couldn't be installed
> or exercised. Milestone 0 was implemented with zero dependencies instead — stdlib
> `dataclasses` for the domain model, hand-written structural parsing + referential
> integrity checks in `validate.py`, and `unittest` (not `pytest`) for the tests — so
> that it could actually be run and verified rather than shipped untested. The
> ground-truth JSON *format* is unaffected; only the Python parsing code differs from
> the original Pydantic-based sketch in Section 1. Recommendation: revisit Pydantic
> specifically at Milestone 3 (Generator), once there's a real environment with
> package-install access and validation ergonomics matter more at scale — not because
> the current approach is wrong, just because it was a constraint-driven choice worth
> re-examining deliberately rather than by default.

### Milestone 0 — Ground-truth schema + one static example
- **Goal**: Prove the "ground truth is the source of truth" loop before any generation automation exists to obscure bugs in it.
- **Components**: `ark/core/` (Pydantic domain model for `API` + `Flow` only, `schema_version: 0.1.0`, inline field docs), one hand-written example ground-truth file (single HTTP listener → one transform → one flow-ref), a validator, one hand-authored matching MuleSoft XML/DataWeave artifact, a golden-file test diffing a hand-written render against it.
- **Dependencies**: Pydantic, pytest. None on other milestones.
- **Risks**: Low — mainly the risk of over-scoping the schema before we've validated it against a real example.
- **Complexity**: Low.

### Milestone 1 — Static sample estate (multi-flow, still hand/code-authored) — ✅ complete

- **Goal**: Exercise the schema with a small but non-trivial estate (several APIs/flows referencing each other) to surface schema gaps before building a generator.
- **Components**: `examples/milestone1/` (4-application, 9-flow "Order Management" estate + README documenting the scenario/patterns/assumptions), extensions to `ark/core/models.py` and `ark/core/validate.py`.
- **Dependencies**: Milestone 0.
- **Complexity**: Low–Medium (as estimated).

**Schema gaps found and fixed (smallest additive change, schema_version 0.1.0 → 0.2.0):**

1. *No way to represent a network call from one flow to another API.* `FlowRefStep` is in-process only. Added **`ApiCallStep`** (`kind: "api-call"`, fields `id`, `name`, `description`, `target_api_id`) to represent the real mechanism by which one application depends on another (e.g. Process API → System API). It targets an API's `id`, not a specific flow — a caller depends on a published contract, not the target's internals.
2. *Only one trigger kind existed.* A nightly batch/reconciliation job has no HTTP entry point. Added **`SchedulerTrigger`** (`type: "scheduler"`, fields `cron_expression`, `description`).
3. *Two different reference-resolution scopes needed to coexist.* `FlowRefStep.target_flow_id` resolves only within the same `Application` (a real MuleSoft constraint: flow-ref cannot cross a deployable-artifact boundary); `ApiCallStep.target_api_id` resolves against the *whole estate* (also a real constraint: cross-application reuse happens over the network). `_check_referential_integrity` in `validate.py` now does both, and a dedicated test (`test_flow_ref_across_applications_is_rejected`) proves the first constraint is actually enforced, not just assumed.

**Gaps identified but deliberately deferred (no concrete need yet, so not implemented — avoiding speculative abstractions):**

- *Cross-application shared business logic below the API level* (e.g. a compiled-in shared library flow). Real Mule can't flow-ref across apps either, so this isn't a bug to fix — it's a genuine future concept (shared DataWeave/library modules with independent versions per consumer) worth modeling later, likely alongside the shared-schema-drift mutation work in Milestone 4, not before.
- *VM/queue-listener trigger.* Nothing in the current example needs one; queues aren't modeled at all yet (that's a later expansion milestone per the original scope note).

**Tests added** (`tests/test_milestone1.py`, 13 new tests, 19 total across the suite, all passing): multiple-APIs/multiple-flows-per-API structural checks, in-application shared-sub-flow reuse, the full Experience→Process→System `ApiCallStep` dependency chain, mixed trigger types on one API, a **backward-compatibility regression test** re-validating the Milestone 0 example (still `schema_version: 0.1.0`) under the updated validator, and five negative tests (cross-application flow-ref rejected, unknown API-call target rejected, duplicate ids across applications rejected, malformed scheduler trigger and api-call step rejected).

**Remaining limitations before Milestone 2**: the estate is still deliberately "clean" (consistent naming, no legacy versions, no duplicate logic) — that's intentional, reserved for the Milestone 4 mutation engine. No renderer/exporter exists yet for this larger estate (Milestone 2's job). Shared-library/versioned-dependency modeling remains an open, deferred design question flagged above.

### Milestone 2 — MuleSoft adapter v0.1 (exporter) — ✅ complete

- **Goal**: Render ground truth into realistic MuleSoft-shaped files automatically, with a rendering manifest and round-trip tests.
- **Components**: `ark/adapters/base.py` (technology-agnostic `TargetAdapter` interface + `RenderedEstate`), `ark/adapters/mulesoft/` (`renderer.py`, `manifest.py`, `adapter.py`).
- **Dependencies**: Milestone 1.
- **Complexity**: Medium (as estimated).

**Implementation note**: rendered with plain Python string-building, not Jinja2 as originally sketched — consistent with the zero-dependency choice made for Milestones 0–1 (no PyPI access in this build environment; see the Milestone 0 note above). Revisit alongside Pydantic once there's package-install access; the output format is unaffected either way.

**What was built**: a *general* adapter — unlike Milestone 0's one-off `render.py` (which only ever handled its one hard-coded flow shape), this renders any estate conforming to the schema: any number of applications, any number of flows per app, both trigger kinds, all four step kinds. One combined Mule XML file per application plus a minimal `api.yaml` per API (title/version/entry flow — not full RAML/OAS, deliberately). The **rendering manifest** (`manifest.json`) is the single authoritative artifact↔entity mapping: which file each Application/Flow/Step/API ended up in, plus a derived `dependencies` list (every `flow-ref` and `api-call` edge) so an evaluator can answer "which entity produced this artifact," "what exists," and "what depends on what" without re-deriving it from the XML. Ground-truth entity ids are deliberately **not** embedded as comments in the rendered artifacts — the manifest is the only source of that mapping, to avoid a second, independently-drifting provenance mechanism (the same drift concern the plan already flags as a top risk).

**Concepts that didn't map cleanly**: `ApiCallStep` carries no HTTP path/method (correctly — that's the callee's concern), so the renderer cross-references the target API's entry-flow trigger to derive one; if that resolution fails (target's entry flow isn't HTTP-triggered), the adapter raises `MuleSoftRenderError` rather than guessing — proven by a dedicated test. Real Mule project scaffolding (`pom.xml`, `mule-artifact.json`, environment `.properties`, API Manager policy bindings) is not generated; none of it affects how an agent would reason about flow/API/mapping logic.

**No schema pressure found** — Milestone 2 needed zero changes to `ark/core/`, which was the point. This is now a pinned test (`TestAdapterDidNotChangeCoreModel`), not just an assertion in prose.

**Tests added** (`tests/test_milestone2.py`, 12 tests; 30 total across the suite): golden-file tests for both example estates against fixtures in `tests/golden/`, a dedicated check that the general adapter reproduces Milestone 0's *original, independently hand-authored* golden XML byte-for-byte (proof that generalizing didn't silently change behavior on the case that mattered most), a determinism test (render twice, compare), manifest-correctness spot checks (entity presence, artifact mapping, both `flow-ref` edges to the shared `validate-order` sub-flow, all three `api-call` edges), a failure-mode test for the unresolvable-API-call case, and the core-model pin test.

**Remaining limitations before Milestone 3**: no generator yet — everything through Milestone 2 is still hand-authored ground truth. The adapter only renders what's already valid ground truth (it doesn't need to handle malformed input; `validate_ground_truth()` is assumed to run first). File-layout conventions (one XML per app) and the requester connector-config name are fixed adapter-side choices, not configurable yet — fine for now, worth revisiting if a real generator needs to vary them.

### Milestone 3 — Generator/Synthesizer (automated, parametrized, seeded) — ✅ complete

- **Goal**: Automatically produce estates of arbitrary size/shape from parameters, reproducibly.
- **Components**: `ark/generator/{config,vocabulary,seeds,topology,generator}.py`; `ark/core/serialize.py` (small, neutral core addition — the inverse of `validate.py`'s parsing, needed so generated estates can round-trip through real validation).
- **Dependencies**: Milestones 0–1 (stable schema).
- **Complexity**: Medium–High (as estimated).

**Implementation note**: no Faker or networkx — consistent with the zero-dependency choice made since Milestone 0 (no PyPI access in this build environment). The vocabulary is a small hand-written noun list instead of Faker; the dependency graph is a handful of plain Python dicts/lists instead of a networkx graph object (the graph here is simple enough — three fixed layers, feed-forward only — that a graph library wasn't pulling its weight). Revisit both if a future milestone needs proper graph algorithms (cycle detection, centrality, etc.) that plain Python starts to strain.

**Generation strategy**: strictly layered, feed-forward topology (Experience → Process → System, matching Milestone 1's hand-authored pattern) rather than arbitrary random connections — this structurally rules out the "every API connects to every other API" anti-pattern regardless of parameter values, since there's no cross-layer or same-layer edge to draw in the first place. `dependency_density` controls fan-out only *within* one layer transition; small downstream pools naturally produce fan-in (shared dependencies) without any special-cased "force sharing" logic. Every application gets one entry flow plus one reusable sub-flow; process/system apps may also get a secondary scheduler-triggered flow (`scheduled_job_ratio`), which either reuses the entry flow's sub-flow (`shared_component_frequency`) or gets an independent one — an organically-arising near-duplicate, a realistic seed for Milestone 4's mutation work without being a mutation itself.

**Deliberate design property**: business-noun naming is drawn independently per layer, so an app's name is never guaranteed to relate to what it actually depends on (see `examples/milestone3/README.md`'s worked example). This is intentional — an evaluator must trace real `ApiCallStep`/`FlowRefStep` edges rather than infer relationships from naming, which is a more honest test of an agent's reasoning.

**Reproducibility**: one `random.Random(seed)` instance, threaded explicitly through every choice point — never Python's global `random` state. Verified, not just claimed: a test disturbs the global `random` module state between two generation calls and asserts identical output. A `GenerationManifest` (seed + generator version + core schema version + full config) is returned alongside every generated estate — the "baseline recipe" a Milestone 4 mutation trajectory will be recorded as extending.

**Tests added** (`tests/test_milestone3.py`, 14 tests; 44 total across the suite): determinism (same seed+config → identical estate, including under disturbed global random state), different seeds → different-but-valid estates, real-file validation round-trip, MuleSoft adapter export, reproducible exports through the full generate→render pipeline, size scaling, a shared-dependency-emerges check verified empirically for a fixed seed (not asserted on faith), a layering-purity check (no direct experience→system calls), and config validation (negative counts, out-of-range densities, unsupported topology/naming styles all rejected).

**Generated examples**: `examples/milestone3/{small_seed1,small_seed2,medium_seed1_shared_dependency,large_seed42}.json`, each with a matching `.manifest.json`, produced by the deterministic `generate_examples.py` script.

**Remaining limitations before Milestone 4**: only one topology style, naming style, and vocabulary domain are implemented (by design — see the config's `SUPPORTED_*` sets); estates are always "clean" (no drift, no legacy versions, no duplicate-and-diverged logic beyond the organic near-duplicates described above) — introducing controlled inconsistency is explicitly Milestone 4's job, not this one's.

### Milestone 4 — Transformation engine (mutation as explicit operators) — ✅ complete

- **Goal**: Introduce controlled, measurable complexity as an ordered sequence of transformation operators (§1.7) — not batch randomization — each producing a full Transformation Record; support building a **trajectory** (baseline → progressively degraded).
- **Components**: `ark/mutation/{base,operators,registry,ledger,profiles,engine}.py`; one new public function on `ark/core/validate.py` (`validate_estate_object` — an in-memory entry point into the same referential-integrity checks, no schema/behavior change).
- **Dependencies**: Milestones 2–3.
- **Complexity**: High (as estimated).

**Six operators implemented**, all independent/composable, all guaranteed to leave the estate passing full validation (this is a hard design invariant, not just a test — an operator that broke referential integrity would make the estate unexportable, defeating the eval pipeline):

| Operator | What it changes | Never touches |
|---|---|---|
| `naming_drift` | An entity's display `.name` (1-3 compounding drift styles by severity) | `.id` fields — identity is always preserved |
| `documentation_decay` | A step's `.description` (truncate → generic placeholder → empty, by severity) | The field's presence (structurally required) |
| `duplicate_processing` | Clones an existing sub-flow, rewires ONE caller to the clone | The original flow and its other callers |
| `legacy_version_introduction` | Adds a sibling API + frozen flow (optionally missing the most recent step) | The original API/flow — purely additive |
| `schema_inconsistency` | Renames an `<word>Id` field in a DataWeave script to a different convention | Everything outside that one field |
| `dependency_change` | Repoints an `ApiCallStep`/`FlowRefStep` at a different, still-valid target | The intra-app / estate-wide scope rules from Milestone 1 |

**Ledger format**: see the table in the design note above (mutation_id, transformation_type, affected_entity_ids, original_state/transformed_state as **diffs of only the changed fields** — not full entity dumps, severity, rationale, sequence_index, timestamp [informational only], seed, record_schema_version).

**Profiles** (`ark/mutation/profiles.py`): Level 0 (clean, 0 mutations) through Level 3 (10 mutations, severity 0.5-0.9), each level's operator set a strict superset of the one below — Level 2 doesn't replace Level 1's issue types, it adds to them, and Level 3 adds again on top.

**A real bug class this process caught**: three separate no-op bugs (an operator computing a "mutated" value that happened to equal the original — see `examples/milestone4/README.md` for the full account). Caught because the engine enforces `original_state != transformed_state` as a hard invariant (`MutationEngineError` otherwise), not because it was manually noticed. All three are fixed with deterministic escalation guards, and a 20-seed regression test plus an ad hoc 900-trajectory/5700-mutation stress run (in the sandbox, not committed) confirm the fix holds broadly, not just for the one seed that first exposed it.

**Tests added** (`tests/test_milestone4.py`, 22 tests; 66 total across the suite): baseline-never-mutated (both at the engine level and the individual-operator level), reproducibility (same seed+profile → identical estate and ledger-minus-timestamp, including under disturbed global random state), ledger completeness (every record's fields populated and consistent, no-op detection across 20 seeds), transformed-estate validity (full JSON round-trip through `validate_ground_truth`, export through the Milestone 2 adapter, Level 0 is a true no-op), graceful degradation (the tiny Milestone 0 estate under Level 3 stops early rather than crashing), every operator individually invocable and independently valid, naming_drift's id-preservation guarantee, duplicate_processing's additive-only guarantee, profile ordering/additivity, and a ledger-enables-precision/recall-scoring test (proving the ledger's shape is usable for evaluation, without building the evaluator itself).

**Example transformation trajectory**: `examples/milestone4/` — Milestone 1's baseline carried through Level 1/2/3, each with its transformed estate and full ledger, plus a walkthrough of specific real ledger entries.

**Deliberately deferred from the original Milestone 4 plan (flagging rather than silently dropping)**:
- The original plan (this section, pre-revision) also called for stubbing a second adapter to pressure-test the core/adapter boundary, and a **complexity aggregation function** (the multi-dimensional per-category vector decided in Section 2). Your actual Milestone 4 instructions scoped this milestone to the operators/ledger/profiles only, so neither was built here. Both remain open — the second-adapter stub is low-effort and worth doing opportunistically; the complexity aggregation function is the more substantive gap and is the natural next piece once an evaluator (Milestone 6) needs to plot performance against complexity level.

### Milestone 5 — Traffic simulator
- **Goal**: Generate representative synthetic runtime traffic (volume seasonality, latency, error rates) tied to the estate's dependency graph.
- **Components**: `traffic/`.
- **Dependencies**: Milestone 3 (needs the dependency graph). Can run in parallel with Milestone 4.
- **Risks**: Over-investment beyond what eval questions actually need.
- **Complexity**: Medium.

### Milestone 6 — Evaluator/scorer — ✅ complete (6.1-6.5 complete)

- **Goal**: Score AI agent outputs against ground truth + full transformation history: accuracy (precision/recall per issue *category*), entity-level localization accuracy, confidence calibration (Brier score / ECE), and cross-report performance-vs-complexity analysis.
- **Full design**: see [`Ark_Evaluator_Design.md`](./Ark_Evaluator_Design.md) (Revision 2) — data flow, evaluation philosophy, scoring hierarchy, agent-output contract, dynamic complexity model, manifest expansion, module architecture, and a phased 6.1-6.5 implementation plan. Not repeated here to avoid drift between two copies of the same design.
- **Components**: `ark/evaluator/{issues,schema,parser,matcher,metrics,calibration,explanation,complexity,report,orchestrator,analysis}.py` — four modules (`issues.py`, `schema.py`, `explanation.py`, `analysis.py`) added beyond the original sketch; `report.py`/`orchestrator.py` supersede the original sketch's single `reports.py`/`evaluator.py` naming, and `analysis.py` supersedes the design doc's sketched `complexity.py`-resident `correlate_with_reports()` — see the design doc for why on both.
- **Dependencies**: Milestone 4 (ledger); Milestone 2 (step-level rendered names in the manifest — landed in 6.2, additive-only, golden fixtures regenerated).
- **Risks**: entity-resolution ambiguity when matching agent claims to ground-truth entities via the manifest — handled in 6.2 via an explicit `ambiguous` resolution status (never a silent guess), proven against a real duplicate-name case already present in Milestone 1's estate; ECE unreliable at low sample sizes (handled via a documented null-below-threshold, not hidden; 6.3); cross-report complexity correlation needs real usage volume to be meaningful, not just unit tests — addressed in 6.5 with the same null-below-threshold discipline applied to correlation coefficients, plus a multi-profile/multi-seed worked example generating 16 reports specifically so the committed example has real, non-degenerate signal in it.
- **Complexity**: Medium–High (as estimated), split across five sub-milestones (6.1-6.5) rather than one block — see the design doc.

**Milestone 6.1 — evaluator foundation ✅ complete**: `ark/evaluator/issues.py` (`derive_issues()` — consolidates raw `MutationRecord`s into deduplicated, observable `Issue`s; handles both compounding, where multiple records on the same entity collapse into one Issue with a `mutation_count`, and the "net-zero" case, where compounding records that cancel out entirely — e.g. a `dependency_change` reverted three records later in the real Milestone 4 example ledger — produce no Issue at all, since there is nothing an agent inspecting only the final artifacts could observe), `ark/evaluator/schema.py` (`parse_agent_output()` — validates the exact required agent-output JSON contract; enforces technology independence by normalizing any `issue_type` outside Ark's six-operator taxonomy to `"other"`, so a MuleSoft-syntax complaint can never match a real Issue), and `ark/evaluator/complexity.py` (`compute_trajectory_complexity()` — the agent-independent half of the dynamic complexity model: mutation count, distinct issue count, compounding, severity stats, transformation diversity, dependency-graph blast radius, and a structural interaction score, rolled into an overridable-weight scalar). 20 new tests (86 total), including regression tests proving neither module mutates the ledger or estate it's handed. `correlate_with_reports()` (the agent-dependent, cross-report half) is deferred to Milestone 6.5, since it needs `EvaluationReport`, which doesn't exist until 6.4.

**Milestone 6.2 — manifest expansion + finding matcher ✅ complete**: additive-only expansion of `ark/adapters/mulesoft/manifest.py` + `renderer.py`'s `_step_entity()` giving every Step entity a rendered-visible label (TransformStep/ApiCallStep reuse their existing `.name`; FlowRefStep gets a synthesized `"reference to '{target}'"` label; LoggerStep uses its message text) — no rendered XML/YAML content changed, only manifest entity labels, so the golden `manifest.json` fixtures were regenerated (a mechanical, additive-only diff, verified programmatically before committing) while the golden `.xml`/`.yaml` fixtures were untouched. `ark/evaluator/parser.py` (`resolve_entity_reference()` — resolves an agent's `artifact_reference`/`entity_reference` strings to internal entity ids using only the manifest: exact path match, unambiguous basename fallback, exact name/alias match scoped to the resolved artifact, then a whole-manifest fallback; anything matching more than one entity is reported `ambiguous`, never guessed at). `ark/evaluator/matcher.py` (`match_findings()` — reports `category_correct`, `entity_correct`, and `artifact_reference_correct` as independent signals per finding, so "wrong entity, right issue type" and "right entity, wrong issue type" are distinguishable rather than collapsed into one score; no blended accuracy metric, that's 6.3). One real design correction made during implementation: FlowRefStep was originally also going to be aliased to its bare target-flow name, but testing showed this makes the step permanently ambiguous with its target Flow entity in every case (a FlowRefStep can only target a Flow in the same Application, so both always render into the same file) — the alias was dropped; the step is only resolvable via its full synthesized label, and the bare name correctly resolves to the Flow itself. 31 new tests (117 total): manifest traceability, entity resolution (including a real duplicate-name case already present in Milestone 1's own estate — two sub-flows both named `log-request-sub-flow`), the five required matcher scenarios, and isolation regression guards (AST-based import checks proving `parser.py`/`matcher.py`/`schema.py` never import `ark.core.models` or `ark.mutation.ledger`/`engine`, plus an end-to-end no-mutation check).

**Milestone 6.3 — evaluation metrics + confidence calibration ✅ complete**: `ark/evaluator/metrics.py` (`compute_category_metrics()`/`compute_category_metrics_by_type()` — precision/recall/F1 for issue-type detection, using a strict true-positive definition shared everywhere in this milestone: a finding is only a true positive if it resolved to a specific real Issue AND named that Issue's actual type; `compute_entity_localization_metrics()` — a genuinely separate precision/recall/"localization accuracy" family that only requires the entity to be right, independent of category, so the two can and do disagree on the same match set), `ark/evaluator/calibration.py` (`compute_calibration()` — Brier score plus ECE with the same documented sample-size gate from the original design doc, importing `metrics.py`'s strict correctness definition rather than redefining "correct" a second way), `ark/evaluator/explanation.py` (`extract_explanation_signals()` — four shallow, deterministic, rule-based structural signals: does the explanation mention the claimed artifact, does it reference the matched issue's real observable symptom, does it contain causal language, is it ungrounded in either; explicitly not an LLM judge, by instruction), and a small addition to `complexity.py` (`TrajectoryPerformanceRecord`/`build_trajectory_performance_record()` — the complexity-performance tracking hook: pairs one trajectory's complexity profile with its full metrics/calibration results, exposing two convenience scalars matching the requested example shape while always keeping every underlying metric object alongside them, never hiding failure modes behind one number). 21 new tests (138 total): classification metrics (perfect/missing/extra/mixed), localization tests proving category and entity metrics diverge on purpose, calibration tests (perfectly calibrated / overconfident / underconfident, plus the exact "same accuracy, different calibration" scenario from the milestone brief), and a full-pipeline regression test confirming ground truth, the mutation ledger, the rendering manifest, and the transformed estate are all untouched. A worked example evaluation report (hand-crafted agent output against a real Milestone 1 trajectory) is committed in `examples/milestone6/`, including an honestly-documented nuance it surfaced: when one entity carries two distinct real issues, a single wrong-category claim on it can only ever localize one of them, leaving the other a false negative even though "the agent was looking at the right place."

**Milestone 6.4 — report assembly + orchestration ✅ complete**: `ark/evaluator/report.py` defines `EvaluationReport` (seven sections — `EvaluationMetadata`, `EnvironmentSummary`, `TransformationSummary`, `IssueSummary`, `AgentPerformanceSummary`, `FailureAnalysis`, `ResearchAnalysisHooks`) and `assemble_report()`, a pure packaging function that performs no scoring of its own — every number it reports was already produced by 6.1-6.3. `FailureAnalysis` keeps five genuinely independent buckets (missed issues, hallucinated findings, wrong-category predictions, correct-location-but-wrong-diagnosis, overconfidence patterns) rather than one blended "wrong" list. `ark/evaluator/orchestrator.py`'s `evaluate(transformed_estate, mutation_ledger, rendered_manifest, agent_output)` is the single pipeline entry point, sequencing derive_issues → parse_agent_output → parse_and_resolve_findings → match_findings → metrics/calibration/explanation/complexity → assemble_report; it never renders anything itself (callers render via whichever adapter first), which is what keeps it technology-independent without needing to avoid an import it never had a reason to make. One small, additive change to a prior milestone: `issues.py` gained `derive_issue_diagnostics()` so the Issue Summary section could report net-zero/cancelled mutation groups — information `derive_issues()` was silently discarding with no way to recover it afterward without duplicating its private grouping logic elsewhere; `derive_issues()` itself is byte-for-byte unchanged. Serialization is two-directional for the first time in the evaluator subsystem: `report_to_dict()`/`report_to_json()` plus a lightweight, non-migrating `report_from_dict()` (explicit per-type reconstruction functions, no generic reflection, no schema-version dispatch — deliberately not "complex reconstruction logic"), so a historical report can be reloaded as real typed objects for future batch analysis, not just read back as JSON. 20 new tests (158 total): determinism excluding the timestamp field, a pinned-timestamp full-equality variant, empty/clean-estate agent output handling, all five failure-analysis buckets (including a purpose-built "right entity, wrong diagnosis" case), full JSON round-trip, no-mutation regression across estate/ledger/manifest/agent-output and reused metrics objects, and an AST-based check that no evaluator module — including the two new ones — imports `ark.adapters` at all. A full worked example (`examples/milestone6/report_example.json`) reuses Milestone 6.3's example trajectory and agent output so the two committed examples describe the same evaluation at two levels of detail.

**Milestone 6.5 — cross-report complexity correlation and experiment analysis ✅ complete**: `ark/evaluator/analysis.py` — a pure consumer of `EvaluationReport` objects (its only import from `ark.evaluator` is `report.py`; no adapter, mutation, or core-model import at all) that aggregates a batch of reports into one `ExperimentAnalysis`. `ComplexityAnalysis` bins reports into five fixed, absolute complexity bands (not derived from whatever min/max one batch happens to contain, so "the 0.4-0.6 band" means the same thing across different experiment runs) and computes a hand-rolled Pearson correlation between `complexity_score` and each performance metric, gated `None` below a minimum sample size exactly like calibration's ECE, with a fixed disclaimer on every statistic that this is an observed association, never a causal claim. `TransformationImpactAnalysis` reports each transformation type's and each exact transformation-combination's observed performance alongside the clean (`mutation_count == 0`) baseline from the same batch, plus a degradation delta with a consistent sign convention: positive always means "worse than baseline," whether the underlying metric is higher-is-better (category F1, localization accuracy) or lower-is-better (calibration ECE). `CalibrationDriftAnalysis` tracks the confidence-vs-accuracy gap across the same complexity bands. A genuine structural finding surfaced during implementation, documented rather than smoothed over: `metrics.py`'s recall is `None` whenever there are zero real Issues (an existing, frozen Milestone 6.3 rule), which is always true for a clean baseline trajectory — so category-F1/localization degradation against a clean baseline is structurally unmeasurable no matter what the agent does, while calibration-ECE degradation is not (ECE only needs the agent to make claims, not for real issues to exist). Serialization is one-directional by explicit scope decision (`analysis_to_dict()`/`analysis_to_json()` only, no `analysis_from_dict()`). 20 new tests (178 total): determinism excluding the timestamp, order-independence of aggregate averages, empty-report-list and missing/malformed-report-file handling, correlation `None`-gating below the sample-size threshold plus a sign-sanity check (a flat, non-degrading agent must never show a spurious negative correlation), baseline presence/absence propagating correctly to `None` degradation, by-type vs. by-combination breakdowns proven distinct, the degradation sign convention checked against hand-computed values, JSON round-trip, confirmation that no `analysis_from_dict()` exists, no-mutation regression, and an AST-based check that `analysis.py` imports only `ark.evaluator.report` from within the evaluator package. A full worked example (`examples/milestone6/analysis_example.json`, generated by `examples/milestone6/generate_analysis_example.py`) runs a simulated agent — whose accuracy degrades and confidence overconfidence grows with complexity, by construction — across all four difficulty profiles and four seeds each (16 reports total), giving the committed example real, non-degenerate correlation and degradation signal rather than a trivially empty one.

### Milestone 7 — Agent harness + experiment runner — ✅ complete
- **Goal**: the infrastructure required to run Ark end-to-end against a **real** AI agent: hand it rendered artifacts as its only input, collect structured JSON findings back, and automatically produce `EvaluationReport`s and an `ExperimentAnalysis` — no UI yet, by instruction; this is the plumbing, not a CLI or dashboard.
- **Components**:
  - `ark/harness/` (zero-dependency, inside `ark`): `contract.py` (`AgentClient` — the one-method `generate(prompt) -> str` interface everything else depends on), `prompt.py` (`build_agent_prompt(artifacts: dict[str, str])` — sources the issue-type taxonomy and required JSON shape straight from `ark.evaluator.schema`, so the prompt can never drift from what `parse_agent_output()` accepts), `response_parsing.py` (`extract_json_object()` — recovers a JSON object from raw LLM text, whether bare, fenced, or surrounded by prose), `runner.py` (`run_agent_harness(artifacts, agent_client) -> dict` — the one function that actually calls an agent), and `scripted_client.py` (`ScriptedAgentClient` — a deterministic, offline reference/test double).
  - `integrations/anthropic_agent_client.py` (deliberately **outside** `ark/`): a real, Anthropic-SDK-backed `AgentClient`. Lazily imports `anthropic` only when it needs to build its own default client, so importing this module — or even instantiating the class with a caller-supplied `client=` — never requires the SDK to be installed. See its own and `integrations/__init__.py`'s docstrings for the reasoning below.
  - `ark/experiment/` (inside `ark`, zero new third-party dependency): `spec.py` (`TrajectorySpec` — a declarative baseline-path-or-`GeneratorConfig` + profile + seed request) and `runner.py` (`run_trajectory_spec()` / `run_experiment()` — the full Generator/hand-authored → Mutation → Render → Agent Harness → `evaluate()` → (batch) `analyze_reports()` arc, with optional JSON persistence to an output directory via the existing `report_to_json()`/`analysis_to_json()` serializers).
- **A deliberate architectural decision, made explicit rather than assumed**: Ark's core (`ark/core`, `ark/generator`, `ark/mutation`, `ark/adapters`, `ark/evaluator`) has zero third-party dependencies, and the agent being evaluated is conceptually **external** to that core — so the one piece of this milestone that needs a real vendor SDK (`anthropic`) lives in a sibling top-level directory, `integrations/`, never inside `ark/`, and is only ever imported one-way (integrations depend on `ark.harness`'s contract; `ark` never depends on `integrations`) — the same directional-boundary discipline `ark/adapters` already established for rendering targets, now applied to the agent side. `ark/harness/` itself stays zero-dependency: it ships only the abstract `AgentClient` contract and the offline `ScriptedAgentClient`, never a vendor SDK.
- **Dependencies**: Milestone 6 (the full evaluator: `orchestrator.evaluate()`, `analysis.analyze_reports()`); Milestone 3 (`ark.generator`, as an alternative estate source alongside hand-authored files); Milestone 2 (adapters, to render before handing artifacts to the harness).
- **Risks**: entity resolution against a heuristic/weak real agent's imprecise references was already exercised by Milestones 6.2-6.4's conservative-matching tests; the genuinely new risk here — an agent's raw text response not being clean JSON — is handled by `response_parsing.py`'s bare/fenced/prose-wrapped extraction, with a documented, tested edge case (a bare top-level JSON *list* is rejected outright rather than having a nested object salvaged out of it, a real bug this milestone's own test-writing caught and fixed).
- **Complexity**: Medium, as estimated.
- **Tests** (`tests/test_milestone7.py`, 33 tests): prompt construction (every artifact path/content present, deterministic regardless of dict order, the real taxonomy listed); response parsing (bare/fenced/prose-wrapped JSON, no-JSON-present, and the bare-list rejection case); `ScriptedAgentClient` determinism and prompt-recording; `run_agent_harness`'s signature enforcing a plain `dict[str, str]`, never a richer object; an AST-based check that no `ark/harness/` module imports `ark.core.models`/`ark.mutation.*`/`ark.adapters.*`, plus a wiring-level spy test proving the real call into `run_agent_harness()` during a full trajectory run receives exactly `rendered.artifacts` and nothing manifest-shaped; `TrajectorySpec`'s mutually-exclusive-source validation; `run_trajectory_spec()`/`run_experiment()` against both hand-authored and generator-produced estates, including output-directory persistence and round-trip reload; a full Generator → Mutation → Render → Agent Harness → Report → Analysis arc test; an AST-based sweep proving nothing under `ark/` imports `integrations` or `anthropic`; and `AnthropicAgentClient` tests using a fake/duck-typed SDK object (no real network call, no real `anthropic` install needed), including the graceful `ImportError` path when no client is supplied and the package genuinely isn't installed.
- **Deviation from the original sketch**: the plan originally scoped Milestone 7 as `ark generate|mutate|export|simulate-traffic|score` — a full CLI wrapping every subsystem. Your actual instruction scoped this milestone to the agent harness + experiment runner specifically ("do not build UI yet"), so that's what was built; a thin CLI/console-script wrapper around `ark.experiment.run_experiment()` remains open, deferred future work, not part of this milestone.
- A full worked example — `HeuristicNamingAgentClient`, a genuinely offline, ground-truth-blind agent that only reads rendered text for two real naming irregularities — runs six trajectories (all four profiles against the Milestone 1 baseline, plus two generator-produced estates) end-to-end via `run_experiment()`, committed at `examples/milestone7/`.

### Milestone 8 — Ark Interactive UI — ✅ complete
- **Goal**: a browser-based interface for demonstrating and running Ark experiments — a thin layer over the now-complete Milestone 7 experiment runner, purely for interactive demonstration and research use. No backend redesign, no new scoring logic, no database, no auth, no cloud deployment.
- **Stack**: Streamlit, no frontend framework, local execution only, per instruction.
- **Components**:
  - `ark/ui/logic.py` (zero Streamlit dependency): every piece of non-widget logic — building `AgentClient`s and `TrajectorySpec`s from UI selections, a thin passthrough to `run_experiment()`, and extraction of already-computed `EvaluationReport`/`ExperimentAnalysis` fields into plain display-ready dicts/lists. Kept Streamlit-free specifically so it's fully unit-tested (`tests/test_milestone8.py`) without Streamlit installed at all.
  - `ark/ui/app.py`: the actual Streamlit page (`streamlit run ark/ui/app.py`) — sidebar configuration (agent choice, estate source, profile, seed, trajectory count), a Run Experiment button, a Results Dashboard (Environment Summary / Agent Performance / Failure Analysis), Research Visualization charts (complexity vs. performance, transformation-type impact, calibration drift, all straight from `ExperimentAnalysis`), an Artifact Viewer with an explicit "🟢 Visible to Agent" / "🔒 Hidden from Agent" separation, and JSON export buttons for both the `EvaluationReport` and the `ExperimentAnalysis`.
- **A small, additive, backward-compatible extension to Milestone 7's runner, made because the UI had a genuine, spec-required need** (the Artifact Viewer) **for data the runner already computed but discarded**: `ark/experiment/runner.py` gained `TrajectoryRunResult` (a report *plus* the rendered artifacts that trajectory's agent was shown) and `run_trajectory_spec_with_artifacts()` — the real implementation, with `run_trajectory_spec()` now a thin wrapper returning just `.report`, signature and behavior byte-for-byte unchanged, exactly the same "existing function must not change, but more of what it already computes needs to be exposed" pattern `ark.evaluator.issues`'s `derive_issues()`/`derive_issue_diagnostics()` established in Milestone 6.4. `ExperimentRunResult` similarly gained a defaulted `artifacts_by_label` field, populated in-memory only (never persisted to `output_dir` — no database, per instruction).
- **The demo agent, reused rather than duplicated**: the UI's "ScriptedAgentClient" option really does return a `ark.harness.scripted_client.ScriptedAgentClient` instance, but its responder is `HeuristicNamingAgentClient`'s real, offline, ground-truth-blind naming-irregularity heuristic — promoted in this milestone from Milestone 7's one-off example script into a proper, reusable `ark/harness/heuristic_client.py` (the example module now just re-exports it, so nothing that already imported it needed to change), so the interactive demo has something genuinely non-trivial to show without a network call.
- **Architecture boundary, enforced not just documented**: `ark/ui/logic.py` imports `ark.experiment`, `ark.evaluator`, and `ark.harness` (the required architecture) plus `ark.mutation.profiles.PROFILES` for profile *names and descriptions only* (plain config data, not the engine/operators/ledger) — never `ark.mutation.engine`/`operators`/`ledger` directly. `integrations.anthropic_agent_client` is referenced only as a lazy, function-local import inside `build_agent_client()`, exactly the same pattern `AnthropicAgentClient` itself uses for the `anthropic` package — both the "core pipeline never imports integrations" rule and this narrow, checked exception are enforced by AST-based tests (one existing Milestone 7 test was updated to scope its sweep to Ark's core pipeline subpackages specifically, plus a new test confirming ark/ui's reference is function-local, not module-level).
- **Dependencies**: Milestone 7 (`run_experiment()`, `AgentClient`, `ScriptedAgentClient`).
- **Risks**: Streamlit (and its `pandas` dependency) could not be installed or exercised in the environment this milestone was built in (no network access) — mitigated by keeping all real logic in the Streamlit-free `logic.py` (fully tested here) and including a `streamlit.testing.v1.AppTest`-based end-to-end test that is currently skipped in this environment but will run automatically in any environment with the `ui` extra installed, rather than only ever being smoke-tested by hand.
- **Complexity**: Medium, as estimated.
- **Tests** (`tests/test_milestone8.py`, 32 tests, 1 skipped here for the reason above): agent selection (scripted always available, Anthropic hidden until configured, no API key needed for the default agent); TrajectorySpec building for both estate sources plus invalid-input rejection; a full scripted-agent experiment run with zero environment configuration; display-extraction correctness for all three Results Dashboard sections and all three Research Visualization chart data sources; the Artifact Viewer's isolation guard both passing on real artifacts and catching a simulated manifest-leak and a non-string-value case; a wiring-level check that the artifacts the UI would display for a trajectory are byte-for-byte identical to an independently-re-rendered copy of that same deterministic trajectory; export-helper equivalence with the underlying serializers; and AST-based import-boundary checks (no mutation internals from either `logic.py` or `app.py`'s source, and that the `ark/ui` package as a whole does import from all three required subsystems).
- A full worked-example note: this UI is meant to be run interactively (`streamlit run ark/ui/app.py`), not regenerated into a committed static artifact the way Milestones 6-7's example scripts were — see `README.md`'s run instructions.

### Milestone 9 (future work, not in initial scope): CLI
- **Goal**: what the original Milestone 7 sketch called for — a thin `ark generate|mutate|export|run-experiment` command-line wrapper around `ark.experiment.run_experiment()`, as an alternative to Milestone 8's interactive UI for scripted/CI use.
- **Dependencies**: Milestone 7 (this is purely a wrapper around it — no new orchestration logic of its own).
- **Risks/Complexity**: Low — mostly integration/glue, now that every underlying piece it would call already exists and is tested.

### Milestone 10 (future expansion, not in initial scope)
- **Goal**: Expand MuleSoft adapter to connectors, policies, queues, schedulers; expand beyond MuleSoft to a second real adapter.
- **Dependencies**: Milestones 0–8 proven out.
- **Risks/Complexity**: TBD — deliberately deferred per your "initial focus on APIs and flows, then expand" instruction.

---

## 4. Ordered checklist (dependencies marked)

1. [x] **M0**: Domain model for API + Flow, validator, one hand-written example, golden-file test — *no dependencies, start here*
2. [x] **M1**: Multi-flow static example estate, schema revisions as needed — *depends on M0*
3. [x] **M2**: MuleSoft adapter v0.1 (renderer + manifest) — *depends on M1*
4. [x] **M3**: Automated generator/synthesizer — *depends on M1* (parallel with M2 — both only need the stable schema, not each other)
5. [ ] **M5**: Traffic simulator — *depends on M3* (parallel with M4)
6. [x] **M4**: Mutation engine + mutation ledger + second-adapter stub — *depends on M2 and M3* (second-adapter stub still open, flagged in the M4 write-up)
7. [x] **M6**: Evaluator/scorer — *depends on M4 (and M2's rendering manifest)*
8. [x] **M7**: Agent harness + experiment runner — *depends on M6*
9. [x] **M8**: Interactive UI — *depends on M7*
10. [ ] **M9**: CLI — *depends on M7; a thin wrapper, now that the underlying pieces exist*
11. [ ] **M10**: Expansion (more MuleSoft components, second real adapter) — *depends on everything above; out of scope for now*

**Parallelizable once M1 is done**: M2 (adapter) and M3 (generator) have no dependency on each other. **Parallelizable once M3 is done**: M5 (traffic) and the mutation-operator design work for M4.

---

## 5. Testing & documentation strategy (applies from Milestone 0 onward)

- **Unit tests**: schema validators (each constraint independently), individual mutation operators in isolation.
- **Integration tests**: full generator pipeline (seed → estate); exporter **round-trip** tests (render → re-parse → assert reconstructs the same ground truth) — this is the single highest-value test class, since agent-eval validity depends entirely on ground truth and rendered artifacts staying semantically identical.
- **Golden-file tests in CI**: commit fixed (ground-truth, rendered-artifact) pairs; CI re-renders and diffs — the tripwire against silent drift.
- **Schema-version pinning**: every ground-truth file carries `schema_version`; no implicit "latest wins."
- **Reproducibility**: every generation/mutation call takes an explicit seed; never ambient randomness. Ground-truth schema versioned with semver (patch = doc/clarification, minor = additive field, major = breaking, requires migration).
- **Documentation**: README as a layered map (problem → pipeline diagram → per-module detail); one lightweight ADR per ambiguous decision from Section 2; the ground-truth schema itself is inline-documented field-by-field as the primary onboarding artifact.

---

## Next step

This is a design proposal, not a commitment — nothing has been coded. Please review:
1. The architecture (Section 1) and the adapter boundary in particular, since that's what keeps this from becoming a one-off MuleSoft tool.
2. The five ambiguous-decision recommendations (Section 2) — these are the ones with real cost to reverse later.
3. The milestone plan and ordering (Sections 3–4) — tell me if you want to reorder, merge, or split any of them.

Once you approve (or request changes), I'll begin with **Milestone 0** only, and stop for review before moving to Milestone 1.
