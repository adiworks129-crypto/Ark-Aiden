# Ark — Investigation: Declining F1 / Worsening ECE Across 4 `level_3_legacy` Batches

## Data actually available

Only batches 1 (n=5) and 2 (n=15) have files in the workspace (`JSON
outputs/experiment.analysis.json`, `experiment.analysis (1).json`, and one
individual report per batch, both for `seed=2`:
`level_3_legacy-seed2.report.json` / `...report (1).json`). Batches 3 and 4
(the two with worse F1/ECE, 0.202/0.634 and 0.180/0.664) are described only
by the aggregate numbers in this task's prompt — no export files for them
exist in this workspace, so several checks below are necessarily limited to
"what batches 1–2 show" plus code inspection, with the gap called out
explicitly rather than papered over.

## Ranked findings

### 1. (Most likely, directly demonstrated) Agent response variance on identical input, independent of any code change

This is not a hypothesis — it's directly observable in the two files
available. `level_3_legacy-seed2.report.json` (batch 1) and
`...report (1).json` (batch 2) are **byte-identical on every ground-truth/
mutation/complexity field**: same `environment_summary`
(4 apps/8 APIs/14 flows/9 dependencies/12 artifacts), same `total_mutations`
(10), same `distinct_transformation_types`, same `severity_mean`/`severity_max`
(0.6974/0.8995), same `affected_entity_count` (14), same `complexity_score`
(0.428135...). This is exactly what the engine's determinism guarantee
requires (same baseline + same profile + same seed -> same transformed
estate + same ledger) — proving both reports were scored against the
*exact same rendered artifacts*.

Yet the **agent's own output** differs substantially between them:

| | batch 1 (seed2) | batch 2 (seed2) |
|---|---|---|
| total findings | 6 | 5 |
| true positives | 3 | 1 |
| category F1 | **0.375** | **0.133** |
| calibration ECE | **0.425** | **0.680** |

Same prompt-worthy content, same ground truth, same everything Ark
controls — and the F1/ECE swing between these two single trajectories is
already larger than the entire 4-batch "trend" the task describes (0.305 ->
0.180 average F1). This demonstrates, with actual data rather than
speculation, that a real agent's run-to-run variance alone is fully
sufficient to produce swings of this size with **zero** code involvement.
Averaging over only 5 or 15 trajectories per batch is nowhere near enough
to average that variance out — both batches sit barely above the
`min_sample_size=5` ECE gate, the same small-n regime already flagged (and
explicitly out of scope to re-litigate) for the complexity-correlation sign
flips.

**This does not by itself explain why the trend looks monotonically
directional across 4 batches rather than bouncing around** — but given
that even batch-1-to-batch-2 (same code, confirmed identical ground truth)
already moved in the same declining direction the task attributes to later
code changes, a real, memoryless declining trend is not necessary to
explain what's observed; four small, noisy samples drawn from a
wide-variance process can easily look directional by chance. I cannot rule
out a real, code-caused decline with the data available — but I also
cannot rule it in over pure variance, and the evidence I *do* have shows
variance this large is real and already present before any relevant code
change happened.

### 2. (Plausible, quantified, unconfirmed) Renderer fix increased prompt size ~9.45%

Reconstructed "before" output by patching out
`_render_http_connector_configs()` (the exact function the renderer-fix
session added) and re-rendering the same seed=2 transformed estate:

| | before renderer fix | after (current) | delta |
|---|---|---|---|
| total rendered characters (all artifacts) | 9,265 | 10,141 | **+876 (+9.45%)** |

The increase is entirely the new `<http:listener-config>`/
`<http:request-config>` blocks (3–4 lines added per `.xml` file, `.yaml`
files unaffected). This is a real, measurable, unavoidable side effect of
fixing a genuine correctness bug (the dangling config-ref) — not a flaw in
that fix. A ~9% longer prompt, containing new boilerplate elements an agent
might reasonably (and validly) reference, is a real way a "pure bug fix"
could still shift agent-facing difficulty, exactly as this task's framing
anticipated. I have no direct before/after-same-agent comparison to
confirm this actually moved F1/ECE (that would require running the
identical trajectory through the same real agent both ways, which this
task's scope forbids) — so this is a plausible, quantified contributing
factor, not a confirmed one.

### 3. (Real risk, cannot confirm or rule out from here) Stale Streamlit process from Ctrl+C-only restarts

Checked `ark/ui/` for anything that would make this *unnecessary* to worry
about: no `st.cache_resource`/`st.cache_data` anywhere, no
`functools.lru_cache` or singleton pattern anywhere in `ark/` at all
(checked the whole package, not just `ark/ui`), and `build_agent_client()`
constructs a brand-new `AgentClient` instance on every "Run" click (never
reused from `st.session_state` — only its resulting label string is
stored there). `ark.generator.seeds.make_rng()` always returns a fresh
`random.Random(seed)`; nothing here touches Python's global `random`
state. **Within one correctly-restarted process, there is no in-code
mechanism for stale state to leak across runs.**

The real risk is at the OS/process level, which no file in this workspace
can confirm either way: `Ctrl+C` followed immediately by
`python -m streamlit run` does not guarantee the prior server process
fully released its port before the new one binds. If it didn't, Streamlit
can silently fall back to a different port for the new process — and a
browser tab left open from before the restart would keep talking to the
*old*, already-running process over its existing connection, not the new
one. If that happened, the "new" batches would actually still be running
old code — which would argue *against* the code changes being the cause
(same code as batch 1/2, just more noise), not for it. I have no server
logs or process list from when these batches ran, so I can't confirm or
rule this out — flagging it as a real, plausible mechanism worth checking
by fully killing all python/streamlit processes and closing every browser
tab before the next run, independent of whatever else this investigation
concludes.

**One confirmed, concrete, previously-unflagged side effect found while
checking this area:** `ark/ui/logic.py`'s `PROFILE_CHOICES = list(PROFILES.keys())`
is computed once at import time from the live registry — so, as a
consequence of Feature 2 registering `domain_injection_preview`, the UI's
profile dropdown now lists 5 profiles instead of 4, including the
still-experimental opt-in one, with no special labeling distinguishing it
from the reviewed Level 0–3 progression. This is **not** implicated in the
batches examined here (both available reports show `profile_name:
"level_3_legacy"` explicitly), but is a real, confirmed fact worth knowing
before running anything further through that UI.

### 4. (Checked directly, ruled out) `level_3_legacy`'s operator set

```
level_3_legacy.operator_types == ('naming_drift', 'documentation_decay',
    'duplicate_processing', 'dependency_change',
    'legacy_version_introduction', 'schema_inconsistency')
num_mutations == 10, severity_range == (0.5, 0.9)
```

Confirmed directly against the live registry (no git history exists in
this workspace to diff against — checked instead by inspecting the current
values and cross-referencing every prior session's own diff record: the
Feature 2 session only ever *appended* a new, separate
`"domain_injection_preview"` entry to `PROFILES`; `level_3_legacy`'s own
dict entry was never touched in any session). The existing pin/regression
tests covering this (`tests/test_domain_component_injection.py`'s
`test_new_operator_is_not_folded_into_any_existing_level_0_3_profile`,
`tests/test_milestone4.py`'s `TestMutationProfiles`) all still pass. Both
available batches' `by_transformation_type` breakdowns show all six
original operators represented at roughly proportional counts to
`trajectory_count` — no sign of a skewed subset. **Ruled out** as a factor
for batches 1–2; by extension (same untouched profile definition) very
unlikely to explain batches 3–4 either, though I can't directly inspect
their per-seed breakdown without their export files.

### 5. (Data gap — partially checked) Seed reuse / harder-combination sampling

The one shared individual report (`seed=2`) confirms batch 2 (n=15) reused
at least one of batch 1 (n=5)'s starting seeds — consistent with the
ordinary, sensible experimental design of "extend the same batch with more
seeds," not a bug. Both batches' `by_transformation_type` breakdowns show
even, proportional operator representation with no visible skew toward
harder combinations. **I cannot check this for batches 3–4** — no export
files for them exist in this workspace, so I can't confirm whether their
seeds were fresh, reused, or (as the task's "seed1" label for batch 4
might hint) drawn from a different starting-seed convention. Flagging this
explicitly as unresolved rather than guessing.

### 6. (Cannot confirm from here — likely user-side) Agent/model consistency

`ark/ui/logic.py`'s `ANTHROPIC_DEMO_MODEL`/`GEMINI_DEMO_MODEL` constants
were never touched in any session in this thread, so if the same UI
dropdown choice was used every time, the same model *string* would have
been requested each time. But: (a) `EvaluationReport` does not record
which agent/model actually produced a run anywhere — a genuine
traceability gap, confirmed by reading `EvaluationMetadata`'s full field
list, which only carries `adapter_name`/`adapter_version` (the MuleSoft
*renderer* adapter, unrelated to the agent); (b) if the real agent used is
Gemini, `GEMINI_DEMO_MODEL = "gemini-3.1-flash-lite"` is a non-dated model
alias — providers can and do update the weights behind such aliases over
time with no code change on Ark's side at all, a well-known LLM-eval
confound; (c) which UI dropdown option was actually clicked for each batch
is a user-side action this codebase has no record of. I can't confirm or
rule this out from anything in this workspace — stated plainly as a
genuine unknown, not glossed over.

## Summary ranking

1. **Agent response variance / small-sample noise** — directly demonstrated with real data (same trajectory, same ground truth, 0.375 vs 0.133 F1). Most likely to account for most or all of the observed movement.
2. **Renderer fix's ~9.45% prompt-size increase** — real, quantified, plausible contributing factor; not confirmed causal.
3. **Stale Streamlit process from partial restarts** — a real, unconfirmable-from-here risk; if true, argues against (not for) code changes being the cause.
4. **`level_3_legacy` operator-set change** — ruled out; confirmed byte-for-byte unchanged.
5. **Seed reuse / harder-combination sampling** — no evidence of skew in the 2 batches checked; unresolved for batches 3–4 due to missing export files.
6. **Agent/model config drift** — plausible in principle (especially an unpinned model alias), unconfirmable from workspace data, likely a user-side/UI fact rather than a code fact.

## No code changes made; nothing proposed to apply yet

If a fix is wanted, the two concrete, low-risk candidates surfaced here are:
(a) start recording which `AgentClient`/model actually produced each report
(closing the traceability gap in finding 6), and (b) labeling
`domain_injection_preview` distinctly in the UI's profile dropdown (finding
3) so it's never confusable with the reviewed Level 0–3 progression.
Neither has been implemented — both are proposals for a future, separate
session, per this task's scope.
