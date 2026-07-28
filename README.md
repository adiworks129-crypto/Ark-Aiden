# Ark

Ark is a framework for generating synthetic enterprise integration estates — starting
with MuleSoft APIs and flows — that come with a versioned, machine-readable **ground
truth**. The ground truth is used to grade how accurately AI agents reason about these
estates, and to measure their confidence calibration as complexity increases.

Full architecture, milestone plan, and the design decisions behind it live in
[`Ark_Architecture_and_Plan.md`](./Ark_Architecture_and_Plan.md). Read that first.

## Status

**Milestones 0 through 4 are complete. Milestone 6.1 (evaluator foundation), 6.2
(manifest expansion + finding matcher), 6.3 (metrics + calibration), 6.4 (report
assembly + orchestration), and 6.5 (cross-report experiment analysis) are all
complete. Milestone 7 (agent harness + experiment runner) is complete — Ark can
run end-to-end against a real LLM agent. Milestone 8 (interactive browser UI) is
complete — see [Running the interactive UI](#running-the-interactive-ui) below.**

- **Milestone 0**: a ground-truth schema (API + Flow scope), a validator with
  referential-integrity checks, one hand-authored example estate, and a golden-file
  test proving that estate renders into a realistic MuleSoft XML file byte-for-byte.
- **Milestone 1**: a larger, 4-application/9-flow example estate that exercised the
  schema and surfaced two real gaps — no way to represent a network call between
  APIs, and only one trigger kind — fixed with two small additive schema changes
  (`ApiCallStep`, `SchedulerTrigger`; schema_version 0.1.0 → 0.2.0).
- **Milestone 2**: a general MuleSoft adapter (`ark/adapters/mulesoft/`) that renders
  *any* estate conforming to the schema into per-application Mule XML + API metadata
  files, plus a rendering manifest that maps every generated artifact back to the
  ground-truth entities and dependencies that produced it. Required zero changes to
  the core domain model (now a pinned regression test, not just a claim).
- **Milestone 3**: a deterministic generator (`ark/generator/`) that produces
  arbitrarily-sized, realistic layered (Experience → Process → System) estates from a
  seed + config — no ambient randomness (verified, not just claimed), shared
  dependencies emerging from realistic pool sizes rather than forced, and every
  generated estate validated through the same path a hand-authored file goes through.
- **Milestone 4**: a mutation/transformation engine (`ark/mutation/`) with six
  independent operators (naming drift, documentation decay, duplicate processing,
  legacy version introduction, schema inconsistency, dependency change) and four
  difficulty profiles (Level 0-3, additive operator sets). Every mutation is recorded
  in a ledger that's the authoritative answer key — diffs of exactly what changed,
  why, and how severely — and every operator is guaranteed to leave the estate fully
  valid and exportable. Caught and fixed three real no-op bugs along the way (see
  `examples/milestone4/README.md`).

- **Milestone 6.1**: the evaluator foundation (`ark/evaluator/`) — `issues.py`
  consolidates the raw mutation ledger into deduplicated, observable `Issue`s
  (handling both compounding and "net-zero"/reverted mutations, which must not
  surface as a scoreable issue), `schema.py` validates the required structured
  agent-output contract (enforcing technology independence: unrecognized
  issue types normalize to `"other"`, never matching a real issue), and
  `complexity.py` computes a dynamic, per-trajectory complexity score directly
  from the realized ledger rather than a fixed difficulty label. Full design
  in `Ark_Evaluator_Design.md`; matching/scoring (6.2-6.5) is designed but not
  yet implemented.

- **Milestone 6.2**: step/component-level manifest traceability (additive-only
  change to `ark/adapters/mulesoft/manifest.py` + `renderer.py` — no rendered
  XML/YAML content changed, only the manifest's entity labels; golden manifest
  fixtures regenerated) plus the agent-reference resolver (`ark/evaluator/parser.py`)
  and finding matcher (`ark/evaluator/matcher.py`). The parser resolves an agent's
  artifact-visible claims to internal entity ids using only the manifest —
  exact path/name matching plus a small set of deterministic aliases, no
  fuzzy/substring matching (a documented tradeoff — see parser.py). The matcher
  reports category correctness, entity correctness, and artifact-reference
  correctness as independent signals per finding, never a blended score.

- **Milestone 6.3**: evaluation metrics (`ark/evaluator/metrics.py` — category
  detection precision/recall/F1 and entity-localization precision/recall/
  localization-accuracy, kept as two genuinely independent metric families, never
  blended), confidence calibration (`ark/evaluator/calibration.py` — Brier score
  and sample-size-gated ECE, so two agents at the same accuracy but different
  confidence honesty are distinguishable), a rule-based (no LLM judge yet)
  explanation-signal extractor (`ark/evaluator/explanation.py`), and a
  complexity-performance tracking hook (`TrajectoryPerformanceRecord` in
  `complexity.py`) that bundles a trajectory's complexity score with its full
  metrics/calibration results for future cross-report correlation (Milestone 6.5).
  A worked example evaluation report lives in `examples/milestone6/`.

- **Milestone 6.4**: report assembly (`ark/evaluator/report.py` — an
  `EvaluationReport` with seven sections: Metadata, Environment Summary,
  Transformation Summary, Issue Summary, Agent Performance, Failure Analysis
  with five independent failure-mode buckets, and Research Analysis Hooks;
  introduces no new metrics, only packages 6.1-6.3's outputs) and a single
  pipeline entry point (`ark/evaluator/orchestrator.py`'s `evaluate()`, taking
  a transformed estate, its mutation ledger, a rendered manifest, and the
  agent's raw output straight through to a finished report). Serialization is
  two-directional (`report_to_dict`/`report_to_json`/`report_from_dict`), so a
  historical report can be reloaded as real typed objects, not just read as
  JSON. One small additive change to a prior milestone: `issues.py` gained
  `derive_issue_diagnostics()` (net-zero/cancelled mutation groups, previously
  undiscoverable after the fact) — `derive_issues()` itself is unchanged. A
  full worked example lives in `examples/milestone6/report_example.json`.

- **Milestone 6.5**: cross-report experiment analysis (`ark/evaluator/analysis.py`
  — a pure consumer of `EvaluationReport` objects, importing nothing else from
  `ark.evaluator` and no adapter/mutation/core-model code at all). Answers Ark's
  founding research questions over a batch of reports: `ComplexityAnalysis` bins
  reports into five fixed, absolute complexity bands and computes a hand-rolled
  Pearson correlation (gated `None` below a minimum sample size, same discipline
  as calibration's ECE) between `complexity_score` and each performance metric —
  explicitly an observed association, never a causal claim. **Passing the
  minimum-sample-size gate is not the same as the correlation being stable**: in
  real batches run at n=5 and n=15 (both above the gate), all three headline
  correlations reversed sign entirely between the two runs — expected behavior of
  Pearson's r this close to the sample-size floor, not a bug, but a reason to
  treat any single small-n correlation reading as provisional rather than a
  finding, until it holds up across multiple batches at meaningfully larger n
  (see `Ark_Fix_Plan_Findings.md` for the specific numbers).
  `TransformationImpactAnalysis` reports each transformation type's *and* exact
  transformation-combination's observed performance alongside the clean
  (`mutation_count == 0`) baseline from the same batch and a degradation delta,
  with a consistent sign convention (positive always means "worse than
  baseline," for both higher-is-better and lower-is-better metrics).
  `CalibrationDriftAnalysis` tracks the confidence-vs-accuracy gap across
  complexity bands. One real, worth-noting structural finding surfaced along the
  way (documented in the module and the worked example, not smoothed over): a
  genuinely clean baseline has zero real Issues, so `metrics.py`'s recall (and
  therefore category F1/localization accuracy) is *always* `None` there by an
  existing Milestone 6.3 rule — meaning category/localization degradation vs. a
  clean baseline is structurally unmeasurable, while calibration-ECE
  degradation is not. Serialization is one-directional
  (`analysis_to_dict`/`analysis_to_json` only, no `analysis_from_dict`, by
  explicit scope decision). A full worked example — four difficulty profiles,
  four seeds each, a mock agent whose accuracy degrades and overconfidence
  grows with complexity — lives in `examples/milestone6/analysis_example.json`.

- **Milestone 7**: the agent harness + experiment runner. `ark/harness/`
  (zero-dependency, inside `ark`) is the only code that ever actually calls an
  agent: `prompt.py` builds a prompt from `rendered.artifacts` alone (sourcing
  the required JSON shape and issue-type taxonomy straight from
  `ark.evaluator.schema` so it can't drift from what's actually validated),
  `response_parsing.py` recovers a JSON object from raw agent text (bare,
  markdown-fenced, or prose-wrapped), and `scripted_client.py` ships
  `ScriptedAgentClient`, a deterministic offline reference `AgentClient`. A
  real, Anthropic-SDK-backed `AgentClient` lives at
  `integrations/anthropic_agent_client.py` — deliberately **outside** `ark/`
  entirely, importing the `anthropic` package lazily, so Ark's own core stays
  at zero third-party dependencies and the agent being evaluated stays
  conceptually external to it (the same directional-import discipline
  `ark/adapters` already established for rendering targets, applied here to
  the agent side). `ark/experiment/` ties every subsystem together:
  `TrajectorySpec` describes one (baseline-or-generated estate, profile, seed)
  request, and `run_experiment()` runs the full Generator/hand-authored →
  Mutation → Render → Agent Harness → `evaluate()` → (batch)
  `analyze_reports()` arc, with optional JSON persistence. A full worked
  example (`examples/milestone7/`) runs `HeuristicNamingAgentClient` — a
  genuinely offline, ground-truth-blind agent that only reads rendered text
  for two real naming irregularities — across six trajectories end-to-end.

- **Milestone 8**: an interactive, browser-based UI (Streamlit) — a thin layer
  over Milestone 7's experiment runner, for local demonstration and research use
  only (no database, no auth, no cloud deployment). `ark/ui/logic.py` holds every
  piece of non-widget logic (building `AgentClient`s/`TrajectorySpec`s from UI
  selections, a passthrough to `run_experiment()`, and extraction of
  already-computed `EvaluationReport`/`ExperimentAnalysis` fields into
  display-ready rows) with zero Streamlit dependency, so it's fully unit-tested
  without Streamlit installed at all; `ark/ui/app.py` is the actual page. The UI
  exposes experiment configuration (agent choice, estate source, profile, seed,
  trajectory count), a Run Experiment button, a Results Dashboard (an "Experiment
  Summary" card at the top — agent model used, estate source, mutation profile,
  trajectory count, and the experiment-wide averages for complexity/F1/localization
  accuracy/calibration error, every one of them read straight off already-computed
  fields — `ExperimentAnalysis.experiment_summary` for the four averages, the
  constructed agent client's own `.model` property for the model string, never a
  hardcoded label — each metric's label also says whether higher or lower is better,
  via a small static lookup table, plus per-trajectory Environment Summary / Agent
  Performance / Failure Analysis below it), Research Visualization charts straight
  from `ExperimentAnalysis` and the run's per-trajectory reports (trajectory-level
  **scatter plots** — one point per trajectory, a single trajectory still rendering
  as a single point rather than a forced line — for both complexity-vs-performance
  and calibration drift (Brier score vs. complexity), each with an optional
  plain-Python OLS trendline layered on top of, never altering, the real data; the
  bucketed/averaged views these replaced as the primary chart are kept in a
  "Bucketed averages" expander under each, and Transformation Type Impact remains a
  bar chart — transformation type is a category spanning many trajectories, not a
  single trajectory's own score), an Artifact Viewer with an explicit
  "🟢 Visible to Agent" / "🔒 Hidden from Agent" separation, and JSON export
  buttons. Every major section also carries brief, plain-language explanatory text
  (captions and "ℹ️" expanders) covering what that section shows and what Ark is
  doing behind the scenes — documentation/UX only, no scoring or pipeline behavior
  attached to any of it. A small, additive, backward-compatible extension to Milestone 7's
  runner made this possible: `ark/experiment/runner.py` gained
  `TrajectoryRunResult`/`run_trajectory_spec_with_artifacts()` (exposing the
  rendered artifacts a trajectory's agent actually saw, which the runner already
  computed but previously discarded) — `run_trajectory_spec()` itself is
  byte-for-byte unchanged, now just a thin wrapper. The UI's default
  "ScriptedAgentClient" demo option reuses Milestone 7's naming-heuristic agent,
  promoted from a one-off example script into a proper, reusable
  `ark/harness/heuristic_client.py`. The agent selector's other two options, "Anthropic
  Claude Agent (API)" and "Gemini Agent (API)," are always shown (not hidden until
  configured) and connect to the real
  `integrations.anthropic_agent_client.AnthropicAgentClient` and
  `integrations.gemini_agent_client.GeminiAgentClient` respectively, each requested
  with a fast/cheap model appropriate for a one-off demo run
  (`claude-haiku-4-5-20251001` and `gemini-3.1-flash-lite`) — see
  [Running the interactive UI](#running-the-interactive-ui) for exact setup and
  API-key instructions. Selecting either without its package installed and/or its API
  key set shows a friendly, specific error rather than crashing the page; the offline
  option is unaffected either way.

266 tests pass across Milestones 0-4, 6.1-6.5, 7, and 8 (one additional test —
an end-to-end Streamlit `AppTest` run — is skipped in environments without the
optional `ui` extra installed; see below). See the plan document's Milestone
sections for the full write-up of each, including gaps found and deliberately
deferred.

No traffic simulator (Milestone 5) yet, and a CLI wrapper around Milestone 7's
`run_experiment()` (as a scripted/CI-friendly alternative to Milestone 8's UI)
is future work (Milestone 9 in the plan document) — see the plan document's
Section 3/4 for what's next.

## Layout

```
ark/
  core/
    models.py     # the ground-truth domain model (stdlib dataclasses)
    validate.py   # structural parsing + referential-integrity validation
    serialize.py  # estate -> dict/JSON (the inverse of validate.py's parsing)
  adapters/
    base.py       # technology-agnostic TargetAdapter interface + RenderedEstate
    mulesoft/
      renderer.py  # ground truth -> Mule XML / API yaml (pure functions, no I/O)
      manifest.py  # builds the artifact <-> entity <-> dependency mapping
      adapter.py   # orchestrates renderer.py + manifest.py per TargetAdapter
  generator/
    config.py      # GeneratorConfig — every parameter that controls generation
    vocabulary.py  # business-noun vocabulary + naming templates
    seeds.py        # seeded-RNG helpers (never Python's global random state)
    topology.py     # builds the abstract layered dependency graph
    generator.py    # turns a config into a real GroundTruthEstate + GenerationManifest
  mutation/
    base.py        # MutationOperator ABC, clone_estate, id-based entity lookup helpers
    operators.py    # the six concrete operators
    registry.py     # transformation_type -> operator instance
    ledger.py        # MutationRecord / MutationLedger — the answer key
    profiles.py      # Level 0-3 difficulty profiles (additive operator sets)
    engine.py        # run_trajectory() — orchestrates selection, application, ledger-building
  evaluator/
    issues.py        # MutationLedger -> deduplicated, observable Issue objects
    schema.py         # AgentOutput/Finding dataclasses + validation of the agent-output contract
    complexity.py      # dynamic, per-trajectory complexity model (agent-independent half)
    parser.py           # resolves agent artifact/entity references -> internal entity ids via the manifest
    matcher.py           # aligns resolved findings against Issues -> category/entity/artifact correctness signals
    metrics.py            # category-detection and entity-localization precision/recall/F1, kept separate
    calibration.py         # Brier score + sample-size-gated ECE
    explanation.py          # rule-based (no LLM judge) explanation-quality signals
    report.py                # EvaluationReport assembly + serialization (to_dict/to_json/from_dict)
    orchestrator.py            # evaluate() -- the single pipeline entry point
    analysis.py                 # analyze_reports() -- cross-report experiment analysis (complexity/transformation/calibration-drift), to_dict/to_json only
  harness/
    contract.py      # AgentClient -- the one-method generate(prompt) -> str interface
    prompt.py          # build_agent_prompt(artifacts) -- sources the taxonomy/JSON shape from ark.evaluator.schema
    response_parsing.py # extract_json_object() -- recovers JSON from bare/fenced/prose-wrapped agent text
    scripted_client.py   # ScriptedAgentClient -- deterministic, offline reference/test double
    runner.py             # run_agent_harness(artifacts, agent_client) -- the one function that actually calls an agent
  experiment/
    spec.py           # TrajectorySpec -- one (baseline-or-generated estate, profile, seed) request
    runner.py           # run_trajectory_spec()/run_trajectory_spec_with_artifacts()/run_experiment() -- the full end-to-end arc, with optional JSON persistence
  ui/
    logic.py          # Streamlit-free business logic behind the UI -- fully unit-tested without Streamlit installed
    app.py              # the actual Streamlit page: `streamlit run ark/ui/app.py`
integrations/
  anthropic_agent_client.py  # AnthropicAgentClient -- a real, Anthropic-SDK-backed AgentClient, deliberately outside ark/ (see its docstring)
  gemini_agent_client.py     # GeminiAgentClient -- a real, Google-genai-SDK-backed AgentClient, same reasoning, same pattern
examples/
  milestone0/
    ground_truth.json    # one hand-authored example estate (schema_version 0.1.0)
    expected_render.xml  # the independently hand-authored MuleSoft file it should render to
    render.py             # a one-off renderer for this one example (superseded by ark/adapters/mulesoft/)
  milestone1/
    ground_truth.json    # 4-application, 9-flow "Order Management" estate (schema_version 0.2.0)
    README.md             # scenario, architectural patterns demonstrated, and assumptions made
  milestone3/
    generate_examples.py  # regenerates the example estates below (deterministic)
    *.json / *.manifest.json  # generated estates at a few sizes/seeds, with their recipes
    README.md               # what each example demonstrates
  milestone4/
    generate_examples.py  # regenerates the transformation trajectory below (deterministic)
    transformed_level_*.json / ledger_level_*.json  # Milestone 1's baseline carried through Level 1-3
    README.md                # walkthrough of real ledger entries + a bug this process caught
  milestone6/
    generate_example_report.py / example_report.json         # Milestone 6.3 worked example
    generate_report_example.py / report_example.json         # Milestone 6.4 worked example (full EvaluationReport)
    generate_analysis_example.py / analysis_example.json     # Milestone 6.5 worked example (ExperimentAnalysis)
    README.md                                                   # all three, explained together
  milestone7/
    heuristic_agent_client.py    # HeuristicNamingAgentClient -- a real, offline, ground-truth-blind AgentClient
    run_experiment_example.py     # runs run_experiment() end-to-end across 6 trajectories
    run_output/                    # committed reports/*.json + analysis.json this script produces
    README.md                       # the heuristic explained, a bug it caught, how to swap in a real LLM
tests/
  test_milestone0.py   # validates the example + golden-file test + negative cases
  test_milestone1.py   # structural checks + referential-integrity negative cases + backward-compat check
  test_milestone2.py   # adapter golden-file tests, determinism, manifest correctness, core-model pin test
  test_milestone3.py   # generator determinism, validity, scaling, topology realism, config validation
  test_milestone4.py   # mutation reproducibility, ledger completeness, operator independence, profile ordering
  test_milestone6.py   # issue consolidation (compounding + net-zero), complexity determinism, agent-output validation, no-mutation regression guards
  test_milestone6_2.py # manifest traceability, entity resolution (incl. duplicate-name ambiguity), finding matcher, isolation regression guards
  test_milestone6_3.py # classification/localization metrics, calibration (incl. same-accuracy-different-calibration), explanation signals, no-mutation regression guards
  test_milestone6_4.py # report determinism (excl. timestamp), serialization round-trip, failure-analysis content, no-mutation and technology-independence regression guards
  test_milestone6_5.py # cross-report analysis determinism, empty/missing-report handling, correlation None-gating and sign sanity, baseline degradation sign convention, serialization, technology-independence regression guards
  test_milestone7.py   # harness isolation (AST + wiring-level spy), prompt/response-parsing correctness, TrajectorySpec validation, end-to-end experiment runs, integrations/ import-boundary check, AnthropicAgentClient tests against a fake SDK object
  test_milestone8.py   # ark/ui/logic.py fully exercised without Streamlit installed; artifact-viewer isolation guard (incl. a simulated manifest-leak case); Anthropic- and Gemini-agent isolation checks against fake SDK clients (no real network/API keys needed); trajectory-scatter-row (complexity and calibration) and linear-trendline unit tests; experiment-summary-card and metric-direction-hint tests; agent-model-label tests (incl. a wiring check against build_agent_client()); export-helper equivalence; import-boundary checks; a Streamlit AppTest end-to-end test that skips gracefully if Streamlit isn't installed
  golden/               # committed reference output for the adapter golden-file tests
    milestone0/
    milestone1/
```

## Running the tests

Milestone 0 has zero third-party dependencies on purpose (see the Milestone 0 note
in the plan doc), so no `pip install` is required to run it:

```bash
python -m unittest discover -s tests
```

This includes `tests/test_milestone7.py`'s `AnthropicAgentClient` tests and
`tests/test_milestone8.py`'s Anthropic- and Gemini-agent isolation tests — they all use
a fake, duck-typed stand-in for the relevant SDK's client object, not the real
`anthropic`/`google-genai` packages, so the full suite passes with zero `pip install`s.
Those packages are only needed to actually run `integrations/anthropic_agent_client.py`
or `integrations/gemini_agent_client.py` against a real model (`pip install -e
".[llm]"`). Similarly, `tests/test_milestone8.py`'s entire `ark.ui.logic` coverage needs
no `streamlit` install; its one Streamlit-`AppTest`-based end-to-end test skips
gracefully (rather than failing) if the optional `ui` extra isn't installed.

Once `pytest` is available in your environment, it will discover and run the same
tests unmodified (`pip install -e ".[dev]" && pytest`).

## Running the interactive UI

### 1. Install dependencies

```bash
pip install -e ".[ui]"          # streamlit + pandas -- needed to run the UI at all
```

Only needed if you also want to run a **real API-backed agent** option instead of the
offline demo agent:

```bash
pip install -e ".[llm]"         # anthropic + google-genai -- both live outside ark/ core (see integrations/__init__.py)
```

### 2. Set your API key (only needed for a real API-backed agent option)

The offline `ScriptedAgentClient` option needs no key at all and works immediately
after step 1.

To use the **Anthropic Claude Agent** option, set `ANTHROPIC_API_KEY` in the same
shell you'll launch Streamlit from:

**Windows PowerShell:**
```powershell
$env:ANTHROPIC_API_KEY="your_key_here"
```

**macOS/Linux:**
```bash
export ANTHROPIC_API_KEY="your_key_here"
```

To use the **Gemini Agent** option instead, set `GEMINI_API_KEY` the same way:

**Windows PowerShell:**
```powershell
$env:GEMINI_API_KEY="your_key_here"
```

**macOS/Linux:**
```bash
export GEMINI_API_KEY="your_key_here"
```

Get a Gemini key from [Google AI Studio](https://aistudio.google.com/apikey). Either
or both keys can be set at once — the agent dropdown just uses whichever one the
selected option needs. Never put a real key in any file in this repo (including this
README) — only in the environment variable, in the shell you launch Streamlit from.
This only needs to be set once per shell session, before running `streamlit run`.

### 3. Run the UI

```bash
streamlit run ark/ui/app.py
```

This opens a local browser tab (default `http://localhost:8501`) with:

1. **Experiment Configuration** (sidebar) — pick an agent:
   - **ScriptedAgentClient (offline)** — always available, no setup, no network call.
   - **Anthropic Claude Agent (API)** and **Gemini Agent (API)** — both always shown
     in the dropdown too. If you select either without having done steps 1-2 for it,
     clicking **Run Experiment** shows a friendly, specific error (missing package
     and/or missing API key) instead of crashing the page; the offline option and the
     other API option keep working regardless. When configured, Anthropic makes one
     real API call per trajectory to `claude-haiku-4-5-20251001`, and Gemini makes one
     real API call per trajectory to `gemini-3.1-flash-lite` — fast, cheap models
     chosen specifically for this kind of one-off interactive demo (see
     `ark/ui/logic.py`'s `ANTHROPIC_DEMO_MODEL` / `GEMINI_DEMO_MODEL`).

   Also pick an estate source (the Milestone 1 hand-authored estate, or a
   procedurally generated one), a complexity/profile level, a starting random seed,
   and how many trajectories to run.
2. **Run Experiment** — runs the real pipeline (Generator/hand-authored → Mutation
   Engine → Renderer → Agent Harness → Evaluator → Analysis) via
   `ark.experiment.run_experiment()` — no logic is duplicated in the UI, and exactly
   the same call path runs regardless of which agent is selected.
3. **Results Dashboard** — Environment Summary, Agent Performance (precision/recall/
   F1/localization accuracy/calibration), and Failure Analysis (missed issues,
   hallucinations, wrong diagnosis, overconfidence, and a fifth real bucket — wrong
   category — included for completeness) for whichever trajectory you select.
4. **Research Visualization** — complexity vs. performance, transformation-type
   impact, and calibration drift, straight from the `ExperimentAnalysis` this
   experiment produced.
5. **Artifact Viewer** — explicitly split into "🟢 Visible to Agent" (the exact
   rendered files the agent received — nothing else, regardless of which agent you
   picked) and "🔒 Hidden from Agent" (the real issues and raw agent output, shown
   only because a human researcher, unlike the agent, is allowed to see both sides
   at once).
6. **Export** — download buttons for the `EvaluationReport` and `ExperimentAnalysis`
   JSON.

### Recommended settings for a one-time demo with meaningful graphs

The Research Visualization charts need more than one trajectory, and more than one
complexity level's worth of contrast, to show anything interesting (a single
trajectory has no "vs. complexity" curve to plot at all). For one meaningful demo
run:

- **Agent**: Anthropic Claude Agent (API) or Gemini Agent (API), whichever you've set
  up a key for — otherwise the offline agent still produces real (if less
  interesting) graphs.
- **Estate source**: Milestone 1 hand-authored estate (small, fast, and the same
  estate every other worked example in this repo uses, so results are easy to
  cross-check).
- **Profile**: `level_2_structural` — enough real issues across enough transformation
  types to give the Failure Analysis and Transformation Type Impact sections
  something to show, without the longer run time of `level_3_legacy`.
- **Starting seed**: `1`.
- **Number of trajectories**: `5` — enough spread for the complexity-bucket and
  correlation charts to be non-degenerate, while keeping a real-API run to about 5
  model calls.

**On screenshots**: this milestone was built in a sandboxed environment with no
network access (so `streamlit` itself could not be `pip install`-ed or launched here)
and no browser to capture a screenshot from — rather than fabricate one, none are
included. `ark/ui/logic.py` (everything the page actually computes and displays, and
every isolation guarantee described above) is fully exercised by
`tests/test_milestone8.py` without Streamlit *or* the `anthropic`/`google-genai`
packages installed at all (the Anthropic- and Gemini-agent tests each use a fake,
duck-typed stand-in for their SDK's client object, same pattern as
`tests/test_milestone7.py`'s). `tests/test_milestone8.py` also includes a
`streamlit.testing.v1.AppTest`-based end-to-end test of the real page that will run
automatically — and should be run before relying on this UI — in any environment with
the `ui` extra installed.
