# Ark: System Overview

A concise technical summary of the whole product — synthetic estate generation, mutation, rendering, agent evaluation, cross-experiment analysis, and the interactive UI — with the exact logic and math behind each stage.

## What Ark is

Ark builds small, synthetic "enterprise integration estates" (fake but realistic API/flow systems, e.g. Mule applications) **with a known answer key recorded up front**. An AI agent is then shown only the rendered artifact files and asked to find integration problems, with no access to that answer key. Ark's evaluator scores the agent's findings against the real answer key, and a cross-experiment analysis layer looks for patterns (does accuracy degrade with complexity? which mutation types are hardest? does confidence stay calibrated?). A Streamlit UI runs and displays all of this locally.

## End-to-end pipeline

```
Generator (or hand-authored estate)
        │
        ▼
Mutation Engine  ──────────────►  Mutation Ledger (the answer key, hidden)
        │
        ▼
Adapter / Renderer  ───────────►  Rendered artifacts (XML/YAML) + Manifest (hidden)
        │
        ▼
Agent Harness  ◄── AgentClient (Scripted / Anthropic / Gemini)
        │  (agent sees ONLY rendered artifacts)
        ▼
Agent findings (JSON)
        │
        ▼
Evaluator  ──► Issues (ledger → answer key) + Matcher + Metrics + Calibration
        │
        ▼
EvaluationReport (one per trajectory)
        │
        ▼
Cross-Experiment Analysis (many reports → trends)
        │
        ▼
Streamlit UI (config, dashboard, charts, artifact viewer, export)
```

Every stage is a pure function or a well-bounded object graph; nothing beyond the UI holds hidden global state.

## 1. Ground truth model (`ark/core`)

Plain stdlib dataclasses, zero third-party dependencies. `GroundTruthEstate` contains `Application`s, each with `API`s and `Flow`s. A `Flow` has a `Trigger` (HTTP listener or scheduler) and a list of `Step`s: `TransformStep`, `LoggerStep`, `FlowRefStep` (calls another flow in the same app), or `ApiCallStep` (calls another app's API over the network — the only place cross-application dependencies live). This is the single source of truth every later stage reads or mutates.

## 2. Generator (`ark/generator`)

Builds a synthetic estate from a seed instead of hand-authoring one. Topology is a fixed three-layer feed-forward graph: **experience → process → system** (system apps are always leaves). Dependency edges only ever point one layer down — never sideways or upward — so `dependency_density` (0–1, the fraction of possible targets sampled) can never produce a fully-meshed graph. Each process/system app independently gets a secondary scheduled flow via a Bernoulli draw on `scheduled_job_ratio`, and a shared sub-flow via a Bernoulli draw on `shared_component_frequency`. Other knobs: how many experience/process/system APIs to create, naming style, and vocabulary domain. Everything is seeded — same seed, same estate.

## 3. Mutation Engine (`ark/mutation`)

Applies realistic, imperfection-introducing transformations to an estate and records every change in a **Mutation Ledger** — this ledger *is* the answer key, and the agent never sees it.

Six operators:

| Operator | What it does |
|---|---|
| Naming drift | Renames an app/API/flow's display name (never its id) via 1–3 compounding drift styles (kebab→camel, legacy suffix, case shift, abbreviation) |
| Documentation decay | Truncates, genericizes, or empties a step's description, scaled by severity |
| Duplicate processing | Clones a shared sub-flow and rewires one caller to the duplicate |
| Legacy version | Adds a sibling "legacy" API with a frozen (possibly truncated) copy of a flow |
| Schema inconsistency | Renames an `...Id:` field in a DataWeave transform to a different naming convention |
| Dependency change | Repoints a flow/API call to a different, still-valid target |

**Profiles** (difficulty levels) are additive: Level 1 = naming drift + doc decay (3 mutations, severity 0.1–0.4); Level 2 adds duplicate processing + dependency change (6 mutations, 0.3–0.6); Level 3 adds legacy version + schema inconsistency (10 mutations, 0.5–0.9); Level 0 applies nothing.

## 4. Adapter / Renderer (`ark/adapters`)

Converts the (possibly mutated) estate into real technology-specific text — MuleSoft XML flows and API-spec YAML — plus a **manifest**: a mapping from artifact file → entity id → dependency, used later to resolve an agent's plain-text references back to real entities. Both the rendered artifacts (shown to the agent) and the manifest (evaluator-only) come out of this one step, and are kept structurally separate from that point on.

## 5. Agent Harness (`ark/harness` + `integrations/`)

`AgentClient` is a one-method interface: `generate(prompt: str) -> str`. Three implementations exist: `ScriptedAgentClient` (deterministic, offline, no network — the default demo agent, backed by a real naming-irregularity heuristic), `AnthropicAgentClient`, and `GeminiAgentClient` (both live in `integrations/`, outside Ark's core, and are only imported lazily when selected).

The prompt sent to whichever agent is selected is built from **only** the rendered artifacts: fixed task instructions, the six issue categories in plain language, the required JSON output shape (sourced live from the evaluator's own taxonomy, never hand-copied), and every artifact file's contents, alphabetically sorted for reproducibility. No ground truth, ledger, manifest, or internal id ever enters this prompt — this boundary is enforced structurally (import checks) and with wiring-level tests, not just documentation.

The agent responds with raw text; a response parser recovers a JSON object (`{"findings": [...]}`) from bare, fenced, or prose-wrapped output.

## 6. Evaluator (`ark/evaluator`)

**Issues (the answer key).** Raw ledger records are grouped by `(transformation_type, affected entities)`. For each entity, the earliest recorded "original" state and the latest "transformed" state are diffed field-by-field; only fields that actually changed net survive. If every entity in a group nets to no change at all (e.g. a later mutation reverted an earlier one), **no Issue is produced** — a "net-zero" group. Surviving Issues get severity = the *maximum* severity across all contributing records (a conservative worst-case rollup) and an id built from the transformation type and entity set.

**Matcher.** Each agent finding's artifact/entity references are resolved through the manifest into real entity ids (if resolvable at all). A resolved finding is checked against every Issue touching that entity: `entity_correct` = any match was found; `category_correct` = the claimed issue type exists *anywhere* in the estate's real issues (not necessarily at that entity). These are independent boolean signals, not a blended score.

**Category metrics** — the core precision/recall/F1, using the strict definition (matched issue **and** correct type):

```
TP = matches where entity_correct AND category_correct at that entity
FP = every other match
FN = real Issues with zero TP claims against them

precision = TP / total_matches        (None if no matches)
recall    = TP / total_issues         (None if no issues)
f1        = 2·precision·recall / (precision + recall)   (0.0 if both are 0, None if either input is None)
```

Entity **localization accuracy** uses the same shape but the looser `entity_correct` definition (right location, regardless of claimed type).

**Complexity score** — one number per trajectory summarizing how much the mutation engine changed the estate, all sub-terms clamped/normalized to [0,1] then averaged (equal weights by default):

```
complexity_score = mean of:
  min(1, mutation_count / 15)                                    # volume
  severity_mean                                                  # average raw severity
  distinct_transformation_types_used / total_operator_types      # diversity
  min(1, mean_dependency_in_degree_of_affected_entities / 5)      # blast radius
  interaction_score                                              # graph connectivity of affected entities
  min(1, compounding_count / 5)                                  # entities hit by >1 mutation
```
where `interaction_score = 1 − (connected_components − 1) / (unique_affected_nodes − 1)`, i.e. 1.0 if all affected entities form one connected cluster, lower if they're scattered into separate islands.

**Calibration.** Over every agent claim (finding that resolved to something), pair its stated `confidence` with whether it was actually correct:

```
brier_score = mean((confidence − 1_if_correct_else_0)²)     # 0 = perfect; needs ≥1 claim
ece = Σ over 10 confidence bins of (bin_size/total) · |avg_confidence_in_bin − avg_accuracy_in_bin|
      # only computed with ≥5 claims total; otherwise reported as None, never a noisy fake number
```

**Failure analysis** buckets every discrepancy into: missed issues, hallucinations (matches nothing real), wrong diagnosis (right entity, wrong type), overconfidence (confident but wrong), and wrong category. All of this is assembled into one `EvaluationReport` per trajectory.

## 7. Cross-Experiment Analysis (`ark/evaluator/analysis.py`)

Given many `EvaluationReport`s, this layer answers Ark's research questions by pure aggregation — no new scoring rules:

- **Complexity buckets**: 5 fixed, equal-width bands over the guaranteed [0,1] complexity range (0–0.2, 0.2–0.4, …), each with its average F1/localization/ECE.
- **Correlation**: Pearson's r between complexity score and each metric — `r = covariance(x,y) / sqrt(variance(x)·variance(y))` — reported only with ≥5 non-null pairs and nonzero variance on both sides; otherwise `None`, never a misleading 0.
- **Transformation impact**: for each transformation type, observed performance vs. a clean-baseline (mutation-free trajectories in the same batch), with `degradation = baseline − observed` (positive = worse than baseline; the ECE version is reversed since ECE is lower-is-better, so positive still means "worse" either way).
- **Calibration drift**: average stated confidence vs. average actual accuracy per complexity bucket — a growing gap means the agent stays confident even as it gets less accurate.
- **Experiment summary**: trajectory count and simple averages (complexity, F1, localization accuracy, ECE) across the whole batch — the numbers the UI's top-level summary card shows directly.

## 8. Experiment Runner (`ark/experiment`)

A `TrajectorySpec` (a baseline-or-generated estate + profile + seed) drives one full run: mutate → render → call the agent → evaluate. `run_experiment()` does this for a batch of specs and hands back every `EvaluationReport` plus the aggregated `ExperimentAnalysis` — the runner is the only place the mutation/render/harness/evaluator calls are actually wired together; nothing downstream re-implements any of it.

## 9. Interactive UI (`ark/ui`, Streamlit)

A thin, logic-free display layer (`app.py`) over a Streamlit-free business-logic module (`logic.py`) that does all request-building and data-shaping so it can be unit-tested without Streamlit installed at all.

- **Experiment Configuration**: pick an agent (offline scripted, or a real Anthropic/Gemini call), an estate source, a mutation profile, a seed, and a trajectory count.
- **Experiment Summary card**: agent model actually used (read off the constructed client, not a hardcoded label), estate source, profile, trajectory count, and the four experiment-wide averages from `ExperimentSummary` above — each labeled with whether higher or lower is better.
- **Per-trajectory dashboard**: Environment Summary (what was tested), Agent Performance (how it did), Failure Analysis (the specific discrepancy buckets).
- **Research Visualization**: trajectory-level scatter plots — complexity score vs. category F1, and complexity score vs. Brier score — each point being one real trajectory (never bucketed away), with an optional ordinary-least-squares trendline computed in plain Python:
  ```
  slope = Σ(x−mean_x)(y−mean_y) / Σ(x−mean_x)²
  intercept = mean_y − slope·mean_x
  ```
  drawn only when ≥2 points exist and x isn't constant; a single point still renders as a point, never a forced line. The older bucketed views remain available in a collapsed expander for reference. Transformation Type Impact stays a bar chart (a transformation type spans many trajectories, not one).
- **Artifact Viewer**: an explicit, checked split between "🟢 Visible to Agent" (exactly the rendered files) and "🔒 Hidden from Agent" (ground truth, mutation ledger, raw output) — shown to the human researcher only, after scoring.
- **Export**: raw `EvaluationReport`/`ExperimentAnalysis` JSON downloads.

## Design invariants that hold throughout

- **The agent never sees ground truth.** Enforced structurally (import-boundary checks) and with wiring-level tests, not just by convention.
- **Every average is honest.** Metrics that are mathematically undefined (no matches, no issues, too few samples) are reported as `None`, never a misleading 0.
- **Everything is seeded and deterministic** where randomness is used, so a given seed always reproduces the same estate/mutation/trajectory.
- **Zero-dependency core.** `ark/`'s core pipeline has no third-party dependencies at all; vendor SDKs (Anthropic, Google) and the UI framework are isolated into optional extras and a separate `integrations/` package.
- **Association, not causation.** Every correlation/degradation number in the analysis layer carries this disclaimer explicitly.
