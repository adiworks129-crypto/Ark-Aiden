"""
Rendering manifest builder for the MuleSoft adapter — Milestone 2.

The manifest is the single authoritative mapping from exported artifact
files back to ground-truth entities, and from entities to the other
entities they depend on. This is what lets a future evaluation pipeline
determine (per the plan's "evaluation readiness" requirement):

- which ground-truth entity produced each artifact  -> "artifacts" + "entity_index"
- which components exist in the estate               -> "entity_index"
- which relationships exist between components        -> "dependencies"

Ark deliberately does not embed entity ids as comments inside the
rendered XML/YAML itself (see renderer.py's docstring) — this manifest is
the only place that mapping lives, so it can't drift from a second copy.
"""

from __future__ import annotations

from ark.core.models import ApiCallStep, FlowRefStep, GroundTruthEstate

ADAPTER_NAME = "mulesoft"
ADAPTER_VERSION = "0.1.0"


def build_manifest(estate: GroundTruthEstate, artifact_entities: dict[str, list[dict]]) -> dict:
    """Build the manifest dict for a rendered estate.

    artifact_entities: relative artifact path -> list of entity dicts
        rendered into that file (as produced by renderer.py).
    """
    entity_index: dict[str, dict] = {}
    for path, entities in artifact_entities.items():
        for entity in entities:
            entity_index[entity["id"]] = {
                "artifact_path": path,
                "entity_type": entity["type"],
                # Added in Milestone 6.2: the rendered-visible label (and
                # any aliases) an evaluator resolves an agent's
                # entity_reference against -- see renderer.py's
                # _step_entity() for the per-step-kind label rules this
                # carries forward. A convenience projection of the same
                # `entities` data already listed under "artifacts" below,
                # not a second source of truth.
                "name": entity.get("name"),
                "aliases": entity.get("aliases", []),
            }

    dependencies: list[dict] = []
    for app in estate.applications:
        for flow in app.flows:
            for step in flow.steps:
                if isinstance(step, FlowRefStep):
                    dependencies.append(
                        {
                            "kind": "flow-ref",
                            "source_entity_id": flow.id,
                            "source_step_id": step.id,
                            "target_entity_id": step.target_flow_id,
                            "target_entity_type": "Flow",
                        }
                    )
                elif isinstance(step, ApiCallStep):
                    dependencies.append(
                        {
                            "kind": "api-call",
                            "source_entity_id": flow.id,
                            "source_step_id": step.id,
                            "target_entity_id": step.target_api_id,
                            "target_entity_type": "API",
                        }
                    )

    return {
        "estate_id": estate.estate_id,
        "schema_version": estate.schema_version,
        "adapter": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "artifacts": [
            {"path": path, "entities": entities} for path, entities in sorted(artifact_entities.items())
        ],
        "entity_index": entity_index,
        "dependencies": dependencies,
    }
