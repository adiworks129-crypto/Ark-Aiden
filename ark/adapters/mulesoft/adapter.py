"""
MuleSoftAdapter — Milestone 2's concrete TargetAdapter implementation.

This is the orchestration layer: it walks a ground-truth estate, calls
renderer.py to produce each artifact, and calls manifest.py to build the
traceability structure tying artifacts back to entities. See renderer.py
and manifest.py for the actual rendering/manifest logic; this file just
wires them together per the TargetAdapter interface in ark/adapters/base.py.
"""

from __future__ import annotations

from ark.adapters.base import RenderedEstate, TargetAdapter
from ark.adapters.mulesoft.manifest import build_manifest
from ark.adapters.mulesoft.renderer import index_apis, render_api_yaml, render_application_xml
from ark.core.models import GroundTruthEstate


class MuleSoftAdapter(TargetAdapter):
    name = "mulesoft"

    def render(self, estate: GroundTruthEstate) -> RenderedEstate:
        api_index = index_apis(estate)

        artifacts: dict[str, str] = {}
        artifact_entities: dict[str, list[dict]] = {}

        for app in estate.applications:
            xml_path = f"{app.name}/src/main/mule/{app.name}.xml"
            xml_text, entities = render_application_xml(app, api_index)
            artifacts[xml_path] = xml_text
            artifact_entities[xml_path] = entities

            flow_names_by_id = {flow.id: flow.name for flow in app.flows}
            for api in app.apis:
                yaml_path = f"{app.name}/src/main/resources/{api.id}.yaml"
                entry_flow_name = flow_names_by_id.get(api.entry_flow_id)
                artifacts[yaml_path] = render_api_yaml(api, entry_flow_name)
                artifact_entities[yaml_path] = [
                    {"id": api.id, "type": "API", "name": api.name, "application_id": app.id}
                ]

        manifest = build_manifest(estate, artifact_entities)
        return RenderedEstate(artifacts=artifacts, manifest=manifest)
