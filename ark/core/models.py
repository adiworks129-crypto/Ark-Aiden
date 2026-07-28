"""
Ark ground-truth domain model — API + Flow scope, extended in Milestone 1
with cross-API network calls (ApiCallStep) and scheduled triggers
(SchedulerTrigger).

This module defines the schema for Ark's "ground truth": the single source
of truth describing a synthetic enterprise estate. Every other Ark
component (generator, mutation engine, exporters, evaluator) reads or
writes objects that conform to these models — nothing else in the system
is authoritative. See Ark_Architecture_and_Plan.md, Section 1, for the
full picture this schema is one piece of.

Implementation note: these are plain stdlib `dataclasses`, not Pydantic
models. The original plan (Section 1.5) recommended Pydantic for this
layer, and that is still the intended long-term choice once the project
is running somewhere with normal package-install access — see the
Milestone 0 note in Ark_Architecture_and_Plan.md for why this milestone
specifically used a zero-dependency implementation instead. The JSON
ground-truth *format* is unaffected either way; only the Python code that
parses/validates it would change.

Design notes for readers new to this project:

- Every entity that might later need to be *referenced* from elsewhere
  (a Transform, a step) is given its own `id` now, even though Milestone 0
  has no separate top-level registry for these yet. This means promoting
  them to shared, independently-referenceable entities later (e.g. for the
  shared-schema-drift mutations described in the plan) is an *additive*
  schema change, not a breaking one.
- Steps and triggers are conceptually discriminated unions (tagged by
  `kind` / `type`) so new step/trigger kinds (schedulers, VM listeners,
  connector calls, ...) can be added later without changing existing ones.
  Dataclasses don't enforce this automatically the way Pydantic does, so
  the tag-based dispatch logic lives in validate.py, next to the rest of
  the structural validation.
- This file only defines *shape* (field names and defaults). It performs
  no validation itself — validate.py is solely responsible for turning a
  raw JSON dict into these objects and for checking it makes sense (e.g.
  "does this flow-ref actually point at a flow that exists?").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

# The schema version this file implements. validate.py checks incoming
# ground-truth files against a registry of versions it knows how to
# validate — see SUPPORTED_SCHEMA_VERSIONS there. Bump this using semver:
# patch = doc/clarification only, minor = additive field, major = breaking
# (requires a migration path for existing ground-truth files).
#
# 0.2.0 (Milestone 1): added SchedulerTrigger and ApiCallStep. Both are
# additive new union members — no existing field changed shape or meaning,
# so 0.1.0 ground-truth files remain valid under this version too (see
# SUPPORTED_SCHEMA_VERSIONS in validate.py).
#
# 0.3.0 (Feature 2 — domain-conditioned component injection / "organized
# randomness"): added ConnectorStep (a new, additive Step union member) and
# GroundTruthEstate.domain (a new, optional field defaulting to None). Same
# additive discipline as 0.2.0 — no existing field changed shape or
# meaning, so 0.1.0/0.2.0 ground-truth files (which simply have no
# "domain" key and no "connector" steps) remain valid under this version
# too.
SCHEMA_VERSION = "0.3.0"


# ---------------------------------------------------------------------------
# Triggers — how a Flow is invoked.
# ---------------------------------------------------------------------------

@dataclass
class HttpListenerTrigger:
    path: str
    """URL path template, e.g. '/orders/{orderId}/status'."""
    method: str
    """HTTP method this listener responds to, e.g. 'GET'."""
    listener_config_ref: str
    """Name of the shared HTTP listener connector config this trigger uses."""
    type: Literal["http-listener"] = "http-listener"


@dataclass
class SchedulerTrigger:
    """Added in Milestone 1: a cron-style trigger, for flows with no HTTP
    entry point at all (e.g. nightly batch/reconciliation jobs). Concretely
    required by the Milestone 1 example estate's reconciliation flow — see
    the Milestone 1 note in Ark_Architecture_and_Plan.md for why this
    wasn't spec'd in Milestone 0."""

    cron_expression: str
    """Cron-style schedule, e.g. '0 2 * * *' for nightly at 02:00."""
    description: str
    """Plain-language summary of what this scheduled job does and why."""
    type: Literal["scheduler"] = "scheduler"


# Union of trigger kinds. validate.py's discriminator dispatch is written so
# adding a future kind (e.g. a VM/queue listener, once queues are modeled)
# just means adding a branch there and a member here. A VM-listener trigger
# is deliberately NOT added yet — nothing in the current example estate
# needs one, and adding it now would be a speculative abstraction.
Trigger = Union[HttpListenerTrigger, SchedulerTrigger]


# ---------------------------------------------------------------------------
# Steps — the ordered processing units inside a Flow's body.
# ---------------------------------------------------------------------------

@dataclass
class TransformStep:
    id: str
    """Stable, unique ID for this transform step."""
    name: str
    """Human-readable step name (Mule 'doc:name')."""
    description: str
    """Plain-language summary of what this transform does. Required (not
    optional) so the ground truth stays human-legible even before the
    doc-decay mutation operator (Milestone 4) starts removing documentation."""
    dataweave: str
    """The DataWeave script this transform executes."""
    kind: Literal["transform"] = "transform"


@dataclass
class FlowRefStep:
    id: str
    """Stable, unique ID for this flow-ref step."""
    target_flow_id: str
    """ID of the Flow this step invokes. Must resolve to an existing Flow in
    the same estate — checked by validate.py, not by this dataclass alone."""
    kind: Literal["flow-ref"] = "flow-ref"


@dataclass
class LoggerStep:
    id: str
    """Stable, unique ID for this logger step."""
    message: str
    """The log message text."""
    level: Literal["TRACE", "DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    kind: Literal["logger"] = "logger"


@dataclass
class ApiCallStep:
    """Added in Milestone 1. Represents a *network* call from this flow to
    another API's entry point — the real-world mechanism by which one
    application depends on another (e.g. a Process API calling a System
    API). This is deliberately a different thing from FlowRefStep:

    - FlowRefStep is an in-process reference and only resolves within the
      same Application (real MuleSoft constraint — you cannot flow-ref
      into another deployable artifact).
    - ApiCallStep is a network call and resolves against the *whole*
      estate's API registry (also a real constraint — network calls are
      exactly how cross-application/cross-team reuse actually happens in
      practice, since flow-ref can't reach across process boundaries).

    Only the target API's id is recorded, not a specific flow — a caller
    depends on the target's published contract, not its internal
    implementation, which mirrors how real API-led integration works.
    """

    id: str
    """Stable, unique ID for this step."""
    name: str
    """Human-readable step name (Mule 'doc:name')."""
    description: str
    """Plain-language summary of what this call does and why."""
    target_api_id: str
    """ID of the API (not Flow) this step calls. Must resolve to an
    existing API anywhere in the estate — checked by validate.py."""
    kind: Literal["api-call"] = "api-call"


@dataclass
class ConnectorStep:
    """A step representing a call to an external enterprise connector or
    system (e.g. an ERP module, a core-banking platform, a payment rail) —
    distinct from ApiCallStep, which targets another Ark-modeled API
    *within this same synthetic estate* (referentially checked by
    validate.py), and from TransformStep/FlowRefStep/LoggerStep, none of
    which represent a dependency on an external system at all.

    Introduced for Feature 2 (domain-conditioned component injection, aka
    "organized randomness" — see ark/mutation/operators.py's
    DomainComponentInjectionOperator and
    ark/generator/domain_plausibility.json): this is the step kind that
    operator adds when it injects a component that is realistic on its
    own terms but implausible for the estate's assigned `domain` (see
    GroundTruthEstate.domain below). Nothing about this dataclass itself
    is mutation-specific, though — like every other Step kind, it's a
    plain, general model concept that simply happens to currently only be
    produced by one particular operator.
    """

    id: str
    """Stable, unique id for this step."""
    name: str
    """Human-readable step name (Mule 'doc:name'-equivalent)."""
    description: str
    """Plain-language summary of what this integration does and why —
    same field discipline as TransformStep/ApiCallStep (required, not
    optional, so the ground truth stays human-legible)."""
    connector_type: str
    """A key into ark/generator/domain_plausibility.json's component
    catalog (e.g. "sap_retail_scm", "core_banking_platform") — NOT free
    text, so the plausibility mapping and this step always agree on
    vocabulary. Deliberately not cross-checked against that catalog by
    validate.py (only checked for being a non-empty string) — doing so
    would require ark.core to import ark.generator, inverting the
    established one-way "generator depends on core, never the reverse"
    dependency direction; flagged here rather than silently done anyway."""
    kind: Literal["connector"] = "connector"


Step = Union[TransformStep, FlowRefStep, LoggerStep, ApiCallStep, ConnectorStep]


# ---------------------------------------------------------------------------
# Flow — a top-level Flow (has a trigger) or a Sub-flow (invoked only via
# flow-ref, no trigger of its own).
# ---------------------------------------------------------------------------

@dataclass
class Flow:
    id: str
    """Stable, unique ID for this flow, referenced by FlowRefStep.target_flow_id
    and API.entry_flow_id."""
    name: str
    """Flow name as it would appear in Mule XML (name="...")."""
    flow_type: Literal["flow", "sub_flow"] = "flow"
    """'flow' has a trigger and can be an API entry point; 'sub_flow' has no
    trigger and is only reachable via a FlowRefStep."""
    trigger: Trigger | None = None
    """Required for flow_type='flow', must be None for flow_type='sub_flow'."""
    steps: list[Step] = field(default_factory=list)
    """Ordered processing steps."""


# ---------------------------------------------------------------------------
# API — the contract-level entity bound to one entry Flow.
# ---------------------------------------------------------------------------

@dataclass
class API:
    id: str
    """Stable, unique ID for this API."""
    name: str
    """API display name, e.g. 'Order Status API'."""
    version: str
    """API version label, e.g. 'v1'. Deliberately a free-form string, not an
    integer — real estates use inconsistent version labels (v1, 1.0,
    V1-legacy) and Ark's naming-drift mutations need to be able to produce
    that."""
    entry_flow_id: str
    """ID of the Flow that implements this API's entry point."""


# ---------------------------------------------------------------------------
# Application — deployable unit containing APIs and Flows.
# ---------------------------------------------------------------------------

@dataclass
class Application:
    id: str
    """Stable, unique ID for this application."""
    name: str
    """Application/project name."""
    apis: list[API] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# GroundTruthEstate — the top-level container. This is the object every
# other Ark component (generator, mutation engine, exporters, evaluator)
# treats as the single source of truth.
# ---------------------------------------------------------------------------

@dataclass
class GroundTruthEstate:
    estate_id: str
    """Stable, unique ID for this estate."""
    schema_version: str = SCHEMA_VERSION
    """Semver of this ground-truth schema. validate.py rejects estates whose
    version has no registered validator — never silently 'latest wins'."""
    applications: list[Application] = field(default_factory=list)
    domain: Literal["finance", "retail"] | None = None
    """Added for Feature 2 (domain-conditioned component injection). The
    real-world business domain this estate represents — deliberately
    optional, defaulting to None: every hand-authored ground-truth file
    from before this field existed (Milestone 0/1's examples) simply has
    no "domain" key and remains fully valid, and a generated estate only
    gets one if its GeneratorConfig.domain was explicitly set. Only two
    values are supported for now (see validate.py's `_VALID_DOMAINS` and
    generator/config.py's `SUPPORTED_DOMAINS`) — deliberately not
    generalized to an open-ended set of domains until a second real
    feature actually needs a third one.

    This field exists so ark.mutation.operators.DomainComponentInjectionOperator
    has something to check plausibility *against*: an estate with
    domain=None has no defined "home" domain, so that operator finds no
    candidates for it at all (nothing to be implausible relative to) — see
    that operator's own find_candidates() docstring."""
