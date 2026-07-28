# Milestone 1 example estate: Order Management

## Scenario

A retail company's order-management integration layer, modeled on MuleSoft's classic
**API-led connectivity** pattern (Experience / Process / System layers):

- **Order Status Experience API** (`app-order-status-experience`) — customer-facing.
  Looks up an order's status by calling the Process API, then returns a response.
- **Order Processing Process API** (`app-order-processing-process`) — orchestrates
  order processing. Has *two* entry flows: an HTTP-triggered "process order" flow and
  a scheduler-triggered nightly reconciliation job. Both call out to two System APIs.
- **Inventory System API** (`app-inventory-system`) and **Customer System API**
  (`app-customer-system`) — thin wrappers around backend systems of record.

## Architectural patterns demonstrated

- **Layered API dependency graph**: Experience → Process → System(s), expressed via
  `ApiCallStep.target_api_id` (a network dependency, not a flow-ref).
- **In-application shared sub-flow reuse**: `flow-validate-order` is flow-ref'd by
  *both* of the Process API's entry flows (`process-order-main-flow` and
  `nightly-order-reconciliation-flow`) — the "shared component" this milestone asked
  for, demonstrated without inventing a new schema concept.
- **Mixed trigger types within one API**: the Process API has one HTTP-triggered flow
  and one scheduler-triggered flow side by side.
- **Independent, non-shared implementations across applications**: both System APIs
  have their own separately-defined `log-request-sub-flow` (same name, different
  `id`, different owning Application). This is deliberate, not an oversight — see the
  schema-gap analysis in `Ark_Architecture_and_Plan.md` for why cross-application
  flow sharing isn't modeled yet, and why that's a real constraint rather than a
  missing feature. It's also a deliberate seed for later mutation work: this is
  exactly the shape a "shared library drifts out of sync across apps" mutation would
  target once shared library sourcing is modeled.

## Assumptions made

- All four APIs are at a clean `v1`, and naming is internally consistent. Milestone 1
  is explicitly about exercising the schema, not simulating messiness — naming drift,
  legacy versions, and duplicate/redundant logic are Milestone 4 (mutation engine)
  concerns, not this milestone's.
- `HTTP_Listener_config` is reused by name across all four applications, standing in
  for "the shared HTTP listener connector config a real Mule project would define
  once." This is just a string reference today; no listener-config entity is modeled.
- DataWeave scripts are illustrative placeholders (they describe plausible
  transformations) rather than fully worked-out mapping logic — full mapping fidelity
  isn't needed to exercise the ground-truth schema or the validator.

## Reproducibility

This file is static and hand-authored (not generated), so it is deterministic by
construction — there is no seed to record. `schema_version: 0.2.0` pins it to the
schema version introduced in Milestone 1 (see `ark/core/models.py`).
