# Ark Evaluation Harness — Fix Plan Findings

Findings from the compute-conscious fix plan, run against the two real batches provided (`JSON outputs/experiment.analysis.json` = n=5, `JSON outputs/experiment.analysis (1).json` = n=15, both Level 3 / `level_3_legacy`, same hand-authored estate, plus one individual trajectory report, `level_3_legacy-seed2.report.json`). No code was changed. No new trajectory batches were run.

## Issue 1 — No clean baseline (confirmed, action needed)

Confirmed: `transformation_impact_analysis.baseline` is `null` in both batches. This is exactly the missing-Level-0-baseline case described — every `category_f1_degradation` etc. is unmeasurable until a Level 0 batch exists on the same estate.

**Status: not fixable from this environment.** This sandbox has no outbound network access to Anthropic's or Google's APIs (confirmed earlier this session — a `pip install` hit a blocked proxy), and the existing batches' `raw_agent_output` reads like real LLM output, not the offline scripted agent — so I can't reproduce "the same agent" here without real API access. Running it with a different (offline) agent would not be a valid baseline for comparison.

**Next step (for you, in your own environment):** open the Streamlit UI (`streamlit run ark/ui/app.py`) and run:
- Agent: whichever real agent you used for the existing batches (match it exactly)
- Estate source: Milestone 1 hand-authored estate
- Profile: `level_0_clean`
- Starting seed: `1`
- Number of trajectories: `5`

Then export `experiment.analysis.json` from that run and add it alongside the two you already have (rename to avoid overwriting, e.g. `experiment.analysis.level_0.json`) so all three stay comparable.

## Issue 2 — Correlation sign instability (confirmed; documentation updated, no code change)

Confirmed exactly as described — all three headline correlations reverse sign between n=5 and n=15:

| metric | n=5 | n=15 |
|---|---|---|
| complexity vs. category_f1 | −0.329 | +0.408 |
| complexity vs. entity_localization_accuracy | −0.463 | +0.167 |
| complexity vs. calibration_ece | +0.236 | −0.380 |

This is expected behavior of Pearson's r at these sample sizes (both are just above the `min_sample_size=5` gate, where r is still very noisy) — not a bug, and the existing `None`-gating/disclaimer infrastructure in `CorrelationStatistic` is untouched. Documentation updated (README + the UI's correlation-coefficients caption) to state plainly that direction is not yet stable and that this sign reversal is itself evidence for why — not just an assertion that "n is small."

## Issue 3 — Complexity-score compression (root cause identified; no constants changed yet)

Using the one available trajectory's raw `complexity_profile` plus both batches' bucket data:

The estate itself is small — 4 apps, 8 APIs, 14 flows, **only 9 total dependency edges**. Two of the six complexity sub-terms are structurally unable to approach their normalization caps in this estate: `dependency_impact_mean` (cap 5.0) hit only **0.029** normalized on the sampled trajectory, and `interaction_score` (already 0–1, uncapped) sat at **0.143** — both low because a 9-edge graph can't produce high in-degree or tight clustering among 10 mutated entities. Separately, `mutation_count` is a hard ceiling problem, not a sampling one: Level 3 (`level_3_legacy`) is configured for a **fixed** 10 mutations (not a range), against a normalization cap of 15 — so that term is capped at exactly **10/15 = 0.667** for every Level 3 trajectory, no matter the seed. Severity and diversity terms do reach reasonably close to their own ceilings.

Averaging one structurally-capped term (0.667), two structurally-suppressed terms (~0.03–0.14), and two closer-to-ceiling terms lands almost every Level 3 trajectory in a narrow band around 0.4–0.5 — matching the observed 18/20-in-one-bucket, zero-above-0.60 pattern.

**Recommended (not yet applied):** lower `_MUTATION_COUNT_NORM_CAP` (15.0 → nearer 10–12, matching what Level 3 actually configures) and `_DEPENDENCY_IMPACT_NORM_CAP` (5.0 → nearer 1.5–2.0, matching this estate's real edge density); `_COMPOUNDING_NORM_CAP` (5.0) may also be too generous for a 10-mutation trajectory. This changes only the three numeric constants — the formula stays "mean of 6 clamped, equal-weighted sub-terms." **I have not made this change.** Per your instructions this needs a version-tag/changelog note in `analysis_schema_version` if applied, since it makes old and new complexity scores non-comparable, and it's built on n=1 trajectory's raw values, not all 20 — worth confirming with the full 20 individual report exports before committing to new constants.

## Issue 4 — Category-matching over-crediting (checked; no bug found)

Traced the concern through the actual code path, not just the field definition. Good news: **the coarse, entity-agnostic `category_correct` field is not what drives the headline precision/recall/F1 metrics at all.** Those are computed by `metrics.is_true_positive()`, which already requires both a specific entity match *and* the claimed type matching that exact matched Issue's real type — i.e., the strict check the plan worried might be missing already exists and is already what backs F1. `category_correct` (the looser field) is only consumed in one place: `report.py`'s `wrong_category_predictions` diagnostic bucket, which never feeds into a score.

On the one available trajectory, no finding both resolved to an entity and claimed a type from the `duplicate_processing`/`legacy_version_introduction` pair at the wrong one of the two — the one finding that claimed `duplicate_processing` was too vague (`entity_reference="customer-system.xml"`, a whole file) to resolve at all, and scored as a plain hallucination, not a mismatched category-credit.

**No additive `category_correct_strict` metric is recommended right now** — this data suggests the existing `is_true_positive()` already does the stricter check where it matters (scoring), and the coarse field is correctly scoped to a diagnostic-only bucket. This is n=1 of 20 trajectories, so treat it as a preliminary, not exhaustive, check — if you want full confidence, the case to look for specifically is a finding whose `entity_reference` cleanly resolves to a `legacy_version_introduction` entity while claiming `duplicate_processing` (or vice versa), which this one trajectory didn't happen to produce.

## What's still open

- Run the Level 0 baseline batch yourself (Issue 1) — the one item that needs real compute, and needs to be your own agent/environment.
- If/when you export all 20 individual trajectory report JSONs (not just the two aggregate `experiment.analysis.json` files), Issues 3 and 4's conclusions above can be checked against the full dataset instead of n=1 — I'd recommend that as the next cheap step before touching any constants.
