# Milestone 3 example estates

These are generated, not hand-authored — produced by `generate_examples.py`, which is
deterministic (re-running it produces byte-identical files). Each `.json` estate has a
matching `.manifest.json` recording exactly what would be needed to reproduce it: the
seed, the generator version, the core schema version, and the full config used.

| File | Config highlights | Apps | What it demonstrates |
|---|---|---|---|
| `small_seed1.json` | seed=1, 1 experience / 1 process / 2 system | 4 | The smallest realistic layered estate. |
| `small_seed2.json` | seed=2, same config as above | 4 | Same shape, different seed — different business nouns chosen throughout (`notification`/`vendor`/`account`/`fulfillment` vs. `inventory`/`invoice`/`pricing`), proving the seed genuinely drives variation. |
| `medium_seed1_shared_dependency.json` | seed=1, 1 experience / 2 process / 3 system, density=0.6 | 6 | A small system-API pool shared by two process APIs — a **shared dependency** (fan-in) emerges from the topology rules rather than being special-cased. |
| `large_seed42.json` | seed=42, 2 experience / 3 process / 4 system | 9 | Demonstrates the generator scales cleanly to a larger, still-valid estate. |

## A worked example of "naming doesn't imply relationship"

In `medium_seed1_shared_dependency.json`, the single experience API is
`app-inventory-experience` — but its entry flow's `ApiCallStep` targets
`api-payment-process-v1`, not anything named "inventory". (You can also see this in
`small_seed1.json`, where the *only* process API happens to also be named
`inventory-process` — so the experience API's call target matching its own name there
is coincidence, not a rule: with only one process API to choose from, any name would
have matched.) The lesson holds either way: never assume a name implies a dependency —
trace the actual `ApiCallStep`/`FlowRefStep` edges. See `ark/generator/vocabulary.py`'s
docstring for why this is a deliberate design choice, not an artifact of small vocabulary.

## Regenerating

```bash
PYTHONPATH=. python3 examples/milestone3/generate_examples.py
```
