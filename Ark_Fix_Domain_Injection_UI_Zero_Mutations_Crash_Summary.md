# Ark — Fix: `domain_injection_preview` Produced Zero Mutations + Crashed the UI

## Root causes (both confirmed by reading the code, matching the reporter's own hypotheses exactly)

**1. Zero mutations.** `ark/ui/logic.py`'s `build_trajectory_specs()` built every
generator-sourced trajectory as `GeneratorConfig(seed=trajectory_seed)` — no
`domain` argument at all, for any profile. The UI had no code path that
could ever set `GroundTruthEstate.domain`, so
`DomainComponentInjectionOperator.find_candidates()` (which correctly
returns `[]` for any domain-less estate — unchanged, per scope) always
found zero candidates, regardless of which profile was selected.

**2. The crash.** `ark/ui/app.py` line 403 called
`pd.DataFrame(logic.transformation_impact_rows(analysis)).set_index("transformation_type")`
with no check that the rows list was non-empty. `transformation_impact_rows()`
itself was already correct — it returns `[]` when
`analysis.transformation_impact_analysis.by_transformation_type` is empty.
An empty list produces a zero-column `DataFrame`, and `.set_index()` on a
column that doesn't exist raises `KeyError`. This is a generic UI gap, not
specific to the domain scenario — any zero-mutation experiment, from any
cause, would have hit it.

## Fixes applied

**`ark/ui/logic.py`**
- Added `DOMAIN_PROFILE_NAME = "domain_injection_preview"` and
  `DOMAIN_CHOICES = ("finance", "retail")` constants.
- `build_trajectory_specs()` gained a keyword-only `domain: str | None = None`
  parameter, threaded into `GeneratorConfig(seed=..., domain=domain)` only
  for the generator estate-source branch. The hand-authored Milestone 1
  branch ignores it (that estate has no domain field or override
  mechanism). Every existing positional call site is unaffected — the
  parameter is optional and keyword-only.

**`ark/ui/app.py`**
- A domain selector now appears only when `profile_name ==
  "domain_injection_preview"`: a `finance`/`retail` dropdown if the
  estate source is Generator, or a warning explaining why Milestone 1
  can't be domain-tagged if that source is selected instead. Every other
  profile's flow is untouched.
- The Transformation Type Impact chart is now wrapped in
  `if not transformation_rows: st.info(...) else: <build chart>` —
  the same pattern already used twice elsewhere on the page for
  empty/undefined metrics. The guard checks emptiness, not cause, so it
  holds for any zero-mutation scenario.

## Tests added (`tests/test_milestone8.py`) — 356 tests total, up from 348, all passing

- `TestTrajectorySpecBuilding`: `domain` is ignored for non-domain profiles
  and for the Milestone 1 estate source; it flows into `GeneratorConfig`
  only for `domain_injection_preview` + Generator; it defaults to `None`.
- `TestDomainInjectionUiWiring`: running the engine directly (not a
  trajectory batch — same convention as `tests/test_domain_component_injection.py`)
  on a generator estate built via the fixed wiring confirms a domain
  actually set produces a non-zero mutation count, and confirms a domain
  left unset still reproduces the original, correct zero-mutation
  behavior.
- `TestTransformationImpactRowsWithZeroMutations`: a real zero-mutation
  experiment (this exact bug scenario) confirms `transformation_impact_rows()`
  returns `[]` without raising.

## Confirmation run

**Substitution note (stated plainly):** the requested agent was Gemini, but
this sandbox has no outbound network access, so this run used
`ScriptedAgentClient` (the offline demo agent) instead, called through
`ark.ui.logic.run_ui_experiment()` exactly as the Streamlit page would.
Scope: Generator estate source, `domain_injection_preview` profile,
seed=1, n=5, domain=`finance` (not previously selectable in the UI; this
is the first exercise of it end-to-end).

**Results, reported plainly:**

| trajectory | mutation_count | complexity_score |
|---|---|---|
| seed1 | 1 | 0.09376 |
| seed2 | 1 | 0.07674 |
| seed3 | 1 | 0.12268 |
| seed4 | 1 | 0.07857 |
| seed5 | 1 | 0.14777 |

- `experiment_summary.average_complexity_score`: 0.10390456517378241
- `experiment_summary.transformation_type_distribution`: `{"domain_implausible_component": 5}`
- `transformation_impact_rows(analysis)`: one row, for `domain_implausible_component`,
  `observed_category_f1` / `category_f1_degradation` / `calibration_ece_degradation`
  all `null` — no crash calling it or building the chart's DataFrame from it.
- Per-report agent performance: `recall = 0.0`, `precision = None`, `f1 = None`
  for all 5 trajectories — the scripted agent produced no findings matching
  the injected issue on any of them. This is expected of `ScriptedAgentClient`
  (a canned offline stub, not a reasoning agent) rather than a signal about
  the feature itself; a real agent (Gemini, once network access is available)
  would need to be run separately to get a meaningful performance read on
  this profile.

No crash occurred anywhere in this run, including simulating the chart's
data path directly.
