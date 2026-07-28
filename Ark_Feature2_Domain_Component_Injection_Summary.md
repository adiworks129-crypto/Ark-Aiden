# Ark — Feature 2: Domain-Conditioned Component Injection ("Organized Randomness")

## Scope actually done

One new, distinct, opt-in-only mutation operator (operator #7) that injects a
component realistic on its own terms but implausible for the estate's
assigned domain, plus the minimum model/data/taxonomy plumbing it needs.
Exactly two domains (finance, retail). Not folded into any existing Level
0–3 profile. No trajectory batches or agent calls run.

## 1. The `domain` concept

Added `GroundTruthEstate.domain: Literal["finance", "retail"] | None = None`
(`ark/core/models.py`) — optional, defaulting to `None`, so every existing
hand-authored ground-truth file (Milestone 0/1's examples) stays valid
unchanged. `GeneratorConfig.domain: str | None = None` (`ark/generator/
config.py`) plumbs it through generation; `generate_estate()` now passes
`domain=config.domain` onto the produced estate. `ark/core/validate.py`
gained a local `_VALID_DOMAINS = ("finance", "retail")` check in
`_parse_estate()` (rejects any other value, same pattern as
`_VALID_FLOW_TYPES`/`_VALID_LOG_LEVELS`). `SCHEMA_VERSION` bumped
0.2.0 → 0.3.0, following the exact additive-only discipline the 0.1.0 →
0.2.0 bump already established (new optional field, new union member —
nothing existing changed shape).

`_VALID_DOMAINS` (validate.py), `SUPPORTED_DOMAINS` (generator/config.py),
and the plausibility mapping's own top-level domain keys are three
independent, self-contained constants, not one shared import — importing a
shared constant from `ark.generator` into `ark.core` would invert the
established one-way "generator depends on core, never the reverse"
dependency direction. A dedicated test
(`test_only_two_domains_are_valid_in_validate_py`,
`test_config_supported_domains_matches_validate_py`,
`test_mapping_domains_match_the_two_supported_domains_exactly`) cross-checks
all three stay in agreement instead.

## 2. The component itself: a new Step kind

Ark's step model (Transform/FlowRef/Logger/ApiCall) had nothing
representing "a call to an external enterprise system" — `ApiCallStep`
specifically targets another *Ark-modeled* API within the same estate,
referentially checked by `validate.py`. Added `ConnectorStep` (`id`, `name`,
`description`, `connector_type`, `kind="connector"`) as a new, additive
`Step` union member — the exact extension point `ark/core/models.py`'s own
design notes describe ("new step kinds can be added later without changing
existing ones"). `connector_type` is a free-form key into the plausibility
catalog below, deliberately not cross-checked against that catalog by
`validate.py` (only checked for being non-empty) — doing so would require
`ark.core` to import `ark.generator`, the same dependency-inversion problem
noted above.

Rendering support for `ConnectorStep` was **not** added —
`ark/adapters/mulesoft/renderer.py` untouched, per the task's explicit
scope (items 1–5 never mention rendering, and no trajectories are run this
session to exercise it). A future session wiring this operator into a real
render/evaluate pass will need to teach the renderer how to emit a
`ConnectorStep`; flagged here as a known, deliberate gap, not an oversight.

## 3. The plausibility mapping — choices made and rejected

`ark/generator/domain_plausibility.json` (loaded via new
`ark/generator/domain_plausibility.py`, mirroring
`ark/validation/mulesoft_http_connector.py`'s `load_schema()` pattern):

Finance: core banking platform (Temenos T24 / FIS Profile / Finastra
Fusion), interbank payment rail / settlement network (SWIFT / ACH / Fedwire
/ card network settlement), regulatory compliance reporting (AML/KYC,
Basel/Dodd-Frank).

Retail: SAP Retail / SAP Supply Chain Management, point-of-sale (POS)
integration, warehouse management system (WMS) / logistics connector.

Each entry carries a `justification` string, the same "cite a real,
specific reason" discipline the HTTP connector schema used.

Judgment calls worth flagging explicitly:

- **SAP is scoped to its retail/SCM verticals, not "SAP" generally.**
  SAP also has a genuine, well-known finance/accounting module (SAP FI/CO),
  so an unscoped "SAP = retail" claim would be shaky. The mapping's
  `display_name`/`justification` are deliberately narrowed to "SAP Retail /
  SAP Supply Chain Management" — the specific, well-documented retail
  vertical the task's own example points at, not the SAP product line as a
  whole.
- **"Payment rail" is scoped to interbank/settlement networks, not merchant
  payment gateways.** Stripe/Adyen-style payment *gateways* are used by
  retail merchants too, which would have made a poor finance-exclusive
  signal. SWIFT/ACH/Fedwire/card-network settlement is specifically a
  bank/financial-institution-side integration with no retail equivalent —
  a meaningfully different, safer claim.

Pairings considered and **rejected** for insufficient domain-exclusivity
(all cross-cut both domains, which would make a poor "this doesn't belong
here" signal):

- CRM platforms (e.g. Salesforce) — heavily used in both wealth-management/
  banking CRM and retail customer engagement.
- Data warehouse / BI platforms (e.g. Snowflake, Databricks) — universal
  across industries.
- Identity/SSO providers (e.g. Okta, Azure AD) — universal infrastructure.
- HR/payroll systems (e.g. Workday) — universal back-office function.
- Fraud detection services — initially considered for finance, but retail
  e-commerce has its own well-known, dedicated fraud-detection vendors
  (e.g. Signifyd, Forter) for card-not-present transactions, so this would
  not have been a clean finance-only signal. Replaced with regulatory
  compliance reporting instead, which has no meaningful retail analog.
- Generic merchant payment gateways (e.g. Stripe/Adyen) for retail — left
  out for the same reason "payment rail" was scoped narrowly for finance
  (see above): not exclusive enough to one domain to be a confident signal.

## 4. The new issue type — live-sourced, not hand-added

`transformation_type = "domain_implausible_component"` on the new
`DomainComponentInjectionOperator` (`ark/mutation/operators.py`), registered
in `ark/mutation/registry.py`'s `OPERATOR_REGISTRY`. Nothing else needed
touching for this to reach the agent-facing taxonomy: `ark/evaluator/
schema.py`'s `ISSUE_TYPE_TAXONOMY = frozenset(OPERATOR_REGISTRY.keys()) |
{"other"}` is already derived from the live registry, and
`ark/harness/prompt.py` already iterates that taxonomy — confirmed with a
test asserting the new type is present in `ISSUE_TYPE_TAXONOMY` purely as a
consequence of registration, with no separate prompt edit anywhere.

The ledger entry shape needed **zero changes** to `ark/mutation/ledger.py`
or `ark/evaluator/issues.py` — both are already fully generic over
`transformation_type` (confirmed by reading `derive_issues()` and
`MutationRecord`/`MutationRecordDraft`). The new operator's records are a
"creation event," the exact same shape `LegacyVersionOperator`/
`DuplicateProcessingOperator` already use: `original_state = {new_id:
None}`, `transformed_state = {new_id: <full ConnectorStep dict>}`.
`ark/evaluator/complexity.py`'s `transformation_diversity` denominator
(`len(OPERATOR_REGISTRY)`) and `_owning_flow_id`'s generic step lookup also
needed no changes — both were already documented as generalizing
automatically to a 7th operator, and now do.

## 5. The operator — opt-in only

`DomainComponentInjectionOperator.find_candidates()` returns `[]` whenever
`estate.domain` isn't one of the two supported values — the engine's
existing "no eligible candidates → skip this operator" graceful-degradation
behavior (`ark/mutation/engine.py`, unchanged) means this operator is
simply inert for any estate without an assigned domain, with zero special-
casing needed elsewhere. When a candidate exists, `apply()` picks the
*other* domain's component set deterministically (exactly two domains, so
"the other one" is unambiguous — flagged in the code as needing a real
decision if a third domain is ever added), draws one component via the
trajectory's own `rng`, and appends one new `ConnectorStep` to an existing
flow. `severity` controls how much the injected step's own description
signals its own oddity (low: a completely ordinary-sounding integration
note; high: explicitly reads as an unusual, carried-over vendor choice) —
never which domain it's drawn from.

Gated via a new, separate `MutationProfile`,
`"domain_injection_preview"` (`ark/mutation/profiles.py`), with
`operator_types = ("domain_implausible_component",)` and a `level=-1`
sentinel (documented as "outside the 0–3 progression," purely descriptive
metadata, never used in any scoring computation). `level_1_minor`/
`level_2_structural`/`level_3_legacy`'s `operator_types` tuples are
byte-for-byte unchanged — verified with a dedicated test — so every
existing experiment batch run under those profile names remains exactly
reproducible and comparable to new runs under the same names.

## 6. Tests and existing-test updates

New file `tests/test_domain_component_injection.py` — 38 unit tests
covering: the `domain` field (default, round-trip, validation, rejection),
`GeneratorConfig.domain` plumbing, the plausibility mapping (loads, exactly
two domains, every component fully populated, no key shared across
domains), `ConnectorStep` structural parsing/rejection/duplicate-id
detection, operator registration + live-sourced taxonomy + opt-in-only
profile wiring, the injection logic itself (no candidates without a domain,
component always drawn from the *other* domain, exactly one new step
appended, estate stays valid, input never mutated, never a no-op,
severity changes the description), and ledger/issue-derivation shape via
the mutation engine directly (one creation-event record, reproducible,
exactly one derived `Issue` of the new type) — explicitly not via
`ark.experiment`/an agent, per this task's own scope.

Three **existing** tests needed conscious, documented updates (each a
deliberate, expected consequence of a real, justified schema/registry
change — not a silent pin update):

- `tests/test_milestone2.py`'s `TestAdapterDidNotChangeCoreModel` pin test
  — updated the pinned `SCHEMA_VERSION` (0.2.0 → 0.3.0) and the pinned
  `GroundTruthEstate`/new-`ConnectorStep` field sets, with a docstring
  explaining this is the conscious, justified update its own docstring
  anticipates.
- `tests/test_milestone4.py`'s `TestOperatorsWorkIndependently` — this
  suite's "every operator must find candidates on the plain Milestone 1
  estate" assumption no longer holds for the new, deliberately
  domain-dependent operator; split it out into its own test
  (`test_domain_dependent_operator_has_no_candidates_without_a_domain_but_does_with_one`)
  and switched the "every operator produces a valid estate" test to use a
  domain-tagged copy of the estate (behaviorally identical for the other
  six, domain-agnostic operators).
- `tests/test_milestone6.py`'s hand-verified level-3 complexity test —
  `transformation_diversity`'s expected value moved from `5/6` to `5/7`,
  exactly matching `ComplexityProfile.transformation_diversity`'s own
  documented "not a hardcoded 6, tracks automatically" behavior now that a
  7th operator genuinely exists; the numerator (5 distinct types realized
  on that fixed seed/ledger) is unaffected.

Full suite: **300 → 338 tests, all 38 additions, 0 failures** (1
pre-existing, unrelated skip). No trajectory batches or agent calls were
run anywhere in this session.

## Confirmed unchanged

`ark/validation/mulesoft_http_connector.py`, `ark/schemas/mulesoft/
http_connector.json`, `ark/validation/pipeline.py`,
`EvaluationReport.rendering_validation`; the renderer's global-config-
element fix and both golden-file decisions from the renderer-fix session;
the complexity score formula itself (only its already-generic, already-
documented registry-size denominator naturally picked up the new count);
the six original operators' own logic; `LEVEL_1/2/3_OPERATORS` tuples and
every existing profile's `operator_types`; evaluator core metrics
(precision/recall/F1, localization accuracy, Brier/ECE) and their
None-instead-of-0 behavior; the structural agent/ground-truth boundary (the
new issue type is ledger-only, never shown to the agent beyond what it
would see in rendered artifacts — which, since rendering isn't wired up
for `ConnectorStep` yet, is currently nothing at all); existing
`experiment_analysis.json` exports.
