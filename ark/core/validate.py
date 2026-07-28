"""
Ground-truth validation for Ark.

This module owns two jobs, deliberately kept distinct:

1. Structural validation: does this JSON dict have the right shape (right
   fields, right types, a recognized 'kind'/'type' tag on each step and
   trigger)? This is what a library like Pydantic would normally do for
   free; here it's hand-written (see the Milestone 0 note in
   Ark_Architecture_and_Plan.md for why) as a set of small `_parse_*`
   functions, one per entity, each collecting errors into a shared list
   rather than raising on the first problem.
2. Referential integrity: given a *structurally* valid estate, do its
   cross-references actually make sense? (Does a flow-ref's target exist?
   Are IDs unique? Does a 'flow' have a trigger and a 'sub_flow' not?)
   Pydantic couldn't check this either, so it was always going to be a
   separate step — see _check_referential_integrity below.

   Milestone 1 note: this step now enforces two *different* resolution
   scopes for two *different* kinds of reference, and that difference is
   intentional, not an inconsistency:
   - FlowRefStep.target_flow_id resolves only within the same Application
     (a real MuleSoft constraint — flow-ref cannot cross a deployable
     artifact boundary).
   - ApiCallStep.target_api_id resolves against the whole estate (also a
     real constraint — cross-application reuse happens over the network,
     not via flow-ref).

validate_ground_truth() is the one function every other Ark component
should call before trusting a ground-truth file. It never fails on the
first error — it collects every problem it finds (structural first, then
referential) and reports them all at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ark.core.models import (
    SCHEMA_VERSION,
    API,
    ApiCallStep,
    Application,
    ConnectorStep,
    Flow,
    FlowRefStep,
    GroundTruthEstate,
    HttpListenerTrigger,
    LoggerStep,
    SchedulerTrigger,
    Step,
    Trigger,
    TransformStep,
)

# Registry of schema versions this validator knows how to check. 0.1.0 is
# Milestone 0's shape; 0.2.0 (Milestone 1) and 0.3.0 (Feature 2) only
# *added* union members/optional fields (SchedulerTrigger, ApiCallStep;
# ConnectorStep, GroundTruthEstate.domain), so 0.1.0/0.2.0 files remain
# valid and all three stay listed here. A future breaking change would
# need a real migration path, not just adding a version string to this set.
SUPPORTED_SCHEMA_VERSIONS = {"0.1.0", "0.2.0", "0.3.0"}

_VALID_FLOW_TYPES = ("flow", "sub_flow")
_VALID_LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR")
_VALID_DOMAINS = ("finance", "retail")
"""Feature 2's supported estate domains. Deliberately a small, local,
self-contained constant here — matching _VALID_FLOW_TYPES/_VALID_LOG_LEVELS
above rather than importing a shared constant from ark.generator, which
would invert the established one-way "generator depends on core, never the
reverse" dependency direction. ark/generator/config.py's own
`SUPPORTED_DOMAINS` and ark/generator/domain_plausibility.json's top-level
domain keys must list exactly the same two values — cross-checked by a
dedicated test, not by a shared import, for that same reason."""


class GroundTruthValidationError(Exception):
    """Raised when a ground-truth file fails structural or referential validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(
            f"Ground truth failed validation with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def validate_estate_object(estate: GroundTruthEstate) -> list[str]:
    """Run only the referential-integrity checks against an already-parsed,
    already-structurally-valid GroundTruthEstate object already in memory.

    Added in Milestone 4 for the mutation engine, which constructs/modifies
    GroundTruthEstate objects directly (never through JSON) and needs a fast
    way to confirm each mutation step kept the estate valid, without a full
    serialize-to-JSON-and-reparse round trip on every step. This performs
    the exact same checks validate_ground_truth() does — it's a thinner
    entry point into the same logic, not a different or weaker check.

    Returns a list of error strings (empty if valid). Does not raise —
    callers decide what "invalid" should mean for them (see
    ark/mutation/engine.py for the mutation engine's use of this).
    """
    return _check_referential_integrity(estate)


def validate_ground_truth(path: str | Path) -> GroundTruthEstate:
    """Load, structurally validate, and referentially validate a ground-truth JSON file.

    Returns the parsed GroundTruthEstate on success. Raises
    GroundTruthValidationError, listing every problem found, on failure.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    version = raw.get("schema_version", SCHEMA_VERSION)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise GroundTruthValidationError(
            [
                f"Unsupported schema_version '{version}'. "
                f"Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}."
            ]
        )

    structural_errors: list[str] = []
    estate = _parse_estate(raw, structural_errors)
    if structural_errors or estate is None:
        raise GroundTruthValidationError(structural_errors)

    referential_errors = _check_referential_integrity(estate)
    if referential_errors:
        raise GroundTruthValidationError(referential_errors)

    return estate


# ---------------------------------------------------------------------------
# Structural parsing — raw JSON dict -> dataclasses, collecting shape errors.
# ---------------------------------------------------------------------------

def _missing_fields(raw: dict, required: tuple[str, ...]) -> list[str]:
    return [k for k in required if k not in raw]


def _parse_trigger(raw: Any, ctx: str, errors: list[str]) -> Trigger | None:
    if not isinstance(raw, dict):
        errors.append(f"{ctx}: trigger must be an object, got {type(raw).__name__}.")
        return None

    ttype = raw.get("type")
    if ttype == "http-listener":
        missing = _missing_fields(raw, ("path", "method", "listener_config_ref"))
        if missing:
            errors.append(f"{ctx}: http-listener trigger missing field(s): {missing}.")
            return None
        return HttpListenerTrigger(
            path=raw["path"], method=raw["method"], listener_config_ref=raw["listener_config_ref"]
        )

    if ttype == "scheduler":
        missing = _missing_fields(raw, ("cron_expression", "description"))
        if missing:
            errors.append(f"{ctx}: scheduler trigger missing field(s): {missing}.")
            return None
        return SchedulerTrigger(cron_expression=raw["cron_expression"], description=raw["description"])

    errors.append(f"{ctx}: unknown trigger type '{ttype}'. Supported: ['http-listener', 'scheduler'].")
    return None


def _parse_step(raw: Any, ctx: str, errors: list[str]) -> Step | None:
    if not isinstance(raw, dict):
        errors.append(f"{ctx}: step must be an object, got {type(raw).__name__}.")
        return None

    kind = raw.get("kind")

    if kind == "transform":
        missing = _missing_fields(raw, ("id", "name", "description", "dataweave"))
        if missing:
            errors.append(f"{ctx}: transform step missing field(s): {missing}.")
            return None
        return TransformStep(
            id=raw["id"], name=raw["name"], description=raw["description"], dataweave=raw["dataweave"]
        )

    if kind == "flow-ref":
        missing = _missing_fields(raw, ("id", "target_flow_id"))
        if missing:
            errors.append(f"{ctx}: flow-ref step missing field(s): {missing}.")
            return None
        return FlowRefStep(id=raw["id"], target_flow_id=raw["target_flow_id"])

    if kind == "logger":
        missing = _missing_fields(raw, ("id", "message"))
        if missing:
            errors.append(f"{ctx}: logger step missing field(s): {missing}.")
            return None
        level = raw.get("level", "INFO")
        if level not in _VALID_LOG_LEVELS:
            errors.append(f"{ctx}: logger step has invalid level '{level}'. Supported: {list(_VALID_LOG_LEVELS)}.")
            return None
        return LoggerStep(id=raw["id"], message=raw["message"], level=level)

    if kind == "api-call":
        missing = _missing_fields(raw, ("id", "name", "description", "target_api_id"))
        if missing:
            errors.append(f"{ctx}: api-call step missing field(s): {missing}.")
            return None
        return ApiCallStep(
            id=raw["id"], name=raw["name"], description=raw["description"], target_api_id=raw["target_api_id"]
        )

    if kind == "connector":
        missing = _missing_fields(raw, ("id", "name", "description", "connector_type"))
        if missing:
            errors.append(f"{ctx}: connector step missing field(s): {missing}.")
            return None
        if not isinstance(raw["connector_type"], str) or not raw["connector_type"].strip():
            errors.append(f"{ctx}: connector step's connector_type must be a non-empty string.")
            return None
        return ConnectorStep(
            id=raw["id"], name=raw["name"], description=raw["description"],
            connector_type=raw["connector_type"],
        )

    errors.append(
        f"{ctx}: unknown step kind '{kind}'. "
        f"Supported: ['transform', 'flow-ref', 'logger', 'api-call', 'connector']."
    )
    return None


def _parse_flow(raw: Any, ctx: str, errors: list[str]) -> Flow | None:
    if not isinstance(raw, dict):
        errors.append(f"{ctx}: flow must be an object, got {type(raw).__name__}.")
        return None

    missing = _missing_fields(raw, ("id", "name"))
    if missing:
        errors.append(f"{ctx}: flow missing field(s): {missing}.")
        return None

    flow_type = raw.get("flow_type", "flow")
    if flow_type not in _VALID_FLOW_TYPES:
        errors.append(f"{ctx}: invalid flow_type '{flow_type}'. Supported: {list(_VALID_FLOW_TYPES)}.")
        return None

    trigger = None
    trigger_raw = raw.get("trigger")
    if trigger_raw is not None:
        trigger = _parse_trigger(trigger_raw, f"{ctx}.trigger", errors)

    steps: list[Step] = []
    for i, step_raw in enumerate(raw.get("steps", [])):
        step = _parse_step(step_raw, f"{ctx}.steps[{i}]", errors)
        if step is not None:
            steps.append(step)

    return Flow(id=raw["id"], name=raw["name"], flow_type=flow_type, trigger=trigger, steps=steps)


def _parse_api(raw: Any, ctx: str, errors: list[str]) -> API | None:
    if not isinstance(raw, dict):
        errors.append(f"{ctx}: API must be an object, got {type(raw).__name__}.")
        return None

    missing = _missing_fields(raw, ("id", "name", "version", "entry_flow_id"))
    if missing:
        errors.append(f"{ctx}: API missing field(s): {missing}.")
        return None

    return API(
        id=raw["id"], name=raw["name"], version=raw["version"], entry_flow_id=raw["entry_flow_id"]
    )


def _parse_application(raw: Any, ctx: str, errors: list[str]) -> Application | None:
    if not isinstance(raw, dict):
        errors.append(f"{ctx}: application must be an object, got {type(raw).__name__}.")
        return None

    missing = _missing_fields(raw, ("id", "name"))
    if missing:
        errors.append(f"{ctx}: application missing field(s): {missing}.")
        return None

    apis: list[API] = []
    for i, api_raw in enumerate(raw.get("apis", [])):
        api = _parse_api(api_raw, f"{ctx}.apis[{i}]", errors)
        if api is not None:
            apis.append(api)

    flows: list[Flow] = []
    for i, flow_raw in enumerate(raw.get("flows", [])):
        flow = _parse_flow(flow_raw, f"{ctx}.flows[{i}]", errors)
        if flow is not None:
            flows.append(flow)

    return Application(id=raw["id"], name=raw["name"], apis=apis, flows=flows)


def _parse_estate(raw: dict, errors: list[str]) -> GroundTruthEstate | None:
    missing = _missing_fields(raw, ("estate_id",))
    if missing:
        errors.append(f"estate: missing field(s): {missing}.")
        return None

    applications: list[Application] = []
    for i, app_raw in enumerate(raw.get("applications", [])):
        app = _parse_application(app_raw, f"applications[{i}]", errors)
        if app is not None:
            applications.append(app)

    domain = raw.get("domain")
    if domain is not None and domain not in _VALID_DOMAINS:
        errors.append(f"estate: invalid domain '{domain}'. Supported: {list(_VALID_DOMAINS)} (or omit entirely).")
        domain = None

    return GroundTruthEstate(
        estate_id=raw["estate_id"],
        schema_version=raw.get("schema_version", SCHEMA_VERSION),
        applications=applications,
        domain=domain,
    )


# ---------------------------------------------------------------------------
# Referential integrity — cross-reference checks structural parsing can't do.
# ---------------------------------------------------------------------------

def _check_referential_integrity(estate: GroundTruthEstate) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()

    def register(entity_id: str, kind: str) -> None:
        if entity_id in seen_ids:
            errors.append(
                f"Duplicate id '{entity_id}' ({kind}) — every id in an estate must be unique."
            )
        seen_ids.add(entity_id)

    # Pre-pass: every API id in the estate, regardless of which Application
    # owns it. ApiCallStep represents a network call, so — unlike
    # FlowRefStep — it's allowed to target an API owned by a *different*
    # Application. This is the estate-wide resolution scope described in
    # the module docstring.
    all_api_ids = {api.id for app in estate.applications for api in app.apis}

    for app in estate.applications:
        register(app.id, "Application")
        flow_ids_in_app = {flow.id for flow in app.flows}

        for flow in app.flows:
            register(flow.id, "Flow")

            if flow.flow_type == "flow" and flow.trigger is None:
                errors.append(f"Flow '{flow.id}' has flow_type='flow' but no trigger.")
            if flow.flow_type == "sub_flow" and flow.trigger is not None:
                errors.append(f"Flow '{flow.id}' has flow_type='sub_flow' but has a trigger.")

            for step in flow.steps:
                register(step.id, f"Step({step.kind})")
                if isinstance(step, FlowRefStep) and step.target_flow_id not in flow_ids_in_app:
                    errors.append(
                        f"FlowRefStep '{step.id}' in Flow '{flow.id}' targets unknown "
                        f"Flow id '{step.target_flow_id}' (not found in Application '{app.id}'; "
                        f"flow-ref only resolves within the same Application)."
                    )
                if isinstance(step, ApiCallStep) and step.target_api_id not in all_api_ids:
                    errors.append(
                        f"ApiCallStep '{step.id}' in Flow '{flow.id}' targets unknown "
                        f"API id '{step.target_api_id}' (not found anywhere in the estate)."
                    )

        for api in app.apis:
            register(api.id, "API")
            if api.entry_flow_id not in flow_ids_in_app:
                errors.append(
                    f"API '{api.id}' entry_flow_id '{api.entry_flow_id}' does not match "
                    f"any Flow in Application '{app.id}'."
                )

    return errors
