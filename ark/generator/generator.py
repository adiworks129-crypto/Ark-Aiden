"""
Ark's estate generator — Milestone 3 top-level entry point.

generate_estate(config) turns a GeneratorConfig into a GroundTruthEstate by:
1. Asking topology.py for an abstract dependency graph (which apps exist,
   by layer + business noun, and which depend on which).
2. Constructing the actual ark.core.models objects (Application, API,
   Flow, steps) from that plan using vocabulary.py's naming templates.

The generator never bypasses core validation or modifies ark/core/models.py
or validate.py: every estate it produces is expected to pass
validate_ground_truth() once serialized (see ark/core/serialize.py) —
Milestone 3's tests round-trip every generated estate through the real
validator, the same path a hand-authored file goes through.

Determinism: every random decision flows through the single
random.Random(seed) instance created here and threaded explicitly through
topology.py — never Python's global `random` module state (see seeds.py).

This is a *clean baseline* generator on purpose. It never introduces
naming drift, legacy versions, duplicate-and-diverged logic, or missing
documentation — that's Milestone 4's mutation engine's job, operating on
top of what this module produces. See GenerationManifest below for how
this milestone's output is meant to be the fixed "v0" starting point a
Milestone 4 transformation trajectory records itself as building on.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ark.core.models import (
    API,
    ApiCallStep,
    Application,
    Flow,
    FlowRefStep,
    GroundTruthEstate,
    HttpListenerTrigger,
    LoggerStep,
    SchedulerTrigger,
    TransformStep,
)
from ark.generator import vocabulary as vocab
from ark.generator.config import GeneratorConfig
from ark.generator.seeds import make_rng
from ark.generator.topology import LAYER_EXPERIENCE, LAYER_PROCESS, AppSpec, build_topology

# Bump using the same semver discipline as ark.core.models.SCHEMA_VERSION:
# patch = no behavior change, minor = generation behavior changed in a way
# that could change output for existing seeds/configs, major = config
# shape itself changed incompatibly. This is recorded in every
# GenerationManifest specifically so a generated estate can always be
# traced back to *which version of these rules* produced it.
GENERATOR_VERSION = "0.1.0"


@dataclass
class GenerationManifest:
    """Everything needed to reproduce a generated estate exactly: the
    seed, this generator's version, the core schema version it targeted,
    and the exact config used.

    This is the "baseline recipe" a Milestone 4 mutation trajectory will
    be recorded as starting from: base = (this manifest, this estate);
    a trajectory = an ordered sequence of Transformation Records applied
    on top (see Ark_Architecture_and_Plan.md Section 1.7). Milestone 3
    does not implement any mutation itself — this manifest is only the
    anchor point Milestone 4 will attach to.
    """

    seed: int
    generator_version: str
    schema_version: str
    config: dict


@dataclass
class GeneratedEstate:
    estate: GroundTruthEstate
    manifest: GenerationManifest


def generate_estate(config: GeneratorConfig) -> GeneratedEstate:
    rng = make_rng(config.seed)
    plan = build_topology(config, rng)

    apps_by_key = {spec.key: spec for spec in plan.apps}
    downstream_by_key: dict[str, list[AppSpec]] = {}
    for source_key, target_key in plan.edges:
        downstream_by_key.setdefault(source_key, []).append(apps_by_key[target_key])

    applications = [
        _build_application(spec, downstream_by_key.get(spec.key, []))
        for spec in plan.apps
    ]

    estate = GroundTruthEstate(
        estate_id=f"{config.estate_id_prefix}-seed{config.seed}",
        applications=applications,
        domain=config.domain,
    )

    manifest = GenerationManifest(
        seed=config.seed,
        generator_version=GENERATOR_VERSION,
        schema_version=estate.schema_version,
        config=dataclasses.asdict(config),
    )
    return GeneratedEstate(estate=estate, manifest=manifest)


def _build_application(spec: AppSpec, downstream_specs: list[AppSpec]) -> Application:
    entry_flow = _build_entry_flow(spec, downstream_specs)
    flows: list[Flow] = [entry_flow]

    primary_subflow = _build_subflow(spec, variant="primary")
    flows.append(primary_subflow)

    if spec.has_secondary_flow:
        if spec.shares_subflow_across_flows:
            secondary_subflow_id = primary_subflow.id
        else:
            secondary_subflow = _build_subflow(spec, variant="secondary")
            flows.append(secondary_subflow)
            secondary_subflow_id = secondary_subflow.id

        flows.append(_build_secondary_flow(spec, downstream_specs, secondary_subflow_id))

    api = API(
        id=vocab.api_id(spec.layer, spec.noun),
        name=vocab.api_name(spec.layer, spec.noun),
        version="v1",
        entry_flow_id=entry_flow.id,
    )

    return Application(
        id=vocab.app_id(spec.layer, spec.noun),
        name=vocab.app_name(spec.layer, spec.noun),
        apis=[api],
        flows=flows,
    )


def _build_entry_flow(spec: AppSpec, downstream_specs: list[AppSpec]) -> Flow:
    primary_subflow_id = vocab.subflow_id(spec.layer, spec.noun, variant="primary")

    trigger = HttpListenerTrigger(
        path=vocab.entry_path(spec.layer, spec.noun),
        method=vocab.entry_method(spec.layer),
        listener_config_ref="HTTP_Listener_config",
    )

    api_call_steps = [_build_api_call_step(spec, d, purpose_prefix="call") for d in downstream_specs]

    is_process = spec.layer == LAYER_PROCESS
    transform_step = TransformStep(
        id=vocab.step_id(spec.layer, spec.noun, "build-result" if is_process else "build-response"),
        name=f"Build {spec.noun.title()} {'Result' if is_process else 'Response'}",
        description=(
            f"Builds the processing result payload for {spec.noun}."
            if is_process
            else f"Builds the response payload for {spec.noun}."
        ),
        dataweave=vocab.build_dataweave(spec.layer, spec.noun),
    )

    subflow_ref_step = FlowRefStep(
        id=vocab.step_id(spec.layer, spec.noun, "subflow-ref"),
        target_flow_id=primary_subflow_id,
    )

    if spec.layer == LAYER_EXPERIENCE:
        steps = [*api_call_steps, transform_step, subflow_ref_step]
    elif spec.layer == LAYER_PROCESS:
        steps = [subflow_ref_step, *api_call_steps, transform_step]
    else:  # system
        steps = [transform_step, subflow_ref_step]

    return Flow(
        id=vocab.entry_flow_id(spec.layer, spec.noun),
        name=vocab.entry_flow_name(spec.layer, spec.noun),
        flow_type="flow",
        trigger=trigger,
        steps=steps,
    )


def _build_subflow(spec: AppSpec, variant: str) -> Flow:
    if spec.layer == LAYER_PROCESS:
        step = TransformStep(
            id=vocab.step_id(spec.layer, spec.noun, f"{variant}-validate"),
            name=f"Validate {spec.noun.title()} Payload",
            description=f"Checks the incoming {spec.noun} payload has all required fields.",
            dataweave="%dw 2.0\noutput application/json\n---\npayload",
        )
    else:
        step = LoggerStep(
            id=vocab.step_id(spec.layer, spec.noun, f"{variant}-log"),
            level="INFO",
            message=f"{spec.noun.title()} {spec.layer} request received",
        )

    return Flow(
        id=vocab.subflow_id(spec.layer, spec.noun, variant=variant),
        name=vocab.subflow_name(spec.layer, spec.noun, variant=variant),
        flow_type="sub_flow",
        trigger=None,
        steps=[step],
    )


def _build_secondary_flow(spec: AppSpec, downstream_specs: list[AppSpec], subflow_target_id: str) -> Flow:
    trigger = SchedulerTrigger(
        cron_expression="0 2 * * *",
        description=f"Runs nightly at 02:00 to reconcile {spec.noun} data.",
    )

    subflow_ref_step = FlowRefStep(
        id=vocab.step_id(spec.layer, spec.noun, "scheduled-subflow-ref"),
        target_flow_id=subflow_target_id,
    )
    api_call_steps = [
        _build_api_call_step(spec, d, purpose_prefix="scheduled-call") for d in downstream_specs
    ]
    logger_step = LoggerStep(
        id=vocab.step_id(spec.layer, spec.noun, "scheduled-log"),
        level="INFO",
        message=f"Nightly {spec.noun} reconciliation completed",
    )

    return Flow(
        id=vocab.secondary_flow_id(spec.layer, spec.noun),
        name=vocab.secondary_flow_name(spec.layer, spec.noun),
        flow_type="flow",
        trigger=trigger,
        steps=[subflow_ref_step, *api_call_steps, logger_step],
    )


def _build_api_call_step(spec: AppSpec, target: AppSpec, purpose_prefix: str) -> ApiCallStep:
    target_api_name = vocab.api_name(target.layer, target.noun)
    return ApiCallStep(
        id=vocab.step_id(spec.layer, spec.noun, f"{purpose_prefix}-{target.noun}-{target.layer}"),
        name=f"Call {target_api_name}",
        description=f"Calls the {target_api_name} as part of {spec.layer}-layer processing for {spec.noun}.",
        target_api_id=vocab.api_id(target.layer, target.noun),
    )
