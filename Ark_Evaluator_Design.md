# Ark Evaluator / Agent Scoring — Design Document (Milestone 6)

Status: **DESIGN ONLY — no code written. Awaiting final approval per your instruction.**

Revision 2: incorporates your decisions on (1) dynamic, continuous complexity modeling
instead of fixed levels, (2) the approved step-level manifest expansion, (3) the exact
structured agent-output contract, and (4) reinforced technology independence. Revision 1's
three open questions are resolved below (Section 9) rather than left open.

This document is a companion to `Ark_Architecture_and_Plan.md`, scoped to the evaluator
subsystem — the piece that turns Ark from "a thing that generates and mutates synthetic
estates" into "a thing that objectively scores how well an AI agent reasons about them."
Milestones 0-4 (schema, adapter, generator, mutation engine) are the prerequisite
infrastructure this design builds on; the only prior-milestone code this design touches is
the additive manifest expansion in Section 5.5 (Milestone 2's `ark/adapters/mulesoft/`) —
everything else builds on top without modification.

---

## 1. What the evaluator consumes, and how the pieces interact

```
baseline_estate (Milestone 3)  ──┐
                                  ├──> mutation engine (Milestone 4) ──> transformed_estate + MutationLedger
transformed_estate ───────────────────────────────────────────────────────────┐
                                                                                │
                                                                                ▼
                                                        adapter.render(transformed_estate)
                                                          (Milestone 2 — any TargetAdapter)
                                                                                │
                                                                                ▼
                                                          RenderedEstate { artifacts, manifest }
                                                                                │
                                    ┌───────────────────────────────────────────┘
                                    ▼
                    [ artifacts handed to an AI agent — OUTSIDE Ark entirely ]
                                    │
                                    ▼
                              AgentOutput (the agent's claims)
                                    │
                                    ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │  ark/evaluator/                                                              │
   │                                                                              │
   │  MutationLedger ──> issues.py ──> Issues (deduplicated, observable-state)    │
   │                                        │                                     │
   │  AgentOutput ──> schema.py (validate) ──> parser.py (resolve via manifest)   │
   │                                        │        │                           │
   │                                        ▼        ▼                           │
   │                                     matcher.py (Issues x Findings -> scores) │
   │                                        │                                     │
   │                                        ▼                                     │
   │              metrics.py + calibration.py + explanation.py                   │
   │                                        │                                     │
   │                    complexity.py (agent-independent complexity profile)     │
   │                                        │                                     │
   │                                        ▼                                     │
   │                        orchestrator.py -> report.py -> EvaluationReport     │
   │                                        │                                     │
   │                                        ▼ (across many reports, Milestone 6.5)│
   │           analysis.py's analyze_reports() -> ExperimentAnalysis (complete)  │
   └────────────────────────────────────────────────────────────────────────────┘
```

(`report.py`/`orchestrator.py` are this diagram's final names for what Section 6's original
sketch called `reports.py`/`evaluator.py` — renamed during Milestone 6.4 implementation to
match every other evaluator module's singular naming, and to avoid `evaluator.py` colliding
with the package name `ark.evaluator` itself. Similarly, the cross-report analysis originally
sketched as `complexity.py`'s `correlate_with_reports()` in Section 4.4/8 below landed instead
as its own module, `analysis.py`, with its entry point named `analyze_reports()` — see
Section 8's Milestone 6.5 write-up for why.)

**Critical point this diagram is trying to make concrete**: the agent never sees the
baseline estate, the ledger, or any ground-truth id. It only ever sees the *rendered
artifacts* (XML, DataWeave, YAML) — exactly what a human engineer opening the exported
MuleSoft project would see. Its "findings" are necessarily expressed in terms of what's
*visible in those files* (file paths, flow names as rendered, API display names) — never
in terms of Ark's internal entity ids. This one fact drives most of the design below: the
evaluator's job includes *resolving* the agent's artifact-visible references back to
ground-truth entity ids (via the rendering manifest), not just comparing two id sets.

---

## 2. Evaluation philosophy

The stated goal — "evaluate reasoning, not file-diffing" — has a concrete consequence:
**the ledger's raw per-mutation records are not what the agent should be scored against
directly.** The agent observes the *final* transformed estate, not the mutation history.
Milestone 4 already showed why this matters: in the Level 3 example trajectory, the same
step (`step-inventory-build-response`) was hit by `documentation_decay` *twice*, first
truncated then emptied. An agent looking at the final artifact sees one thing: an empty
description. It cannot and should not be expected to report "two separate decay events."

This means there's a step missing from every version of this pipeline I've seen sketched
so far (including the module list in your message): **before matching, the ledger's raw
records must be consolidated into a deduplicated set of observable Issues** — one entry
per (affected entity, transformation type), carrying the *cumulative* before/after state
and the *highest* severity among the contributing records. This is why the module list in
Section 6 adds an `issues.py` that isn't in your original sketch. Everything else follows
your structure closely.

A second philosophy point worth stating explicitly: a Level 0 (clean, unmutated) estate is
a valid and useful evaluation input — it has zero Issues, so *every* agent finding against
it is definitionally a false positive. This is the cleanest way to measure an agent's
hallucination/over-triggering rate, independent of its detection ability, and the design
below treats it as a first-class case rather than a degenerate one.

---

## 3. Scoring hierarchy — detection, entity, and explanation are three different questions

Your example makes the right distinction: "something is wrong with CustomerAPI" and
"CustomerAPI v2 has documentation decay caused by missing migration notes" should not
score the same. I'm proposing three independent axes per finding, not one blended score:

| Axis | What it measures | Values |
|---|---|---|
| **Category match** | Did the agent name the right *kind* of issue? | boolean |
| **Entity match level** | How precisely did the agent localize it? | `none` / `app_level` (right application, wrong or no specific entity) / `exact_entity` (right entity id) |
| **Explanation quality** | Did the agent's free-text explanation capture the *why* (not just the *what*)? | 0.0-1.0, secondary/optional |

A finding is a **true positive** only if category match is true AND entity match level is
`exact_entity`. `app_level` matches are recorded and reported separately (partial credit,
never blended into the headline precision/recall numbers) — this keeps the primary metric
simple and auditable while still capturing "the agent was in the right neighborhood."
Explanation quality is scored but reported as its own secondary metric, not folded into
detection accuracy, specifically so the headline numbers stay simple, reproducible, and
not dependent on a fuzzier text-quality judgment.

---

## 4. Scoring metrics

### 4.1 Detection accuracy (precision / recall / F1)

Standard definitions, applied per-estate (one transformed estate + one agent run = one
evaluation):

- **True positive**: an agent finding that matches an Issue (category match + exact entity match).
- **False positive**: an agent finding matching no Issue at all (includes *all* findings on a Level 0 estate, and any `app_level`-only match, by the definition above).
- **False negative**: an Issue with no matching finding.

Precision = TP / (TP + FP); Recall = TP / (TP + FN); F1 = harmonic mean. Computed two ways
in every report: **aggregate** (across all Issues in the estate) and **per-category**
(restricted to Issues of one transformation type) — the per-category breakdown is what
answers "which transformation types are hardest for agents," matching your example table
directly.

### 4.2 Entity-level accuracy

Reported as a confusion-style breakdown, not a single number: count of
`exact_entity` / `app_level` / `none` matches, both in aggregate and per category. This is
what lets a later analysis distinguish "the agent knows something's wrong with this app
but can't pinpoint what" from "the agent has no idea."

### 4.3 Confidence calibration

**Scope**: calibration is only computed over the agent's *claims* (true positives + false
positives) — each paired with (stated confidence, was it correct). False negatives have no
agent-stated confidence and don't participate in calibration; they only affect recall.

- **Brier score**: mean squared error between confidence and binary correctness (0 =
  perfect, 1 = worst). Works fine even with the small sample size (~5-15 claims) a single
  evaluation run produces — recommended as the primary per-report calibration number.
- **Expected Calibration Error (ECE)**: bins claims by confidence, compares average
  confidence to average accuracy per bin. This is the metric your example (95% confidence
  duplicate-processing claim) is really asking about. **Caveat I want to flag rather than
  paper over**: ECE needs volume to be meaningful — a handful of claims from one run
  produces mostly-empty bins and a noisy number. I'm designing the report to store raw
  `(confidence, correct)` pairs (not just a summary statistic) specifically so ECE and
  reliability diagrams can be computed *after the fact* across many runs, and the
  per-report ECE field is `null` below a documented minimum sample count rather than
  silently reporting a misleading value from 5 data points.

Both metrics fit the use case; Brier is the one that's honest at single-report scale, ECE
is the one that becomes meaningful once you're running dozens of evaluations, which is
exactly what a benchmark like this is for.

### 4.4 Dynamic complexity model (not fixed difficulty levels)

Per your decision, `complexity.py` does **not** use `profile_name`/`level` (`"level_2_structural"`,
etc.) as its measure of how complex a trajectory turned out to be. Those labels describe how
a trajectory was *generated* (which operators were eligible, roughly how many mutations to
try); they don't describe how complex it actually *turned out*, especially since Milestone
4's engine can stop early (graceful degradation) or have operators compound in
seed-dependent ways. Instead, complexity is **derived from the realized `MutationLedger`
itself** — a pure function of what actually happened, independent of which profile asked
for it. This decouples "how the estate was generated" from "how hard it actually is,"
which is the more principled version of the "quantifiable complexity levels" decision
already on record in `Ark_Architecture_and_Plan.md` Section 2.

**`compute_trajectory_complexity(baseline_estate, transformed_estate, ledger) -> ComplexityProfile`**
— agent-independent; needs no agent output at all, since it's purely a property of the
transformation itself:

| Factor | Computed as | What it captures |
|---|---|---|
| `mutation_count` | `len(ledger.records)` | Raw number of transformation operator applications — "how much happened," before dedup. |
| `distinct_issue_count` | `len(issues.derive_issues(ledger))` | Deduplicated observable issues (Section 2) — "how many separate things an agent could notice." Can be *lower* than `mutation_count` when mutations compound on the same entity. |
| `compounding_count` / `max_compounding_depth` | entities hit by >1 record / the highest such count | Directly measures the compounding pattern Milestone 4 itself surfaced (the same step decayed twice). More compounding on fewer entities is a different kind of hard than the same total mutation count spread thinly. |
| `severity_mean` / `severity_max` | mean / max of `record.severity` across the ledger | Raw severity, as recorded. |
| `affected_entity_count` | size of the union of all `affected_entity_ids` | Breadth — how much of the estate is touched. |
| `transformation_diversity` | distinct `transformation_type` values used / 6 (total operators) | How varied the issue types are, not just how many mutations. |
| `dependency_impact` | for each affected entity, its **in-degree** in the transformed estate's dependency graph (how many other entities call/reference it via `ApiCallStep`/`FlowRefStep`), aggregated (mean and max) | "Blast radius" — a mutation on a widely-depended-on entity (e.g. a System API three Process APIs call) is structurally more consequential than one on a leaf no one calls. Computed by building a reverse-dependency index over the *transformed* estate — no ledger schema change needed; this was always derivable from data Milestone 4 already produces, just not yet computed anywhere. |
| `interaction_score` | build a graph over *only the mutated entities*, using the same dependency edges; `1 - (num_connected_components - 1) / max(1, mutated_entity_count - 1)` | 0 = every mutation landed on an isolated, unconnected entity; 1 = all mutations landed on entities that are structurally connected to each other. Directly operationalizes "interaction between multiple transformations." |

**Scalar rollup** (for plotting a single performance-vs-complexity curve — never the only
representation, always alongside the full vector above):

```
complexity_score = (
    w1 * normalize(mutation_count) +
    w2 * severity_mean +
    w3 * transformation_diversity +
    w4 * normalize(dependency_impact_mean) +
    w5 * interaction_score +
    w6 * normalize(compounding_count)
)
```

Weights (`w1..w6`) are a documented, overridable `ComplexityWeights` config — defaulted to
something reasonable (e.g. equal weighting after normalization) but never a hidden
constant, consistent with "no undocumented assumptions." `normalize()` denotes min-max or a
fixed reasonable-range scaling, documented per factor rather than left implicit.

**`correlate_with_reports(reports: list[EvaluationReport]) -> ComplexityCorrelationResult`**
— the agent-dependent half of the module, operating across a *batch* of evaluation reports
(each already paired with its own `ComplexityProfile`). This answers your three research
questions directly:

- *"How does agent performance change as environments become progressively more complex?"*
  → bin reports by `complexity_score` (continuous, not discrete labels) and compute
  recall/precision/F1 per bin — a real degradation curve, not a lookup table keyed by an
  arbitrary "Level" label.
- *"Which transformation types are most difficult?"* → per-category recall, already in
  `metrics.py` (Section 4.1); `complexity.py` additionally checks whether a category's
  recall drops *further* when it co-occurs with high overall complexity, to separate
  "this issue type is intrinsically hard" from "this issue type is hard only when
  buried among other changes."
- *"At what level of accumulated drift do agents begin failing?"* → sort reports by
  `complexity_score`, find the threshold where recall crosses a documented cutoff (e.g.
  50%); report both the threshold and the sample count it was estimated from, since this
  is exactly the kind of number that's meaningless below a minimum sample size (same
  caveat as ECE, Section 4.3).

All of the above requires a **batch** of reports to be statistically meaningful — a single
evaluation gives you one `ComplexityProfile` and one score, not a curve. Every aggregate
number `complexity.py` produces is reported alongside its sample size, never as a bare
percentage.

---

## 5. Data model

### 5.1 Evaluator input

Not a new serialization format — a composition of what already exists:

```
EvaluationInput:
  transformed_estate   # GroundTruthEstate (Milestone 3/4 output)
  ledger               # MutationLedger (Milestone 4 output) — already carries baseline_estate_id
  manifest             # RenderedEstate.manifest (Milestone 2 output, any adapter)
  agent_output         # AgentOutput — see 5.2
```

The raw baseline estate object is *not* required as a separate input — the ledger's
`original_state`/`transformed_state` diffs already capture everything that changed. Only
`baseline_estate_id` (already on the ledger) is retained, for provenance.

### 5.2 Agent output format — structured JSON, required (per your decision)

This replaces my originally-proposed schema with your exact required contract:

```json
{
  "findings": [
    {
      "artifact_reference": "customer-api.xml",
      "entity_reference": "Customer API",
      "issue_type": "documentation_decay",
      "explanation": "The API documentation is incomplete because migration information is missing.",
      "confidence": 0.87
    }
  ]
}
```

`artifact_reference` and `entity_reference` are **singular strings**, not lists — a finding
points at exactly one artifact and one entity reference. `issue_type` (not
`issue_category`) must be one of Ark's published taxonomy: the six operator
`transformation_type` values, plus `other`.

**Any-of matching implication**: some Issues affect more than one entity (e.g. the
`legacy_version_introduction` example in Section 4.4's walkthrough affects both a new API
*and* a new flow). Since a finding carries only one `entity_reference`, `matcher.py` treats
a finding as a candidate match for an Issue if its resolved entity id is **any one** of that
Issue's `affected_entity_ids` — the agent isn't required to name every affected entity to
get credit for detecting the issue, only to correctly identify at least one of them.

**Why structured JSON, not free text parsed afterward**: reproducibility is a stated
project-wide constraint; classifying free-text prose into categories would require an NLP
or LLM-judge layer, which introduces a second model's biases and non-determinism into what's
supposed to be an objective scoring pipeline. Asking the agent (or a thin harness wrapping
it) to emit these five required fields is a small ask that keeps the entire
detection/entity/precision/recall/calibration pipeline fully deterministic and auditable.

`explanation` is still fully supported and required by the schema, but scored separately and
more leniently (Section 3's third axis), never blended into the primary detection numbers. A
pure natural-language-only input path (no structured fields at all) is explicitly **out of
scope for this design** — it would need its own NL-to-structured adapter (heuristic or
LLM-judge-based) sitting in front of `parser.py`, which I'd treat as a distinct, clearly
lower-reproducibility future capability, not part of the initial evaluator.

`artifact_reference` and `entity_reference` deliberately use **artifact-visible
identifiers** (rendered file name and rendered display name/label), never Ark's internal
entity ids — because a real agent never sees those ids. `parser.py` is responsible for
resolving these against the manifest. See Section 5.5 for the manifest expansion this
requires at the step/component level.

### 5.3 Scoring result format

```
FindingScore:
  finding_id
  matched_issue_id: str | None
  category_match: bool
  entity_match_level: "none" | "app_level" | "exact_entity"
  is_true_positive: bool
  confidence: float
  explanation_quality: float | None   # secondary, optional

EvaluationScoring:
  precision, recall, f1: float
  by_category: dict[str, {precision, recall, f1, tp, fp, fn}]
  entity_match_breakdown: {exact_entity: int, app_level: int, none: int}
  brier_score: float
  calibration_pairs: list[(confidence, correct)]   # raw, for later cross-report ECE
  ece: float | None                                 # null below a documented min sample size
  false_negatives: list[issue_id]
```

### 5.4 Evaluation report format (the artifact saved to disk)

```
EvaluationReport:
  report_schema_version
  evaluator_version
  estate_id, baseline_estate_id, profile_name, trajectory_seed   # full provenance back to Milestone 4
  agent_id
  scoring: EvaluationScoring
  raw_agent_output: {...}     # verbatim, for audit
  ledger_issues: [...]        # the derived Issues this report scored against, for audit
```

Every field needed to answer "why did this report say what it said" is retained — no
scoring decision should require re-running anything to explain.

### 5.5 Manifest expansion (approved) — step/component-level traceability

Your reasoning for this is correct and matches what Milestone 4's own example ledger
already shows: several real Level 3 mutations land *below* the API/flow level —
`documentation_decay` on a single step's description, `schema_inconsistency` on one field
inside a step's DataWeave script, `dependency_change` repointing one step's
`target_api_id`. An evaluator that can only resolve agent references down to "this flow" or
"this API" can't score those precisely; it would always report `app_level`, never
`exact_entity`, for an entire class of real issues.

**Traceability hierarchy** (matches your diagram, with one correction): Application and API
are **siblings** under the estate, not strictly nested — an Application owns Flows; an API
is a separate top-level entity whose `entry_flow_id` happens to point into a Flow (possibly
in a different Application, per the schema). The corrected hierarchy Ark actually has:

```
Rendered artifact (file)
   └─ Application  ──┐
        └─ Flow      ├─ (API references a Flow via entry_flow_id — cross-cutting, not nested)
             └─ Step/component
   └─ API
```

Full artifact → entity traceability still holds end-to-end; it's just not a single strict
tree, which the manifest already reflects structurally (separate top-level `Application`,
`Flow`, `API` entity entries, not a nested dict) — this expansion adds `Step` as a fourth
entity kind at the same level, not a restructuring.

**Constraint respected**: no entity ids or hidden metadata get embedded in the rendered
XML/YAML/DataWeave artifacts themselves. The manifest remains the single source of this
mapping, exactly as already decided for Application/Flow/API in Milestone 2 — this
expansion is additive to the manifest only.

**Per-step-kind label synthesis** (what rendered-visible label represents each step, since
`entity_reference` in the agent-output schema must match something a human/agent actually
sees in the file):

| Step kind | Rendered label used | Why |
|---|---|---|
| `TransformStep` | `.name` | Already a real field on the domain model; already rendered as `doc:name` in Mule XML. |
| `ApiCallStep` | `.name` | Same — already rendered today. |
| `FlowRefStep` | synthesized: `"reference to '{target_flow_name}'"` | The step itself has no `.name`; the rendered `<flow-ref>` element's identity in the XML *is* its target, so the label is derived from the resolved target flow's name rather than invented. |
| `LoggerStep` | its `message` text (or a truncated prefix of it) | `LoggerStep` has no `.name` field in the domain model, and the renderer doesn't emit a `doc:name` on `<logger>` elements today. Using the message text as the identifying label lets step-level resolution work **without touching Milestone 2's renderer or its golden XML fixtures** — deliberately the least invasive option, since adding a synthetic `doc:name` attribute would change actual rendered file content, not just the manifest. |

**Ripple effect, stated plainly**: this change is additive to `ark/adapters/mulesoft/manifest.py`
(`build_manifest()` gains step-level entries with a `name`/label field) but does **not**
change `renderer.py`'s output — no `.xml`/`.yaml` artifact content changes. However,
`test_milestone2.py`'s golden-file tests compare `RenderedEstate.manifest` against committed
`golden/milestone2/*/manifest.json` fixtures directly, so those committed manifest fixtures
**will need regenerating** once this lands (a mechanical, reviewable diff — new step entries
appended, nothing existing removed or changed) — flagged here honestly rather than
discovered as a surprise test failure mid-implementation.

---

## 6. Module architecture

```
ark/evaluator/
  __init__.py
  issues.py        # ledger records -> deduplicated Issue objects (NEW vs. your sketch — see Section 2); also derive_issue_diagnostics() (Milestone 6.4)
  schema.py        # AgentOutput/Finding dataclasses + validation of the agent-output contract (NEW — mirrors ark/core/validate.py's role for ground truth)
  parser.py        # normalizes raw agent output into internal Finding objects; resolves artifact_reference+entity_reference -> entity id via the manifest
  matcher.py       # aligns Findings against Issues -> FindingMatchResult list (TP/FP/FN); any-of matching against affected_entity_ids (Section 5.2)
  metrics.py       # precision/recall/F1, aggregate + per-category, kept separate from entity localization
  calibration.py   # Brier score, ECE (sample-size-guarded), raw calibration pairs
  explanation.py   # rule-based (no LLM judge) structural explanation-quality signals (NEW — Milestone 6.3)
  complexity.py    # two distinct responsibilities, see below; also TrajectoryPerformanceRecord (Milestone 6.3)
  report.py        # assembles EvaluationReport (7 sections) + report_to_dict/json/from_dict (Milestone 6.4; named "report.py" not "reports.py")
  orchestrator.py  # top-level entry point: evaluate(transformed_estate, ledger, manifest, agent_output) -> EvaluationReport (Milestone 6.4; named "orchestrator.py" not "evaluator.py", to avoid colliding with the ark.evaluator package name)
  analysis.py      # cross-report batch aggregation: analyze_reports(reports) -> ExperimentAnalysis (Milestone 6.5; this is where Section 4.4/8's originally-sketched complexity.py-resident correlate_with_reports() ended up living, under a new name -- see Section 8)
```

I kept your six modules and added three (`issues.py`, `schema.py`, `explanation.py`) plus a top-level
orchestrator (`orchestrator.py`, matching the pattern already established by
`ark/generator/generator.py` and `ark/mutation/engine.py` — every subsystem so far has one
clearly-named entry point, and this should too).

**`complexity.py` has two distinct responsibilities, kept as separate functions in the same
module rather than split further** (Section 4.4 has the full spec):
1. `compute_trajectory_complexity(...)` — **agent-independent**, a pure function of
   `(baseline_estate, transformed_estate, ledger)`. Runs once per estate, no agent output
   involved at all.
2. `correlate_with_reports(...)` — **agent-dependent**, cross-report batch aggregation
   requiring many `EvaluationReport`s (each already carrying its own complexity profile) to
   produce a statistically meaningful accuracy-vs-complexity curve.

**Technology independence in `schema.py`**: `issue_type` is a closed enum — Ark's six
technology-agnostic transformation types plus `other` — deliberately excluding any
adapter-specific vocabulary (no `"invalid_mule_xml_attribute"`-style values). This is not
just a taxonomy choice; it's a scoring guarantee. Milestone 4's operators already carry a
hard invariant that every transformed estate renders to valid, well-formed output (the
no-op/validity check from Milestone 4). Given that invariant, a syntax complaint like
"invalid Mule XML attribute" can **never** correspond to a real injected issue — Ark simply
never produces malformed output. `schema.py` rejecting non-taxonomy `issue_type` values
(mapping anything technology-syntax-flavored to `other`, which cannot score as a true
positive against any real Issue) turns this principle into an enforced rule, not just a
documented aspiration.

**Dependencies**: `issues.py` depends only on `ark.mutation.ledger`. `parser.py` depends on
the manifest shape from `ark.adapters.base` (technology-agnostic — never imports
`ark.adapters.mulesoft` directly, preserving the boundary from Milestone 2). Nothing in
`ark/evaluator/` imports from `ark/adapters/mulesoft/` specifically, which is what makes
this evaluator usable against a future non-MuleSoft adapter without modification.

**Testing strategy**: unit tests per module against small, hand-constructed fixtures where
the expected numbers can be verified by manual arithmetic (not just "doesn't crash") —
mirroring how Milestone 4's tests hand-verified specific ledger entries. `issues.py` gets
tested directly against Milestone 4's *actual* committed example ledgers (the compounding
`documentation_decay` case is the concrete regression case it must handle correctly).
`complexity.py` gets tested against synthetic multi-report fixtures with a known,
manufactured accuracy-vs-severity trend, verifying the aggregation recovers it.

**Risks**:
- Entity resolution ambiguity: an agent's natural-language reference may plausibly match
  more than one entity (two apps with a "customer" flavored name, as Milestone 3's own
  examples demonstrated). `parser.py` needs an explicit, documented tie-breaking or
  "ambiguous — unresolved" outcome rather than guessing silently.
- ECE at low sample size (Section 4.3) — mitigated by the null-below-threshold design,
  not by hiding the caveat.
- Report format churn once real agents are tested against it for the first time — mitigate
  by versioning `report_schema_version` from day one, the same discipline used for the
  ground-truth and ledger schemas.
- The manifest expansion (Section 5.5) touches a Milestone 2 file and requires regenerating
  committed golden `manifest.json` fixtures — mechanical, but a real dependency to land
  before `matcher.py` can be exercised against step-level Issues.

---

## 7. How this preserves Ark's existing principles

- **Ground truth remains the sole source of truth**: the evaluator only ever reads
  `GroundTruthEstate`/`MutationLedger`; it never writes to or infers a replacement for
  either.
- **The ledger remains authoritative**: `issues.py` produces a *view* over the ledger
  (deduplicated Issues), never a replacement — the report retains the original
  `profile_name`/`trajectory_seed` so every score is traceable back to the exact Milestone
  4 records that produced it.
- **Never infer hidden truth from exported files**: the evaluator does not independently
  re-parse the rendered XML/YAML to form its own opinion about what's wrong — it only
  matches the agent's claims against the ledger-derived Issues. If a future need arises to
  double-check adapter fidelity, that's a different tool (arguably back in Milestone 2's
  round-trip testing territory), not this one.
- **Reproducibility**: every module here is a pure function of its inputs — same
  `(transformed_estate, ledger, manifest, agent_output)` always produces the same
  `EvaluationReport`, with no hidden randomness anywhere in the pipeline.
- **No undocumented assumptions**: the issue-consolidation rule, the entity-match-level
  definitions, and the TP/FP/FN definitions are all written down here and will be
  restated as docstrings in the code, exactly like every prior milestone's design
  decisions.
- **Future technologies beyond MuleSoft**: the evaluator only depends on the
  technology-agnostic `GroundTruthEstate`, `MutationLedger`, and the adapter-agnostic
  manifest shape from `ark/adapters/base.py` — a second adapter (still not yet built; see
  the open item noted in Milestone 4's write-up) would need zero evaluator changes to work.

---

## 8. Implementation plan

Broken into five sub-milestones so this can be built and checkpointed incrementally,
consistent with how every prior milestone was handled.

### Milestone 6.1 — Issue derivation + agent-output contract
- **Goal**: consolidate raw ledger records into deduplicated Issues; define and validate the agent-output JSON contract (your exact schema — `findings[]` with `artifact_reference`, `entity_reference`, `issue_type`, `explanation`, `confidence`).
- **Files**: `ark/evaluator/__init__.py`, `issues.py`, `schema.py`.
- **Dependencies**: Milestone 4 (ledger).
- **Risks**: getting the dedup/grouping rule right for compounding mutations on the same entity (the exact scenario Milestone 4's own example ledger exhibits).
- **Complexity**: Medium.
- **Tests**: `issues.py` against Milestone 4's committed example ledgers (verify the two `documentation_decay` hits on the same step collapse into one Issue with cumulative severity); `schema.py` rejects malformed agent output (missing field, out-of-range confidence, unknown `issue_type`) and specifically rejects/downgrades non-taxonomy syntax-flavored `issue_type` values per the technology-independence rule (Section 6).

### Milestone 6.2 — Manifest expansion + parser + matcher
- **Goal**: land the approved step/component-level manifest expansion (Section 5.5); resolve agent references (`artifact_reference` + `entity_reference`) to entity ids via the manifest; compute category/entity match (any-of against `affected_entity_ids`) and produce TP/FP/FN.
- **Files**: `ark/adapters/mulesoft/manifest.py` (additive step-level entries + per-step-kind label synthesis, Section 5.5), `parser.py`, `matcher.py`. Also regenerates `golden/milestone2/*/manifest.json` fixtures and re-runs `test_milestone2.py`.
- **Dependencies**: 6.1; Milestone 2 (manifest, `renderer.py` untouched).
- **Risks**: ambiguous entity references (two entities with the same rendered label); the golden-fixture regeneration must be a reviewable, additive-only diff — any unexpected change to existing manifest entries would indicate a bug, not the intended expansion.
- **Complexity**: High — this is the reasoning-adjacent core of the whole subsystem, plus a real (if small) change to committed Milestone 2 fixtures.
- **Tests**: `test_milestone2.py` re-run against regenerated fixtures (confirm only additions, no regressions); hand-written mock agent outputs against Milestone 4's example ledgers covering exact match, app-level-only match, wrong category, unmatched/hallucinated claims, step-level matches (the `documentation_decay`/`schema_inconsistency` cases), and the all-false-positive Level-0 case.

### Milestone 6.3 — Metrics + calibration
- **Goal**: precision/recall/F1 (aggregate + per-category), Brier score, ECE with a sample-size guard.
- **Files**: `metrics.py`, `calibration.py`.
- **Dependencies**: 6.2.
- **Risks**: ECE misleading at low N — must null it out below a documented threshold rather than report a noisy number.
- **Complexity**: Medium.
- **Tests**: small fixed fixtures with hand-computed expected precision/recall/F1/Brier values, checked against manual arithmetic.

### Milestone 6.4 — Report assembly + orchestration ✅ complete
- **Goal**: assemble the `EvaluationReport`; the single `evaluate(...)` entry point. No new metrics, scoring, or matching — this milestone only combines 6.1-6.3's existing outputs into one reproducible, serializable experiment artifact (Ark_Architecture_and_Plan.md's Milestone 6.4 write-up has the full detail).
- **Files**: `report.py` (renamed from the sketch's `reports.py` — singular, matching every other evaluator module's naming), `orchestrator.py` (renamed from the sketch's `evaluator.py`, to avoid colliding with the package name `ark.evaluator` itself). One small additive change outside this milestone's own files: `issues.py` gained `derive_issue_diagnostics()`.
- **Dependencies**: 6.1-6.3.
- **Risks**: report format churn as real agents get tested — mitigated by a versioned `report_schema_version` from day one, exactly as planned.
- **Complexity**: Low-Medium, as estimated.
- **Tests**: end-to-end `evaluate()` against real Milestone 1 trajectories plus hand-written agent outputs; determinism excluding the timestamp field; full JSON round-trip via `report_to_dict`/`report_to_json`/`report_from_dict`; all five Failure Analysis buckets; no-mutation regression across every input; an AST-based check that no evaluator module (old or new) imports `ark.adapters`.
- **Deviation from the original sketch**: `report_from_dict()` wasn't in the original plan (Section 5 only specified one-directional serialization, matching `ledger.py`'s precedent) — added because Ark is now expected to reload historical reports as an experiment framework, not just write them out once. Kept deliberately lightweight (explicit per-type reconstruction, no schema migration, no generic reflection) rather than expanding scope.

### Milestone 6.5 — Cross-report complexity correlation and experiment analysis ✅ complete
- **Goal**: `compute_trajectory_complexity(...)` (agent-independent, per-estate — Section 4.4's factor table and scalar rollup) was already built in Milestone 6.3. This milestone builds the cross-report batch aggregation half: does agent performance degrade as complexity increases, which transformation operators produce the largest measurable degradation vs. a clean baseline, does calibration worsen with complexity, and are certain transformation combinations disproportionately difficult.
- **Files**: `ark/evaluator/analysis.py` (new). Not `complexity.py`, and the entry point is not named `correlate_with_reports()` — see the deviation note below.
- **Dependencies**: 6.4 (consumes `EvaluationReport` objects only).
- **Design decisions locked in during implementation**:
  - **Fixed, absolute complexity buckets** (five equal-width bands across `complexity_score`'s guaranteed `[0, 1]` range), not dynamic per-batch min/max — so "the 0.4-0.6 band" means the same thing across different experiment runs rather than being redefined by whatever range one batch happens to contain.
  - **Hand-rolled Pearson correlation** (no `statistics.correlation`, which is Python 3.10+-only and this project stays broadly version-portable and zero-dependency) — returns `None` (never `0.0`) for fewer than `DEFAULT_MIN_SAMPLE_SIZE_FOR_CORRELATION` (5) non-null data points, or when either series has zero variance, mirroring `calibration.py`'s ECE sample-size gate exactly. Every `CorrelationStatistic` carries a fixed disclaimer: this is an *observed association*, never a causal claim, and is not adjusted for confounders (mutation_count and severity tend to rise together, so either could be "the" driver of any association found).
  - **Baseline-relative degradation with a consistent sign convention**: for every transformation type and exact transformation-type combination, `TransformationImpactAnalysis` reports observed performance, the clean (`mutation_count == 0`) baseline's performance from the same batch, and a degradation delta defined so **positive always means "worse than baseline,"** regardless of whether the underlying metric is higher-is-better (category F1, entity localization accuracy — degradation = `baseline - observed`) or lower-is-better (calibration ECE — degradation = `observed - baseline`).
  - **A genuine, worth-flagging structural finding, not a bug**: `metrics.py`'s `recall` is `None` whenever there are zero real Issues to find (`ClassificationMetrics.recall`'s own docstring) — which is *always* true for a `level_0_clean` trajectory, since it has no Issues by construction. This means `category_f1` (and `entity_localization_accuracy`, same shape) is **structurally undefined on any true clean baseline**, regardless of what the agent does, so `category_f1_degradation`/`entity_localization_degradation` are `None` whenever the baseline is built from genuinely clean reports — not a gap in `analysis.py`, but an honest downstream consequence of an existing, frozen Milestone 6.3 decision. `calibration_ece` doesn't have this problem (it's defined as soon as the agent makes any claims at all, real issues or not), so the ECE-degradation axis is the one that stays measurable against a clean baseline. Documented in `analysis.py`'s module docstring and `examples/milestone6/generate_analysis_example.py` rather than silently smoothed over — the same "surface real limitations" ethos as the Milestone 6.3 duplicate-issue-per-entity nuance and Milestone 4's three no-op bugs.
- **Complexity**: Medium-High, as estimated.
- **Tests** (`tests/test_milestone6_5.py`, 20 tests): determinism (identical reports → identical analysis excluding `generated_at`, order-independence); empty report list and missing/malformed report files handled gracefully (`load_reports_from_files` skips and records what it couldn't load rather than raising); correlation `None`-gating below the minimum sample size and a sign-sanity check (a genuinely flat, non-degrading agent must never show a spurious negative correlation — zero variance in the metric correctly yields `None`, not a fabricated number); baseline presence/absence handling (`None` baseline when no clean reports are in the batch, propagating to `None` degradation everywhere downstream); by-type vs. by-combination breakdowns are verifiably distinct; the degradation sign convention verified directly against hand-computed expected values; JSON serialization round-trips as plain data; `analysis_from_dict()` deliberately does not exist (explicit scope boundary); no mutation of input reports; and an AST-based check that `analysis.py` imports *only* `ark.evaluator.report` from within `ark.evaluator` (a stronger, more specific version of the general "no `ark.adapters` import" check every other evaluator module is held to — this module doesn't even need the intermediate modules, only the already-assembled report).
- **Deviation from the original sketch**: the design doc originally proposed this functionality as `complexity.py`'s `correlate_with_reports(reports) -> ComplexityCorrelationResult`. It was implemented instead as its own module, `ark/evaluator/analysis.py`, with the entry point `analyze_reports(reports) -> ExperimentAnalysis` — for the same reason `report.py`/`orchestrator.py` were split out of a single sketched file in Milestone 6.4: this is a substantial, independently-testable unit (four result dataclasses, ~10 private helper functions) that reads far more clearly as its own module than as a second responsibility bolted onto `complexity.py`, which already has a distinct, agent-independent job (`compute_trajectory_complexity`). `complexity.py` itself required zero changes for this milestone. Also out of the original sketch, per explicit instruction: no `analysis_from_dict()` (serialization is one-directional — `analysis_to_dict()`/`analysis_to_json()` only) and no failure-threshold-crossing detection (not requested in the approved plan; would be a straightforward, separately-scoped addition on top of the complexity buckets already computed here).

---

## 9. Resolution of prior open questions, and what remains

Revision 1 left three items open; your latest message resolved all three explicitly:

1. **Complexity model** — resolved: dynamic, derived from the realized ledger (Section 4.4), not fixed profile levels. No change to `ark/mutation/profiles.py` is needed or planned — profiles remain generation-time knobs, complexity is a post-hoc measurement.
2. **Manifest expansion** — resolved: approved, scoped to Section 5.5, folded into Milestone 6.2 above.
3. **Structured JSON as the required agent-output contract** — resolved: approved, using your exact schema (Section 5.2).

**One remaining non-blocking judgment call**, flagged rather than silently decided: the
default `ComplexityWeights` values in Section 4.4's scalar rollup (`w1..w6`) and the
minimum-sample-size thresholds for ECE (Section 4.3) and the complexity failure-threshold
(Section 4.4) are reasonable starting defaults, not empirically tuned — I'll document them
plainly as overridable in the code and revisit once real evaluation reports exist to tune
against, rather than treating them as a blocker to starting Milestone 6.1.

Waiting for your go-ahead before writing any code, per your instruction.

---

## 10. Postscript: Milestone 7 (agent harness + experiment runner)

Everything above (Sections 1-9) describes the evaluator subsystem this design
document scoped -- Milestones 6.1 through 6.5, all now complete. Milestone 7,
built afterward, is the piece that actually calls an agent and feeds this
evaluator its output automatically: `ark/harness/` (the `AgentClient`
interface, prompt construction, response parsing, and an offline
`ScriptedAgentClient` reference implementation) and `ark/experiment/`
(`TrajectorySpec` + `run_experiment()`, the full Generator/hand-authored →
Mutation → Render → Agent Harness → `evaluate()` → `analyze_reports()` arc).
Not written up in full here since it's a different subsystem with its own
concerns (prompting, response parsing, a real vendor SDK integration) rather
than new evaluator design -- see `Ark_Architecture_and_Plan.md`'s Milestone 7
section for the full write-up, and `examples/milestone7/README.md` for the
worked example.

One thing worth noting here specifically because it touches this document's
Section 5.2 (the agent-output contract) and Section 7 (isolation
principles): Milestone 7's `ark/harness/prompt.py` sources the required JSON
shape and `ISSUE_TYPE_TAXONOMY` directly from this package's own
`schema.py`, so a real agent's prompt can never drift from what
`parse_agent_output()` actually validates -- the one deliberate, documented
exception to "the harness never imports evaluator internals," since the
taxonomy is the agent-VISIBLE output contract, not ground truth. Every other
isolation guarantee this document establishes (the agent never sees
`ark.core.models`, `ark.mutation.ledger`, or the rendering manifest) is
carried forward unchanged and re-verified at the harness/runner wiring level
in `tests/test_milestone7.py`.
