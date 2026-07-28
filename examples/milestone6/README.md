# Milestone 6.3 / 6.4 worked examples

`generate_example_report.py` runs the full evaluator pipeline built across Milestones
6.1-6.3 against a real trajectory (Milestone 1's baseline, `level_2_structural` profile,
seed 7 — 6 raw mutation records, 5 consolidated Issues) and a hand-written, illustrative
agent output (not a real model) designed to exercise every metric this milestone
introduces. `example_report.json` is its committed output.

## What the agent output deliberately gets right and wrong

| Finding | Intent | What actually happened |
|---|---|---|
| `finding-000` | Correct: naming_drift on the Order Status Experience API | True positive (category + entity both correct) |
| `finding-001` | Correct: documentation_decay on a Customer step | True positive |
| `finding-002` | Wrong entity: real issue_type, but the named entity was never mutated | Unresolved reference -> false positive on both axes |
| `finding-003` | Wrong category: right entity, wrong issue_type | Entity-correct (true positive for localization), but see the nuance below |
| `finding-004` | Pure hallucination: real artifact and entity, nothing mutated there | False positive on both axes |
| *(unmentioned)* | The agent never mentions the `naming_drift` issue on the Order Processing API at all | False negative |

## A real nuance this example surfaced, worth being honest about

`step-inventory-log-ref` carries **two** real issues in this trajectory: `duplicate_processing`
(the flow it's part of was duplicated) and `dependency_change` (its own target was repointed).
`finding-003` names the wrong category (`documentation_decay`) for this entity, so the
matcher's tie-break (prefer the issue whose type matches the claim, else the first
candidate) falls back to `duplicate_processing`. That "uses up" the entity match for
`duplicate_processing` — meaning `dependency_change` is left with **no** finding pointing at
it at all, and shows up as a separate false negative in the per-category breakdown, even
though a human would say "the agent was looking at the right place." This is a known,
documented limitation of resolving one finding to one issue per entity: when an entity
carries multiple real issues, a single wrong-category claim can only ever localize one of
them. Not fixed in Milestone 6.3 — flagged here rather than silently smoothed over.

## Reading the report

- `category_metrics`: precision 0.4, recall 0.4 (2 of 5 issues correctly identified by type
  *and* location).
- `category_metrics_by_type`: shows `documentation_decay` and `naming_drift` each partially
  right, `dependency_change` and `duplicate_processing` each with a false negative — this is
  the "which transformation types are hardest" breakdown.
- `entity_localization_metrics`: precision/recall/localization_accuracy all 0.6 — higher than
  category accuracy, because `finding-003` gets credit here even though it named the wrong
  issue_type.
- `calibration`: Brier 0.33, ECE 0.45 — this agent is meaningfully overconfident (its average
  confidence, ~0.75, is well above its true 40% category accuracy).
- `explanation_signals`: `finding-000` and `finding-001` both reference a real observable
  symptom; `finding-002`, `finding-003`, and `finding-004` are flagged
  `unsupported_assumption_flag: true` — none of them ground their claim in the artifact name
  or a real symptom text.
- `complexity_score` / `mutation_count` / `agent_accuracy` / `calibration_error`: the
  `TrajectoryPerformanceRecord` convenience fields — a single row that could later sit
  alongside many others for the Milestone 6.5 complexity-vs-performance correlation.

## Regenerating (Milestone 6.3 example)

```bash
PYTHONPATH=. python3 examples/milestone6/generate_example_report.py > examples/milestone6/example_report.json
```

---

## Milestone 6.4 worked example: `report_example.json`

`generate_report_example.py` runs `ark.evaluator.orchestrator.evaluate()` — the single
Milestone 6.4 pipeline entry point — against the same trajectory (Milestone 1 baseline,
`level_2_structural`, seed 7) and a five-finding agent output covering every failure mode
Milestone 6.4's Failure Analysis section is meant to surface (correct, hallucination, wrong
entity, wrong category, missed issue). `report_example.json` is the complete, serializable
`EvaluationReport` this produces — `example_report.json` above shows the same underlying
numbers in isolation; this shows them assembled into the full report artifact, with the
metadata/environment/transformation/issue context around them.

A few things worth pointing out when reading it:

- `metadata.generated_at` is pinned to a fixed timestamp in this committed example (via
  `evaluate(..., generated_at=...)`) specifically so the file is reproducible byte-for-byte —
  a real evaluation run leaves this to the actual clock, which is the one field
  `Ark_Evaluator_Design.md`'s reproducibility tests deliberately exclude from equality checks.
- `issue_summary.net_zero_transformation_count` and `net_zero_groups` — new in 6.4 — surface
  raw mutation groups that cancelled out to no observable difference (see `issues.py`'s
  `derive_issue_diagnostics()`), a number that previously had no way to reach a report at all.
- `failure_analysis` has five separate lists (`missed_issues`, `hallucinated_findings`,
  `wrong_category_predictions`, `correct_location_incorrect_diagnosis`,
  `overconfidence_patterns`) rather than one blended "wrong" bucket — each entry explains
  itself in plain language via its `detail` field.
- `research_hooks` duplicates nothing new: `complexity_score`, `category_metrics_by_type`, and
  `calibration_ece` are pointers back to numbers already present in full elsewhere in the same
  report, gathered in one place for a future Milestone 6.5 batch script to read without
  re-deriving them.

### Regenerating

```bash
PYTHONPATH=. python3 examples/milestone6/generate_report_example.py > examples/milestone6/report_example.json
```

---

## Milestone 6.5 worked example: `analysis_example.json`

`generate_analysis_example.py` builds a batch of 16 `EvaluationReport`s — all four difficulty
profiles (`level_0_clean` through `level_3_legacy`), four seeds each — using a *simulated*
agent (not a real model, same spirit as the hand-written `AGENT_OUTPUT` above) whose accuracy
degrades and whose overconfidence grows as the profile level increases, by construction. Those
16 reports are then passed to `ark.evaluator.analysis.analyze_reports()`, and
`analysis_example.json` is the resulting `ExperimentAnalysis`, serialized via
`analysis_to_dict()`.

The point of deliberately engineering the mock agent's behavior (rather than reusing one fixed
`AGENT_OUTPUT` across profiles) is that Milestone 6.5's whole job is to reveal *trends across
many reports* — a single report, or several reports from an identical agent, has nothing
interesting for a correlation coefficient or a baseline-degradation delta to find.

### A structural nuance worth reading before the numbers

`level_0_clean` (the baseline profile) has **zero real Issues by construction**. `metrics.py`'s
`recall` is `None` whenever there are no real issues to find (an existing Milestone 6.3 rule,
unchanged here) — so `category_f1` and `entity_localization_accuracy` are **structurally
`None` for every clean trajectory**, regardless of what the agent does. This means, in
`transformation_impact_analysis`, `category_f1_degradation` and
`entity_localization_degradation` are `None` for every transformation type and combination in
this example — not a bug, but an honest consequence of an already-frozen metrics decision:
there is no such thing as a "clean-baseline category F1" to degrade from. To still give
`calibration_ece_degradation` a real, non-`None` baseline value (ECE only needs the agent to
make *some* confidence claims, not for real issues to exist), the mock agent hallucinates a
handful of findings with varied confidence even on the clean profile — see
`LEVEL_BEHAVIOR[0]` in the script for the exact reasoning.

### Reading the output

- `experiment_summary`: 16 trajectories, average complexity ~0.24, and a
  `transformation_type_distribution` showing how often each of the six operators appeared
  across the batch.
- `complexity_analysis.buckets`: category F1 drops from 1.0 in the lowest complexity band to
  ~0.33 in the 0.40-0.60 band in this run — the "does performance degrade with drift" question,
  answered directly.
- `complexity_analysis.correlations`: category F1 and entity localization accuracy both
  correlate strongly negatively with `complexity_score` (around -0.84 and -0.89 in the
  committed run); calibration ECE correlates positively (~0.43) — the mock agent gets both less
  accurate *and* more overconfident as complexity rises, exactly as engineered. Every
  correlation carries its own sample size and the fixed no-causation disclaimer.
- `transformation_impact_analysis`: `by_transformation_type` and
  `by_transformation_combination` are sorted worst-first by category-F1 degradation (all `None`
  here, per the nuance above) — `calibration_ece_degradation` is the axis with real signal in
  this particular committed example.
- `calibration_drift_analysis.points`: average stated confidence vs. average category F1 per
  complexity band, plus the confidence-minus-accuracy gap — watch this gap widen as complexity
  increases.

### Regenerating

```bash
PYTHONPATH=. python3 examples/milestone6/generate_analysis_example.py > examples/milestone6/analysis_example.json
```
